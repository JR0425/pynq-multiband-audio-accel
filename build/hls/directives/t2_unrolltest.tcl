# 实验 t2：y 摊成寄存器 + 试一下 unroll 指令是否真的能用（挑一个没有子循环的小循环）
set_directive_array_partition -type complete -dim 1 fir_multiband y
set_directive_unroll fir_multiband/VITIS_LOOP_90_5
