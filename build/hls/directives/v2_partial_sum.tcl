# v2 —— 拆开加法链（多个部分和）
#
# v1 证明了「光拆数组没用」：面积涨了 10 倍，速度一动不动。
# 卡住的是 y += h*c 这条加法链 —— 浮点加法电路本身有 5 拍延迟，
# 而 y 必须等上一轮算完，所以每 6 拍才能喂一个新数进去（基线报告里 II=6）。
#
# 破法：不要一个累加器，要 P 个。把 65 个抽头分成 P 组，
# 每组各累各的，最后再把这 P 个部分和加起来。
#
# ⚠️ 这**改变了浮点加法的顺序**，所以结果会有 1e-7 量级的差异
#    （和 Python 的 float64 比仍然是这个量级，不是算错了）。
#    加完必须重跑一次 csim 确认 —— 工序在 PROGRESS.md 里。
#
# 这一版先用 P=4 试水，配合把数组按 4 拆开（不然 4 个部分和会抢内存读口）。

set_directive_array_partition -type block -factor 4 -dim 1 fir_multiband hist
set_directive_array_partition -type block -factor 4 -dim 2 fir_multiband FIR_COEFFS
