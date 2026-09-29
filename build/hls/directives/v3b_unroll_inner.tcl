# v3b —— 在 v3 基础上，把内层 p 循环也完全展开
#
# v3 是「4 个频段并排 + 数组按 5 拆开」。
# 这一版再加一条：把 `y[p] += ...` 那个 p 循环（5 次）彻底展开成 5 份并行硬件，
# 让它和数组的 5 路拆分一一对上 —— 不然 HLS 可能还在拿一个乘法器轮流用。
#
# 循环名同样与源码行号绑死，只对当前这版 fir_multiband.cpp 有效。

set_directive_array_partition -type cyclic -factor 5 -dim 1 fir_multiband hist
set_directive_array_partition -type cyclic -factor 5 -dim 2 fir_multiband FIR_COEFFS

set_directive_unroll fir_multiband/VITIS_LOOP_86_4
set_directive_unroll fir_multiband/VITIS_LOOP_96_7
