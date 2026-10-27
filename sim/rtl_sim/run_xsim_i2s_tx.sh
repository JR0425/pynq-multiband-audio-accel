#!/usr/bin/env bash
# 用 codec 的解码规矩解我们自己发的 I2S 流，看比特位置对不对。
set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"
WORK="$SIM/xsim_work_i2s_tx"

XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
XE="${XELAB_BIN:-E:/Xilinx/Vivado/2020.2/bin/xelab.bat}"
XS="${XSIM_BIN:-E:/Xilinx/Vivado/2020.2/bin/xsim.bat}"

for f in "$XV" "$XE" "$XS"; do
    [ -f "$f" ] || { echo "找不到 $f"; exit 1; }
done

rm -rf "$WORK"
mkdir -p "$WORK"
cd "$WORK" || exit 1

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
    "$(cygpath -w "$ROOT/src/rtl/axis_fifo.v")" \
    "$(cygpath -w "$ROOT/src/rtl/i2s_duplex.v")" \
    "$(cygpath -w "$SIM/tb_i2s_tx_selfcheck.v")" > xvlog.log 2>&1
grep -q "analyzing module" xvlog.log || { echo "xvlog 失败"; tail -20 xvlog.log; exit 1; }
if grep -iE "^ERROR" xvlog.log; then echo "xvlog 有错"; tail -20 xvlog.log; exit 1; fi

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" tb_i2s_tx_selfcheck -s tb_i2s -debug typical > xelab.log 2>&1
if grep -iE "^ERROR" xelab.log; then echo "xelab 有错"; tail -20 xelab.log; exit 1; fi

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" tb_i2s -R > xsim.log 2>&1
grep -E "^===|^    |^  frame|^  共|^\[done\]" xsim.log | sed 's/^/    /'
