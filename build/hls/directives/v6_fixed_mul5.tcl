# v6_fixed_mul5 —— 定点版 + 限流(这次限的是**定点乘法器**)
#
# 和 v5b_fixed_mulw 唯一的差别在第 22 行的指令:
#   v5b_fixed_mulw   set_directive_allocation -limit 5 ... fmul   ← 定点下**不生效**
#   这一版           set_directive_allocation -limit 5 ... mul    ← 定点下应该生效
#
# 为什么要试这一次:
#   HLS 里的 "operation 类型" 分得很细:
#     fmul = 浮点乘法器
#     mul  = 定点/整数乘法器
#   之前那条 `fmul` 是从浮点版直接抄过来的 —— 浮点下标的是 fadd/fmul,
#   换成定点之后整个数据通路的运算都换了一类,**那条指令就成了空文**。
#   证据:v5_fixed 综合出来 65 个乘法器一个没复用、DSP 只有 3 个(全落在逻辑格子里);
#         修正位宽后 v5b_fixed_mulw 变成 68 个 DSP、全并行铺开,也说明没有任何限流生效。
#
# 想看到的:乘法器数量掉到 5 个上下、LUT 跟着下去,
#           而拍数**不该像浮点那样暴涨** —— 因为定点加法只要 1 拍,
#           那条把浮点版卡到 327 拍的 5 拍延迟链在定点下压根不存在。
#           如果真是这样,这一版就是"又快又小":比浮点版快,和浮点版一样省。
#
# 复现命令(在仓库根目录):
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v6_fixed_mul5 HLS_DIRECTIVES=build/hls/directives/v6_fixed_mul5.tcl \
#     HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18" \
#     "E:/Xilinx/Vitis_HLS/2020.2/bin/vitis_hls.bat" -f build/hls/run_synth.tcl
set_directive_allocation -limit 5 -type operation fir_multiband mul
