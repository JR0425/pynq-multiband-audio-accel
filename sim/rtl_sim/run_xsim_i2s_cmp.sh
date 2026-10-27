#!/usr/bin/env bash
# 我们的 I2S 发送 vs 官方 iis_ser：同一份数据、同一根时钟，逐拍比对。
#
#   bash sim/rtl_sim/run_xsim_i2s_cmp.sh
#
# 混语言仿真：iis_ser 是 VHDL（官方音频 IP 的源码），其余是 Verilog。
set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"
WORK="$SIM/xsim_work_i2s_cmp"

XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
XH="${XVHDL_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvhdl.bat}"
XE="${XELAB_BIN:-E:/Xilinx/Vivado/2020.2/bin/xelab.bat}"
XS="${XSIM_BIN:-E:/Xilinx/Vivado/2020.2/bin/xsim.bat}"
VHD="$ROOT/build/vivado/ip_repo/audio_codec_ctrl_v1.0/src/iis_ser.vhd"

for f in "$XV" "$XH" "$XE" "$XS"; do
    [ -f "$f" ] || { echo "找不到 $f"; exit 1; }
done
[ -f "$VHD" ] || { echo "找不到官方 iis_ser.vhd: $VHD"; exit 1; }

rm -rf "$WORK"
mkdir -p "$WORK"
cd "$WORK" || exit 1

echo "### VHDL（官方发送器）"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XH")" \
    "$(cygpath -w "$VHD")" > xvhdl.log 2>&1
if grep -iE "^ERROR" xvhdl.log; then
    echo "xvhdl 有错"; tail -20 xvhdl.log; exit 1
fi

echo "### Verilog"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
    "$(cygpath -w "$ROOT/src/rtl/axis_fifo.v")" \
    "$(cygpath -w "$ROOT/src/rtl/i2s_duplex.v")" \
    "$(cygpath -w "$SIM/tb_i2s_vs_official.v")" > xvlog.log 2>&1
grep -q "analyzing module" xvlog.log || { echo "xvlog 失败"; tail -20 xvlog.log; exit 1; }
if grep -iE "^ERROR" xvlog.log; then echo "xvlog 有错"; tail -20 xvlog.log; exit 1; fi

echo "### 精化"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" \
    tb_i2s_vs_official -s tb_i2s_cmp --debug typical > xelab.log 2>&1
if grep -iE "^ERROR" xelab.log; then
    echo "xelab 有错"; tail -25 xelab.log; exit 1
fi

echo "### 仿真"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" tb_i2s_cmp -R > xsim.log 2>&1
grep -E "^===|^  diff|^  比对|^PASS|^FAIL|^\[done\]" xsim.log | sed 's/^/    /'
