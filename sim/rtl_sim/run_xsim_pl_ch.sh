#!/usr/bin/env bash
# 验「每帧只喂一个采样 / 每帧吐两个字」这条比例。
#
#   bash sim/rtl_sim/run_xsim_pl_ch.sh
#
# 判据写在 tb_pl_channel.v 的文件头。这条修的是 2026-10-27 读码发现的硬问题：
# i2s_duplex 每帧送两个字，旧代码把两个字都当采样喂前端 ⇒ 等效 97.7 kHz、
# 四个分频点全部翻倍。

set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"
WORK="$SIM/xsim_work_pl_ch"

XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
XE="${XELAB_BIN:-E:/Xilinx/Vivado/2020.2/bin/xelab.bat}"
XS="${XSIM_BIN:-E:/Xilinx/Vivado/2020.2/bin/xsim.bat}"

for f in "$XV" "$XE" "$XS"; do
    [ -f "$f" ] || { echo "找不到 $f"; exit 1; }
done

for e in 500 1000 2000; do
    [ -f "$ROOT/src/rtl/coeffs_lp${e}_n193.mem" ] || {
        echo "缺 coeffs_lp${e}_n193.mem"; exit 1; }
done

rm -rf "$WORK"
mkdir -p "$WORK"
cd "$WORK" || exit 1

cp "$ROOT/src/rtl/coeffs_lp500_n193.mem"  coeffs_lp500.mem
cp "$ROOT/src/rtl/coeffs_lp1000_n193.mem" coeffs_lp1000.mem
cp "$ROOT/src/rtl/coeffs_lp2000_n193.mem" coeffs_lp2000.mem

echo "### 编译"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
    "$(cygpath -w "$ROOT/src/rtl/axis_fifo.v")" \
    "$(cygpath -w "$ROOT/src/rtl/i2s_duplex.v")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_mb_fe.v")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_drc4.v")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_gain_ramp.v")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_pl_direct.v")" \
    "$(cygpath -w "$SIM/tb_pl_channel.v")" \
    > xvlog.log 2>&1
grep -q "analyzing module" xvlog.log || {
    echo "xvlog 没跑起来，见 $WORK/xvlog.log"; tail -20 xvlog.log; exit 1; }
if grep -iE "^ERROR" xvlog.log; then
    echo "xvlog 有错，见 $WORK/xvlog.log"; exit 1
fi

echo "### 精化"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" \
    tb_pl_channel -s tb_sim_pl_ch --debug typical > xelab.log 2>&1
if grep -iE "^ERROR" xelab.log; then
    echo "xelab 有错，见 $WORK/xelab.log"; tail -20 xelab.log; exit 1
fi

echo "### 仿真"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" tb_sim_pl_ch -R > xsim_pl_ch.log 2>&1
grep -E "^===|^  \+|^         |^PASS|^FAIL|^\[done\]" xsim_pl_ch.log | sed 's/^/    /'

if grep -q "^PASS" xsim_pl_ch.log; then
    echo
    echo "### 结论：比例对（每帧 1 个采样进、2 个字出）"
    exit 0
fi
echo
echo "### 结论：不通过，全文见 $WORK/xsim_pl_ch.log"
tail -40 xsim_pl_ch.log
exit 1
