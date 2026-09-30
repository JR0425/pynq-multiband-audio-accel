# v15_sub_n193_mul64 —— 同 v14，限流数从 96 收到 64
#
# v14 量的是"96 个乘法器够不够把 DSP 压到可接受"。这一版接着往下压，
# 目的是找出"面积-速度"的拐点在哪 —— 和早先扫 v3_limit10/20/40 是同一条曲线，
# 只是这次的自变量换成了定点乘法器、而且规模是 193 抽头。
#
# 判据：拍数必须留在 2083 以内（48 kHz @ 100 MHz 的实时预算），
# 且留出足够余量给"包装成 overlay 之后还要重新量一次时序"这件事。
#
# 复现命令（在仓库根目录）：
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v15_sub_n193_mul64 \
#     HLS_DIRECTIVES=build/hls/directives/v15_sub_n193_mul64.tcl \
#     HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18 -DFIR_ACC_SAT=0 -DFIR_N_TAPS=193" \
#     "E:/Xilinx/Vitis_HLS/2020.2/bin/vitis_hls.bat" -f build/hls/run_synth.tcl
set_directive_allocation -limit 64 -type operation fir_multiband mul
