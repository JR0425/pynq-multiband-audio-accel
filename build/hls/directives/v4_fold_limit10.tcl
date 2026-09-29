# v4_fold_limit10：折叠 + 乘法器上限 10
#
# 另一个方向的排查：会不会只是"拍子数"不够，而不是读口不够？
# 折叠后总乘法 132 次，上限 5 要 27 轮；上限 10 只要 14 轮。
# 如果这一版明显变快，说明瓶颈在轮数；如果还是慢，说明瓶颈在读口。
# 编译开关：-DFIR_FOLD=1
set_directive_allocation -limit 10 -type operation fir_multiband fmul
