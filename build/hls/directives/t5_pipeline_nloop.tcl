# t5 / t6：只加"流水线"指令，源码一个字不动
#
# 动机：Xilinx 官方 Vitis_Accel_Examples 的 shift_register 例子（fir_naive ->
#       fir_shift_register，官方实测 72 倍）里说，给**外层**循环加 PIPELINE 之后，
#       "the HLS compiler automatically unrolls the shift loop"（编译器会自动
#       把移位循环展开）。如果这条对我们也成立，那 t3 那个源码改写就不是必需的 ——
#       只加一条指令就够，源码保持原样、风险更小。
#
# 对照：t3_shiftchain（改写法）= 28 拍。若这里也是 28 拍，说明两条路等价；
#       若这里还是 194/398，说明"编译器自己会展开"在我们的写法下不成立，
#       t3 那个改写才是唯一出路 —— 那也是结论。
#
# ⚠️ 循环标签（VITIS_LOOP_xxx_n）里的行号会随源码改动而变。
#    本文件按下完 FIR_SHIFT_CHAIN 开关之后的源码行号写：外层逐采样循环 = VITIS_LOOP_270_3。
#
# ⚠️ set_directive_* 会**静默失效**（不报错、不生效）。判据是重跑之后看报告里
#    该循环的 Pipelined / II 列有没有变，别信"命令跑过了"。

set_directive_pipeline -II 1 fir_multiband/VITIS_LOOP_270_3
