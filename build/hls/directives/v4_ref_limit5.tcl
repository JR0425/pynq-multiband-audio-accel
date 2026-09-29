# v4_ref —— 折叠那组的对照（同一份源码，只是不开 -DFIR_FOLD）
#
# 和 v4_fold_limit5 用的是同一条指令（乘法器上限 5），唯一的差别是编译开关：
#   这一版      -DFIR_FOLD=0（默认）—— 直算，65 个抽头逐个乘
#   v4_fold 那版 -DFIR_FOLD=1        —— 折叠，33 项先加后乘
#
# 放在一起跑，是为了让"折叠省了多少"这个结论只归因于折叠本身 ——
# 两边并行度一样、指令一样、源码同一份。
set_directive_allocation -limit 5 -type operation fir_multiband fmul
