# v4_fold_cyc13：折叠 + 把 hist 分成 13 路
#
# v4_fold_limit5 出来是 1247 拍，比直算的 327 拍还慢 —— 和"乘法次数减半"的预期相反。
# 最可能的原因：**内存读口不够**。
#   直算：每次调度槽里 5 个乘法各读一个 hist[k]，按 5 路循环拆分正好一路一个，不冲突。
#   折叠：每次要读 hist[k] 和 hist[64-k] 两个，5 个乘法就是 10 个读口，
#         而 cyclic factor=5 只给了 5 个存储体 —— 每拍读不完，槽位被迫拉长。
# 所以这一版把 hist 拆成 13 路（65 = 5 x 13），看看是不是这个原因。
# 编译开关：-DFIR_FOLD=1
set_directive_allocation -limit 5 -type operation fir_multiband fmul
# （拆分改由源码里的 -DFIR_HIST_CYC13 控制，这里不再下指令）
