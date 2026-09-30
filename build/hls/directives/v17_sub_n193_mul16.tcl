# v17_sub_n193_mul16 —— 继续往下压乘法器个数，找"面积-速度"的拐点
#
# v13(不限流) 197 DSP / 449 拍 -> v14(限 96) 97 DSP / 452 拍 -> v15(限 64) 65 DSP / 455 拍
# 结论：在 193 抽头定点版上，**乘法器数量根本不是瓶颈** ——
# 抽头循环卡在 II=2（hist 被实现成 RAM，读口不够），所以复用乘法器几乎不要钱。
# 既然复用的代价这么低，就继续往下压，看 DSP 能到多小、LUT 涨到多少。
#
# 复现命令（在仓库根目录）：
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v17_sub_n193_mul16 #     HLS_DIRECTIVES=build/hls/directives/v17_sub_n193_mul16.tcl #     HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18 -DFIR_ACC_SAT=0 -DFIR_N_TAPS=193" #     "E:/Xilinx/Vitis_HLS/2020.2/bin/vitis_hls.bat" -f build/hls/run_synth.tcl
set_directive_allocation -limit 16 -type operation fir_multiband mul
