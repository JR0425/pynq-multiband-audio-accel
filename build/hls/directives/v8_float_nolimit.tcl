# v8_float_nolimit —— 「不限流」这一格的浮点半边
#
# 这一版**故意不下任何 allocation 指令**：乘法器想用几个就用几个，全部并行铺开。
# 这是和 Nunigan/FIR-FIlter_HLS 那组报告可比的设计点 —— 他们那版也是全并行不限流。
#
# 为什么要补这一格：
#   现有的 v5 / v6 / v7 全部是「乘法器上限 5」，量出来的结论是
#   「定点更省 DSP、但更费 LUT」。而 Nunigan 在同样的想法下（同一份源码、
#   只换数据类型，float 对 ap_fixed<32,1>）量出来的却是**定点全面更省**：
#       float  372 DSP / 31017 LUT
#       fixed  162 DSP / 14705 LUT
#   两边的自变量不同 —— 他们不限流，我限 5。所以这个方向相反的结果
#   必须补一格「两边都不限流」的对照才能定位，不能靠嘴解释。
#
# 对照组：v9_fixed_nolimit（同一份源码、同样不下指令，只开 -DFIR_FIXED=1）
#
# 复现命令（在仓库根目录）：
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v8_float_nolimit HLS_CFLAGS="-DFIR_PARTIAL=5" \
#     "E:/Xilinx/Vitis_HLS/2020.2/bin/vitis_hls.bat" -f build/hls/run_synth.tcl
#
# 这个文件里没有 set_directive_* —— 它存在的意义就是把「不下指令」这件事
# 写成一份可复现的记录，而不是靠「当时忘了加」。

# 无指令：不限制任何运算的个数，让 HLS 自己铺开。
