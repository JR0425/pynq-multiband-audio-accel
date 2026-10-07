#!/usr/bin/env bash
# 跑 fir_lp 的 RTL 仿真，三个滤波器各跑一遍，然后把结果和参考模型逐位比对。
#
#   bash sim/rtl_sim/run_xsim.sh
#
# 前置：先跑过
#   python tools/gen_rtl_coeffs.py
#   python tools/ref_model_int.py
#
# 为什么三个滤波器分三次跑、而不是在 testbench 里例化三次：
#   系数表是**按文件名读**的（见 src/rtl/fir_lp.v 里那段说明），三份一起
#   例化会抢同一个 coeffs.mem。一次跑一个正好，而且出错时"是哪个滤波器"
#   这个问题不用问。
#
# ⚠️ 路径：Xilinx 的工具是 .bat，只能用 cmd 调；而 cmd 会把参数里的引号
#    重新解析一遍。所以**任何路径都不上命令行** —— 靠"把文件复制成固定名字 +
#    从固定工作目录启动"来解决，最后一个参数都没有。

set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"
WORK="$SIM/xsim_work"

XV="E:/Xilinx/Vivado/2020.2/bin/xvlog.bat"
XE="E:/Xilinx/Vivado/2020.2/bin/xelab.bat"
XS="E:/Xilinx/Vivado/2020.2/bin/xsim.bat"

for f in "$XV" "$XE" "$XS"; do
    [ -f "$f" ] || { echo "找不到 $f"; exit 1; }
done
[ -f "$SIM/in_q15.mem" ] || {
    echo "缺 $SIM/in_q15.mem —— 先跑 python tools/ref_model_int.py"; exit 1; }
[ -f "$ROOT/src/rtl/coeffs_lp500_n193.mem" ] || {
    echo "缺系数表 —— 先跑 python tools/gen_rtl_coeffs.py"; exit 1; }

rm -rf "$WORK"
mkdir -p "$WORK"
cd "$WORK" || exit 1

# 编译只认这两个源文件；include 路径也写全，免得依赖 cwd
cp "$SIM/in_q15.mem" in_q15.mem
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
    -i "$(cygpath -w "$SIM")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_lp.v")" \
    "$(cygpath -w "$SIM/tb_fir_lp.v")" \
    > xvlog.log 2>&1
grep -q "analyzing module" xvlog.log || {
    echo "xvlog 没跑起来，见 $WORK/xvlog.log"; cat xvlog.log; exit 1; }
grep -iE "^ERROR" xvlog.log && { echo "xvlog 有错，见 $WORK/xvlog.log"; exit 1; }

echo "### 精化"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" \
    tb_fir_lp -s tb_sim --debug typical > xelab.log 2>&1
grep -iE "^ERROR" xelab.log && { echo "xelab 有错，见 $WORK/xelab.log"; exit 1; }
[ -d xsim.dir ] || {
    echo "xelab 没产出 xsim.dir，见 $WORK/xelab.log"; tail -30 xelab.log; exit 1; }

RC=0
for E in 500 1000 2000; do
    echo "### 仿真 LP$E"
    cp "$ROOT/src/rtl/coeffs_lp${E}_n193.mem" coeffs.mem
    rm -f rtl_acc.mem rtl_out.txt
    MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" \
        tb_sim -R > "xsim_lp${E}.log" 2>&1
    if ! grep -q "\[tb\] DONE" "xsim_lp${E}.log"; then
        echo "仿真没跑完，见 $WORK/xsim_lp${E}.log"
        tail -20 "xsim_lp${E}.log"
        RC=1
        continue
    fi
    grep -E "samples:|collected" "xsim_lp${E}.log" | sed 's/^/    /'
    # 按参考模型的命名挪到 sim/rtl_sim/ 下，check.py 只认那一套名字
    [ -f rtl_acc.mem ] && mv rtl_acc.mem "$SIM/rtl_acc_lp${E}.mem"
    [ -f rtl_out.txt ] && mv rtl_out.txt "$SIM/rtl_out_lp${E}.txt"
done

echo
echo "### 和参考模型逐位比对"
cd "$ROOT" || exit 1
PYTHONIOENCODING=utf-8 python "$SIM/check.py" || RC=1
exit $RC
