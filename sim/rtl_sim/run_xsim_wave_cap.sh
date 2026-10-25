#!/usr/bin/env bash
# fir_wave_cap（自研抓取器）的单元仿真。
#
#   bash sim/rtl_sim/run_xsim_wave_cap.sh
#
# 激励在 tb_wave_cap.v 里：输入每 3 拍一个握手、tdata 是采样序号，
# 输出整体延后 288 拍。抓两次（块头 / 块尾），检查：
#   触发的那一拍是不是第 0 条、位序对不对、TLAST 落在第 288 条、写满 1024 条置 done。

set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"
WORK="$SIM/xsim_work_wave_cap"

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
    "$(cygpath -w "$ROOT/src/rtl/fir_wave_cap.v")" \
    "$(cygpath -w "$SIM/tb_wave_cap.v")" \
    > xvlog.log 2>&1
grep -q "analyzing module" xvlog.log || {
    echo "xvlog 没跑起来，见 $WORK/xvlog.log"; tail -20 xvlog.log; exit 1; }
if grep -iE "^ERROR" xvlog.log; then
    echo "xvlog 有错，见 $WORK/xvlog.log"; exit 1
fi

echo "### 精化"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" \
    tb_wave_cap -s tb_sim_wave_cap --debug typical > xelab.log 2>&1
if grep -iE "^ERROR" xelab.log; then
    echo "xelab 有错，见 $WORK/xelab.log"; tail -20 xelab.log; exit 1
fi

echo "### 仿真"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" tb_sim_wave_cap -R > xsim_wave_cap.log 2>&1
grep -E "^(PASS|FAIL)|### " xsim_wave_cap.log | sed 's/^/    /'

grep -q "### ALL PASS" xsim_wave_cap.log || {
    echo "抓取器仿真没过，见 $WORK/xsim_wave_cap.log"
    tail -40 xsim_wave_cap.log
    exit 1; }
echo
echo "### 抓取器激励全过"
