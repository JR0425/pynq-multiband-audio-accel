#!/usr/bin/env bash
# 逐帧变化数据下，对比我们的 TX 与官方 iis_ser 解出来的样本序列。
set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"
WORK="$SIM/xsim_work_i2s_seq"

XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
XH="${XVHDL_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvhdl.bat}"
XE="${XELAB_BIN:-E:/Xilinx/Vivado/2020.2/bin/xelab.bat}"
XS="${XSIM_BIN:-E:/Xilinx/Vivado/2020.2/bin/xsim.bat}"
VHD="$ROOT/build/vivado/ip_repo/audio_codec_ctrl_v1.0/src/iis_ser.vhd"

for f in "$XV" "$XH" "$XE" "$XS"; do
    [ -f "$f" ] || { echo "找不到 $f"; exit 1; }
done

rm -rf "$WORK"
mkdir -p "$WORK"
cd "$WORK" || exit 1

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XH")" "$(cygpath -w "$VHD")" > xvhdl.log 2>&1
if grep -iE "^ERROR" xvhdl.log; then echo "xvhdl 有错"; tail -20 xvhdl.log; exit 1; fi

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
    "$(cygpath -w "$ROOT/src/rtl/axis_fifo.v")" \
    "$(cygpath -w "$ROOT/src/rtl/i2s_duplex.v")" \
    "$(cygpath -w "$SIM/tb_i2s_seq_cmp.v")" > xvlog.log 2>&1
if grep -iE "^ERROR" xvlog.log; then echo "xvlog 有错"; tail -20 xvlog.log; exit 1; fi

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" tb_i2s_seq_cmp -s tb_i2s_seq --debug typical > xelab.log 2>&1
if grep -iE "^ERROR" xelab.log; then echo "xelab 有错"; tail -25 xelab.log; exit 1; fi

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" tb_i2s_seq -R > xsim.log 2>&1
grep -E "^===|^  我们|^  官方|^  最佳|^PASS|^FAIL|^\[done\]" xsim.log | sed 's/^/    /'
