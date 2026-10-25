#!/usr/bin/env bash
# 跑 fir_stream_shell（DMA 版外壳，AXI-Stream 接口）的 RTL 仿真。
#
#   bash sim/rtl_sim/run_xsim_stream.sh                        # 4800 点，仅前端
#   DRC=1 bash sim/rtl_sim/run_xsim_stream.sh                  # 4800 点，走 DRC
#   bash sim/rtl_sim/run_xsim_stream.sh <golden_dir>           # 指定黄金对目录
#
# 黄金对目录里要有 in_q15.mem 和 rtl_fe_out.txt（run_xsim_long.sh 产出的那套）。
# 默认：DRC=0 → golden_n4800/，DRC=1 → golden_n4800_drc_loud/。
#
# 验的是流式接口本身：主机按 valid/ready 送数、从机随机背压，
# 输出必须和块模式同一批输入的黄金值**逐位相同**，且背压不改变结果。

set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"

DRC="${DRC:-0}"
if [ "$DRC" = "1" ]; then
    GOLD="${1:-$SIM/golden_n4800_drc_loud}"
    SIM_ARG="-testplusarg DRC"
    WORK="$SIM/xsim_work_stream_drc"
else
    GOLD="${1:-$SIM/golden_n4800}"
    SIM_ARG=""
    WORK="$SIM/xsim_work_stream"
fi

XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
XE="${XELAB_BIN:-E:/Xilinx/Vivado/2020.2/bin/xelab.bat}"
XS="${XSIM_BIN:-E:/Xilinx/Vivado/2020.2/bin/xsim.bat}"

for f in "$XV" "$XE" "$XS"; do
    [ -f "$f" ] || { echo "找不到 $f"; exit 1; }
done
[ -f "$GOLD/in_q15.mem" ] || { echo "缺 $GOLD/in_q15.mem"; exit 1; }
[ -f "$GOLD/rtl_fe_out.txt" ] || { echo "缺 $GOLD/rtl_fe_out.txt"; exit 1; }
for E in 500 1000 2000; do
    [ -f "$ROOT/src/rtl/coeffs_lp${E}_n193.mem" ] || {
        echo "缺 coeffs_lp${E}_n193.mem"; exit 1; }
done

rm -rf "$WORK"
mkdir -p "$WORK"
cd "$WORK" || exit 1

cp "$GOLD/in_q15.mem"     in_q15.mem
cp "$GOLD/rtl_fe_out.txt" rtl_fe_out.txt
cp "$ROOT/src/rtl/coeffs_lp500_n193.mem"  coeffs_lp500.mem
cp "$ROOT/src/rtl/coeffs_lp1000_n193.mem" coeffs_lp1000.mem
cp "$ROOT/src/rtl/coeffs_lp2000_n193.mem" coeffs_lp2000.mem

echo "### 编译"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
    -i "$(cygpath -w "$SIM")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_mb_fe.v")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_drc4.v")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_stream_shell.v")" \
    "$(cygpath -w "$SIM/tb_fir_stream_shell.v")" \
    > xvlog.log 2>&1
grep -q "analyzing module" xvlog.log || {
    echo "xvlog 没跑起来，见 $WORK/xvlog.log"; tail -20 xvlog.log; exit 1; }
if grep -iE "^ERROR" xvlog.log; then
    echo "xvlog 有错，见 $WORK/xvlog.log"; exit 1
fi

echo "### 精化"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" \
    tb_fir_stream_shell -s tb_sim_stream --debug typical > xelab.log 2>&1
if grep -iE "^ERROR" xelab.log; then
    echo "xelab 有错，见 $WORK/xelab.log"; tail -20 xelab.log; exit 1
fi
[ -d xsim.dir ] || {
    echo "xelab 没产出 xsim.dir"; tail -30 xelab.log; exit 1; }

echo "### 仿真"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" tb_sim_stream -R $SIM_ARG \
    > xsim_stream.log 2>&1
grep -E "\[tb\]" xsim_stream.log | sed 's/^/    /'
grep -q "\[tb\] STREAM PASS" xsim_stream.log || {
    echo "流式外壳仿真没过，见 $WORK/xsim_stream.log"
    tail -40 xsim_stream.log
    exit 1; }

echo
echo "### 和黄金向量再逐位比一遍（$GOLD）"
# 注意：这里给的是 Git Bash 的 /d/... 路径时，native Windows python 解析不了，
# 必须先 cygpath -w
python - "$(cygpath -w "$WORK/stream_out.txt")" "$(cygpath -w "$GOLD/rtl_fe_out.txt")" <<'PY'
import sys
a = [int(x) for x in open(sys.argv[1]) if x.strip()]
b = [int(x) for x in open(sys.argv[2]) if x.strip()]
if len(a) != len(b):
    print(f"长度不一致 {len(a)} vs {len(b)}"); sys.exit(1)
bad = [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]
print(f"  比了 {len(a)} 点，不同 {len(bad)} 点")
for i, x, y in bad[:5]:
    print(f"  [{i}] stream={x} golden={y}")
sys.exit(1 if bad else 0)
PY
RC=$?

echo
if [ $RC -eq 0 ]; then echo "### 流式外壳 + 前端 逐位一致"; else echo "### 输出和黄金向量对不上"; fi
exit $RC
