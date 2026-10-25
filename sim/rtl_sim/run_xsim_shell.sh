#!/usr/bin/env bash
# 跑 fir_rtl_shell（给 fir_mb_fe 套的块模式外壳）的 RTL 仿真。
#
#   bash sim/rtl_sim/run_xsim_shell.sh
#
# 验的是"上板之前能验的全部"：LOAD/RUN/DRAIN 状态机、字节地址换算、
# 3 拍/采样、块间复位（连跑两块必须逐位相同），最后把输出和
# rtl_fe_out.txt（同一批输入的黄金向量）逐位比。

set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"
WORK="$SIM/xsim_work_shell"

XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
XE="${XELAB_BIN:-E:/Xilinx/Vivado/2020.2/bin/xelab.bat}"
XS="${XSIM_BIN:-E:/Xilinx/Vivado/2020.2/bin/xsim.bat}"

for f in "$XV" "$XE" "$XS"; do
    [ -f "$f" ] || { echo "找不到 $f"; exit 1; }
done
[ -f "$SIM/in_q15.mem" ] || { echo "缺 $SIM/in_q15.mem"; exit 1; }
for E in 500 1000 2000; do
    [ -f "$ROOT/src/rtl/coeffs_lp${E}_n193.mem" ] || {
        echo "缺 coeffs_lp${E}_n193.mem"; exit 1; }
done

rm -rf "$WORK"
mkdir -p "$WORK"
cd "$WORK" || exit 1

cp "$SIM/in_q15.mem" in_q15.mem
cp "$ROOT/src/rtl/coeffs_lp500_n193.mem"  coeffs_lp500.mem
cp "$ROOT/src/rtl/coeffs_lp1000_n193.mem" coeffs_lp1000.mem
cp "$ROOT/src/rtl/coeffs_lp2000_n193.mem" coeffs_lp2000.mem

echo "### 编译"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
    -i "$(cygpath -w "$SIM")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_mb_fe.v")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_drc4.v")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_rtl_shell.v")" \
    "$(cygpath -w "$SIM/tb_fir_rtl_shell.v")" \
    > xvlog.log 2>&1
grep -q "analyzing module" xvlog.log || {
    echo "xvlog 没跑起来，见 $WORK/xvlog.log"; tail -20 xvlog.log; exit 1; }
if grep -iE "^ERROR" xvlog.log; then
    echo "xvlog 有错，见 $WORK/xvlog.log"; exit 1
fi

echo "### 精化"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" \
    tb_fir_rtl_shell -s tb_sim_shell --debug typical > xelab.log 2>&1
if grep -iE "^ERROR" xelab.log; then
    echo "xelab 有错，见 $WORK/xelab.log"; tail -20 xelab.log; exit 1
fi
[ -d xsim.dir ] || {
    echo "xelab 没产出 xsim.dir"; tail -30 xelab.log; exit 1; }

echo "### 仿真"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" tb_sim_shell -R > xsim_shell.log 2>&1
grep -E "\[tb\]" xsim_shell.log | sed 's/^/    /'
grep -q "\[tb\] SHELL PASS" xsim_shell.log || {
    echo "外壳仿真没过，见 $WORK/xsim_shell.log"
    tail -40 xsim_shell.log
    exit 1; }

echo
echo "### 和黄金向量逐位比（rtl_fe_out.txt = 同一批输入的 xsim 结果）"
cd "$ROOT" || exit 1
python - "$WORK/shell_out.txt" "$SIM/rtl_fe_out.txt" <<'PY'
import sys
a = [int(x) for x in open(sys.argv[1]) if x.strip()]
b = [int(x) for x in open(sys.argv[2]) if x.strip()]
if len(a) != len(b):
    print(f"长度不一致 {len(a)} vs {len(b)}"); sys.exit(1)
bad = [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]
print(f"比了 {len(a)} 点，不同 {len(bad)} 点")
for i, x, y in bad[:5]:
    print(f"  [{i}] shell={x} golden={y}")
sys.exit(1 if bad else 0)
PY
RC=$?

echo
if [ $RC -eq 0 ]; then echo "### 外壳 + 前端 逐位一致"; else echo "### 输出和黄金向量对不上"; fi
exit $RC
