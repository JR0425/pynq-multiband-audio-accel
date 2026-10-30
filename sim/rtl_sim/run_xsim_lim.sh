#!/usr/bin/env bash
# fir_drc4 末级软膝限幅的自检（2026-10-30 任务 A）
#
#   bash sim/rtl_sim/run_xsim_lim.sh
#
# 判据：单调 / 有界 / 0.7 FS 以下逐位透明 / 过载不越界不翻符号不跳变。
# 日志本身就能当交付件（A_sim_log.txt 是它的副本）。
set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"
WORK="$SIM/xsim_work_lim"

XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
XE="${XELAB_BIN:-E:/Xilinx/Vivado/2020.2/bin/xelab.bat}"
XS="${XSIM_BIN:-E:/Xilinx/Vivado/2020.2/bin/xsim.bat}"

for f in "$XV" "$XE" "$XS"; do [ -f "$f" ] || { echo "找不到 $f"; exit 1; }; done

rm -rf "$WORK"; mkdir -p "$WORK"; cd "$WORK" || exit 1

echo "### 编译"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_drc4.v")" \
    "$(cygpath -w "$SIM/tb_drc_lim.v")" > xvlog.log 2>&1
grep -q "analyzing module" xvlog.log || { echo "xvlog 没跑起来"; tail -30 xvlog.log; exit 1; }
if grep -iE "^ERROR" xvlog.log; then echo "xvlog 有错"; exit 1; fi

echo "### 精化"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" tb_drc_lim -s tb_sim_lim > xelab.log 2>&1
if grep -iE "^ERROR" xelab.log; then echo "xelab 有错"; tail -30 xelab.log; exit 1; fi

echo "### 仿真"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" tb_sim_lim -R > xsim.log 2>&1
grep -E "^(===|  判据|  比较|  输出|  扫描)" xsim.log

if grep -q "=== DRC_LIM PASS ===" xsim.log; then
    echo "### 结论：PASS"
    exit 0
fi
echo "### 结论：FAIL，见 $WORK/xsim.log"
exit 1
