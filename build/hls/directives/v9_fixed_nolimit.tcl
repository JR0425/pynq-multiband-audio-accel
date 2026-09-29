# v9_fixed_nolimit —— 「不限流」这一格的定点半边
#
# 和 v8_float_nolimit 是**同一份源码、同一组结构开关（-DFIR_PARTIAL=5）、
# 同样不下任何 allocation 指令**，唯一的差别是一个编译开关：
#   v8_float_nolimit  -DFIR_FIXED=0（默认）—— 数据通路是 float
#   这一版             -DFIR_FIXED=1        —— 数据通路是 ap_fixed<16,1> × ap_fixed<18,1>
#
# 为什么要和 v8 配成一对（而不是直接和 v4_ref_limit5 / v6_fixed_mul5 比）：
#   限流那一组（上限 5）量出来的结论是「定点更省 DSP、更费 LUT」。
#   不限流这一组量的才是「两边都全并行铺开时谁更省」—— 也就是
#   Nunigan/FIR-FIlter_HLS 那组报告所在的设计点。
#   只有把两个设计点都量出来，才能说清楚那个方向相反的结果是
#   「限流复用造成的」还是「我的结论本身有问题」。
#
# ⚠️ 位宽用修好的写法（系数表本身就是定点类型，见 fir_coeffs.h）——
#    v5_fixed 那一版是不限流但位宽写错的（3 DSP / 28309 LUT），不能用它的数。
#
# 复现命令（在仓库根目录）：
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v9_fixed_nolimit \
#     HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18" \
#     "E:/Xilinx/Vitis_HLS/2020.2/bin/vitis_hls.bat" -f build/hls/run_synth.tcl

# 无指令：不限制任何运算的个数，让 HLS 自己铺开。
