#!/usr/bin/env bash
# fir_pl_direct（全 PL 直连：I2S 收 → 前端 → 压缩 → I2S 发）的诊断仿真。
#
#   bash sim/rtl_sim/run_xsim_pl.sh
#
# **这一份不出 PASS/FAIL**，它是一台诊断台：把板上 2026-10-25 观察到的时序
# 原样喂进去 —— flush 20 ms 后立刻放行，数据不出；中间停 1 秒再放行就正常 ——
# 然后按秒打一次内部相位（前端相位、valid 移位链、两个 FIFO 的水位、各级握手计数）。
#
# 判读方法：看 `run+20us` 之后 out_vld / fifo_wr 有没有继续涨。
# 停住不动说明卡在“拿得到字但不往外发”那一级，`ph` 和 `v_sr` 指到哪一级就是哪一级。
#
# 走压缩通路要带上系数表（fir_mb_fe 按文件名读），脚本已经拷进工作目录。

set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"
WORK="$SIM/xsim_work_pl"

XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
XE="${XELAB_BIN:-E:/Xilinx/Vivado/2020.2/bin/xelab.bat}"
XS="${XSIM_BIN:-E:/Xilinx/Vivado/2020.2/bin/xsim.bat}"

for f in "$XV" "$XE" "$XS"; do
    [ -f "$f" ] || { echo "找不到 $f"; exit 1; }
done

for e in 500 1000 2000; do
    [ -f "$ROOT/src/rtl/coeffs_lp${e}_n193.mem" ] || {
        echo "缺 coeffs_lp${e}_n193.mem，先跑 python tools/gen_rtl_coeffs.py"
        exit 1; }
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
    "$(cygpath -w "$ROOT/src/rtl/fir_pl_direct.v")" \
    "$(cygpath -w "$SIM/tb_fir_pl_direct.v")" \
    > xvlog.log 2>&1
grep -q "analyzing module" xvlog.log || {
    echo "xvlog 没跑起来，见 $WORK/xvlog.log"; tail -20 xvlog.log; exit 1; }
if grep -iE "^ERROR" xvlog.log; then
    echo "xvlog 有错，见 $WORK/xvlog.log"; exit 1
fi

echo "### 精化"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" \
    tb_fir_pl_direct -s tb_sim_pl --debug typical > xelab.log 2>&1
if grep -iE "^ERROR" xelab.log; then
    echo "xelab 有错，见 $WORK/xelab.log"; tail -20 xelab.log; exit 1
fi

echo "### 仿真（诊断台，不出 PASS/FAIL）"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" tb_sim_pl -R > xsim_pl.log 2>&1
grep -E "^\[|^ +dbg0=" xsim_pl.log | sed 's/^/    /'

grep -q "\[rerun\]" xsim_pl.log || {
    echo "仿真没跑到最后的 rerun，见 $WORK/xsim_pl.log"
    tail -40 xsim_pl.log
    exit 1; }
echo
echo "### 快照在 $WORK/xsim_pl.log"
