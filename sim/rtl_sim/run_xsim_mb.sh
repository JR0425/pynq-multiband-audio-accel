#!/usr/bin/env bash
# 跑 fir_mb_fe（3 低通轮转 + 相减 + 4 段综合）的 RTL 仿真，然后逐位对拍。
#
#   bash sim/rtl_sim/run_xsim_mb.sh
#
# 前置：先跑过
#   python sim/model/gen_rtl_coeffs.py
#   python sim/model/ref_model_int.py     # 它顺手写出 in_q15.mem
#   python sim/model/ref_model_mb.py
#
# 和 run_xsim.sh 的区别：三组系数**同时**摆在目录里，模块一次读完，
# 所以整个前端只跑一次仿真（不是像 fir_lp 那样跑三遍）。
#
# ⚠️ 路径不上命令行（原因见 src/rtl/fir_lp.v 文件头）。所有文件都用固定名
#    摆在工作目录里，xsim 最后一个参数都没有。

set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SIM="$ROOT/sim/rtl_sim"
WORK="$SIM/xsim_work_mb"

# 工具路径可用环境变量覆盖（默认是这份仓库当初用的安装位置）
XV="${XVLOG_BIN:-E:/Xilinx/Vivado/2020.2/bin/xvlog.bat}"
XE="${XELAB_BIN:-E:/Xilinx/Vivado/2020.2/bin/xelab.bat}"
XS="${XSIM_BIN:-E:/Xilinx/Vivado/2020.2/bin/xsim.bat}"

for f in "$XV" "$XE" "$XS"; do
    [ -f "$f" ] || { echo "找不到 $f"; exit 1; }
done

[ -f "$SIM/in_q15.mem" ] || { echo "缺 $SIM/in_q15.mem —— 先跑 python sim/model/ref_model_int.py"; exit 1; }
[ -f "$SIM/ref_fe_sum.mem" ] || { echo "缺参考文件 —— 先跑 python sim/model/ref_model_mb.py"; exit 1; }
for E in 500 1000 2000; do
    [ -f "$ROOT/src/rtl/coeffs_lp${E}_n193.mem" ] || {
        echo "缺 coeffs_lp${E}_n193.mem —— 先跑 python sim/model/gen_rtl_coeffs.py"; exit 1; }
done

rm -rf "$WORK"
mkdir -p "$WORK"
cd "$WORK" || exit 1

cp "$SIM/in_q15.mem" in_q15.mem
cp "$ROOT/src/rtl/coeffs_lp500_n193.mem"  coeffs_lp500.mem
cp "$ROOT/src/rtl/coeffs_lp1000_n193.mem" coeffs_lp1000.mem
cp "$ROOT/src/rtl/coeffs_lp2000_n193.mem" coeffs_lp2000.mem

echo "### 编译"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XV")" \
    -i "$(cygpath -w "$SIM")" \
    "$(cygpath -w "$ROOT/src/rtl/fir_mb_fe.v")" \
    "$(cygpath -w "$SIM/tb_fir_mb_fe.v")" \
    > xvlog.log 2>&1
grep -q "analyzing module" xvlog.log || {
    echo "xvlog 没跑起来，见 $WORK/xvlog.log"; cat xvlog.log; exit 1; }
grep -iE "^ERROR" xvlog.log && { echo "xvlog 有错，见 $WORK/xvlog.log"; exit 1; }

echo "### 精化"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XE")" \
    tb_fir_mb_fe -s tb_sim_mb --debug typical > xelab.log 2>&1
grep -iE "^ERROR" xelab.log && { echo "xelab 有错，见 $WORK/xelab.log"; exit 1; }
[ -d xsim.dir ] || {
    echo "xelab 没产出 xsim.dir，见 $WORK/xelab.log"; tail -30 xelab.log; exit 1; }

echo "### 仿真"
MSYS_NO_PATHCONV=1 cmd /c "$(cygpath -w "$XS")" tb_sim_mb -R > xsim_mb.log 2>&1
if ! grep -q "\[tb\] DONE" xsim_mb.log; then
    echo "仿真没跑完，见 $WORK/xsim_mb.log"
    tail -30 xsim_mb.log
    exit 1
fi
grep -E "samples:|collected|cycles:|ERROR" xsim_mb.log | sed 's/^/    /'

RC=0
for f in rtl_fe_ylp0.mem rtl_fe_ylp1.mem rtl_fe_ylp2.mem \
         rtl_fe_band0.mem rtl_fe_band1.mem rtl_fe_band2.mem rtl_fe_band3.mem \
         rtl_fe_sum.mem rtl_fe_out.txt; do
    if [ -f "$f" ]; then
        mv "$f" "$SIM/$f"
    else
        echo "缺输出文件 $f"; RC=1
    fi
done

echo
echo "### 和整数参考模型逐位比对"
cd "$ROOT" || exit 1
PYTHONIOENCODING=utf-8 python "$SIM/check_mb.py" || RC=1

echo
echo "参考模型自己那三条自检（含和 HLS csim bypass 的逐位比）跑这个看："
echo "  python sim/model/ref_model_mb.py"

exit $RC
