#!/usr/bin/env bash
# 只做语法/elaboration 检查，不跑仿真
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$ROOT/sim/rtl_sim/xsim_lint_$$"
XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
mkdir -p "$WORK"; cd "$WORK" || exit 1
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
    "$(cygpath -w "$ROOT/src/rtl/axis_fifo.v")" \
    "$(cygpath -w "$ROOT/src/rtl/i2s_duplex.v")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_mb_fe.v")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_drc4.v")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_gain_ramp.v")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_pl_regs.v")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_pl_direct.v")" > xvlog.log 2>&1
grep -iE "^ERROR" xvlog.log && { echo "有错"; exit 1; }
grep -icE "warning" xvlog.log | sed 's/^/warning 行数: /'
echo "LINT OK"
