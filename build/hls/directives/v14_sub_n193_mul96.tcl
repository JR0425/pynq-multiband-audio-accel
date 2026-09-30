# v14_sub_n193_mul96 —— 相减式 + 193 抽头 + **给乘法器限流 96**
#
# 为什么加限流：
#   v13_sub_n193 不限流，综合出来 197 个 DSP，占 xc7z020 全部 220 个的 **89%**。
#   核自己占 89%，剩下的 11% 要装整个 overlay（AXI、I2S、DMA 通路和包装逻辑）——
#   布局布线很可能过不去，即使过去也没有余量。
#   而 v13 每个采样只花 449 拍，实时预算有 2083 拍 —— **4.6 倍余量白放着**。
#   所以这里把速度换回面积：限死乘法器个数，逼 HLS 复用它们，代价是拍数变多。
#
#   这笔换算是**只有定点才划算**：浮点加法电路（fadd）本身 5 拍延迟，
#   限流会让它变成一条长串行链（实测浮点限流 5 -> 327 拍，不限流 274 拍，差得不多
#   但那是因为已经撞到加法链的天花板）。定点加法只要 1 拍，复用乘法器
#   只需要多跑几轮，不会额外造出延迟链。
#
# 为什么现在敢重提"折叠"那个结论：
#   早先量过"折叠（利用系数对称性把乘法减半）不划算"，结论是
#   **瓶颈不在乘法、在浮点加法链**。现在换成定点、而且瓶颈变成了 DSP 数量，
#   前提整个变了 —— 但这一版用的不是折叠，是最直接的限流，先量这个。
#
# ⚠️ `set_directive_allocation` 是按**运算类型名**匹配的，定点乘法的类型名是 `mul`
#    （浮点才叫 `fmul`）。抄浮点版的指令会让它变成一条空文，而且不报错 ——
#    判据是 DSP 数真的掉下来，而不是"命令有没有报错"。这一条踩过一次。
#
# 复现命令（在仓库根目录）：
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v14_sub_n193_mul96 \
#     HLS_DIRECTIVES=build/hls/directives/v14_sub_n193_mul96.tcl \
#     HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18 -DFIR_ACC_SAT=0 -DFIR_N_TAPS=193" \
#     "E:/Xilinx/Vitis_HLS/2020.2/bin/vitis_hls.bat" -f build/hls/run_synth.tcl
set_directive_allocation -limit 96 -type operation fir_multiband mul
