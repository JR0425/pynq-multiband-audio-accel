# v4_fold_histfull：折叠 + hist 完全拆成寄存器
#
# 接着 v4_fold_cyc13 的思路往下走：既然折叠一次要读 10 个 hist 元素，
# 那就干脆把整个 hist 摊平成 65 个独立寄存器 —— 读口要多少有多少，彻底不冲突。
# 代价是 LUT 会涨（65 个 32 位寄存器 + 读选择器），这一版量的就是这个代价。
# 编译开关：-DFIR_FOLD=1
set_directive_allocation -limit 5 -type operation fir_multiband fmul
# （拆分改由源码里的 -DFIR_HIST_FULL 控制，这里不再下指令）
