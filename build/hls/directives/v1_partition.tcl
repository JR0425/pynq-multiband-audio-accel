# v1 —— 数组分割（array partition）
#
# 基线为什么慢：HLS 把 `hist[65]` 和 `FIR_COEFFS[4][65]` 实现成了 RAM。
# RAM 只有 1~2 个读口，所以内层那个 4x65 的乘加循环每一轮最多只能读一个数，
# 想做快也做不快 —— 被"内存带宽"卡住了，不是被运算卡住的。
#
# 数组分割就是把这个"内存"拆开：每个元素各占一份寄存器，想同时读几个都行。
#   -type complete  = 全部拆开（65 个元素就是 65 份寄存器）
#
# 这一版先只拆数组、不展开循环，用来看清"拆掉内存瓶颈之后，
# 剩下的瓶颈是什么"。预期：资源明显变大，速度有一点改善但不彻底
# —— 因为还有一个串行的加法链在拖后腿（基线报告里那条 II=6 的警告就是它）。

set_directive_array_partition -type complete -dim 1 fir_multiband hist

# FIR_COEFFS 是二维 [4][65]，要拆的是第二维（65 个抽头）
set_directive_array_partition -type complete -dim 2 fir_multiband FIR_COEFFS
