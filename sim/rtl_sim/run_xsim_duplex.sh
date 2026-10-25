#!/usr/bin/env bash
# i2s_duplex（自写 I2S 收发 + 两个轴 FIFO）的单元仿真。
#
#   bash sim/rtl_sim/run_xsim_duplex.sh
#
# 激励在 tb_i2s_duplex.v 里：往录音 FIFO 灌 20 帧，收侧把 sdata_o 解回来，
# 判据是逐字相同、TLAST 落在每帧第二个字、左右声道各自解对、ovf/und 一直是 0。
#
# 这个模块是 fir_dma / fir_dma_core / fir_pl / i2sdbg 四颗位流的共同底座：
# 它坏了上面全部不通，所以改它之后先跑这一条再上板。

set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"
WORK="$SIM/xsim_work_duplex"

XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
XE="${XELAB_BIN:-E:/Xilinx/Vivado/2020.2/bin/xelab.bat}"
XS="${XSIM_BIN:-E:/Xilinx/Vivado/2020.2/bin/xsim.bat}"

for f in "$XV" "$XE" "$XS"; do
    [ -f "$f" ] || { echo "找不到 $f"; exit 1; }
done

rm -rf "$WORK"
mkdir -p "$WORK"
cd "$WORK" || exit 1

echo "### 编译"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
    "$(cygpath -w "$ROOT/src/rtl/axis_fifo.v")" \
    "$(cygpath -w "$ROOT/src/rtl/i2s_duplex.v")" \
    "$(cygpath -w "$SIM/tb_i2s_duplex.v")" \
    > xvlog.log 2>&1
grep -q "analyzing module" xvlog.log || {
    echo "xvlog 没跑起来，见 $WORK/xvlog.log"; tail -20 xvlog.log; exit 1; }
if grep -iE "^ERROR" xvlog.log; then
    echo "xvlog 有错，见 $WORK/xvlog.log"; exit 1
fi

echo "### 精化"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" \
    tb_i2s_duplex -s tb_sim_duplex --debug typical > xelab.log 2>&1
if grep -iE "^ERROR" xelab.log; then
    echo "xelab 有错，见 $WORK/xelab.log"; tail -20 xelab.log; exit 1
fi

echo "### 仿真"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" tb_sim_duplex -R > xsim_duplex.log 2>&1
grep -E "^(RESULT|captured|tlast|channels|  L |  R |stat0)" xsim_duplex.log | sed 's/^/    /'

grep -q "RESULT: PASS" xsim_duplex.log || {
    echo "i2s_duplex 仿真没过，见 $WORK/xsim_duplex.log"
    tail -40 xsim_duplex.log
    exit 1; }
echo
echo "### i2s_duplex 激励全过"
