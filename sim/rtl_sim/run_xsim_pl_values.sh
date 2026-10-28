#!/usr/bin/env bash
# 喂真实 I2S 正弦，看 fir_pl_direct 内部到底吐不吐非零样本
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"
WORK="$SIM/xsim_work_plv_$$"
XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
XE="${XELAB_BIN:-E:/Xilinx/Vivado/2020.2/bin/xelab.bat}"
XS="${XSIM_BIN:-E:/Xilinx/Vivado/2020.2/bin/xsim.bat}"
for f in "$XV" "$XE" "$XS"; do
  [ -f "$f" ] || { echo "找不到 $f"; exit 1; }
done
mkdir -p "$WORK"; cd "$WORK" || exit 1

# 系数表是 $readmemh 按文件名读的，必须在 cwd 里
for e in 500 1000 2000; do
  cp "$ROOT/src/rtl/coeffs_lp${e}_n193.mem" "coeffs_lp${e}.mem" || exit 1
done

FILES=(
  "$ROOT/src/rtl/axis_fifo.v"
  "$ROOT/src/rtl/i2s_duplex.v"
  "$ROOT/src/rtl/fir_mb_fe.v"
  "$ROOT/src/rtl/fir_drc4.v"
  "$ROOT/src/rtl/fir_gain_ramp.v"
  "$ROOT/src/rtl/fir_pl_direct.v"
  "$SIM/tb_pl_values.v"
)

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" $(for f in "${FILES[@]}"; do printf '%s ' "$(cygpath -w "$f")"; done) > xvlog.log 2>&1
grep -qiE "^ERROR" xvlog.log && { echo "xvlog 失败"; tail -20 xvlog.log; exit 1; }

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" tb_pl_values -s tb_val > xelab.log 2>&1
grep -qiE "^ERROR" xelab.log && { echo "xelab 失败"; tail -20 xelab.log; exit 1; }

MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" tb_val -R > xsim.log 2>&1
grep -aE "^(===|  |\[)" xsim.log | head -20
