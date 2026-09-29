# v5_fixed —— 定点版（采样 16 位 / 系数 18 位），并行度与浮点版保持一致
#
# 和 v5_ref_float 用的是**同一条指令、同一份源码、同一组结构开关**
# （-DFIR_PARTIAL=5），唯一的差别是一个编译开关：
#   v5_ref_float  -DFIR_FIXED=0（默认）—— 数据通路是 float
#   v5_fixed      -DFIR_FIXED=1        —— 数据通路是 ap_fixed<16,1> × ap_fixed<18,1>
#
# 这样量出来的差别才能只归因于"浮点换定点"，而不是并行度不同。
#
# 想法的来处：浮点加法电路（fadd）本身 5 拍延迟，是这一整条线所有瓶颈的根源；
# 定点加法只要 1 拍。而且浮点乘法在 FPGA 上要吃掉好几个 DSP，
# 定点 16×18 的乘法一个 DSP48 就够。
#
# 代价是精度，已单独量过（见 src/python/quantization_sweep.py）：
#   采样 16 位 / 系数 18 位 → SNR 77.2 dB、最大单点误差 5.96e-05
#   系数位宽到 16 位以上就不再是瓶颈（16→18 只涨 0.4 dB），所以取 18。
#
# 复现命令（在仓库根目录）：
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v5_fixed HLS_DIRECTIVES=build/hls/directives/v5_fixed_limit5.tcl \
#     HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18" \
#     "E:/Xilinx/Vitis_HLS/2020.2/bin/vitis_hls.bat" -f build/hls/run_synth.tcl
set_directive_allocation -limit 5 -type operation fir_multiband fmul
