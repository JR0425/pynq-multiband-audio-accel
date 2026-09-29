# v11_fixed_nosat_mul5 —— 定点 + 限流 5 + 关掉累加器的饱和
#
# 这一格量的才是「限流 + 不饱和能不能同时拿到」：
#   限流 5 能把 DSP 压下来（v6_fixed_mul5 = 8 DSP），
#   关饱和能把 LUT 压下来（v10_fixed_nosat = 6502 LUT），
#   这一版看两者能不能叠加。
#
# 三格的对照关系（同一份源码、同一组位宽）：
#   v6_fixed_mul5        限流 5 + AP_SAT    253 拍 /  8 DSP / 19130 LUT
#   v10_fixed_nosat      不限流 + AP_WRAP   173 拍 / 67 DSP /  6502 LUT
#   这一版               限流 5 + AP_WRAP   ？
#
# 要盯的一件事：`set_directive_allocation` 是按**运算类型名**匹配的，
# 而不限流版和限流版综合出来的乘法器模块名不一样
# （v10 是 `mul_mul_18s_16s_34_4_1`，v6 是 `mul_18s_16s_34_1_1`）。
# 所以这一版要**先确认指令真的生效了**再采信数字 ——
# 判据是 DSP 掉到 10 以下，而不是"命令有没有报错"。
# 这正是 skill 里那条「换数据类型后按运算类型下的指令都要重验」的延伸：
# 换溢出模式也会改变综合器选用的模块，指令未必还认得。
#
# 复现命令（在仓库根目录）：
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v11_fixed_nosat_mul5 \
#     HLS_DIRECTIVES=build/hls/directives/v11_fixed_nosat_mul5.tcl \
#     HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18 -DFIR_ACC_SAT=0" \
#     "E:/Xilinx/Vitis_HLS/2020.2/bin/vitis_hls.bat" -f build/hls/run_synth.tcl
set_directive_allocation -limit 5 -type operation fir_multiband mul
