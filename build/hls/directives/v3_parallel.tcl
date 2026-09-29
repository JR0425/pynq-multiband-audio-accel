# v3 —— 4 个频段并排算 + 数组摊开
#
# v2a（FIR_PARTIAL=5，不加指令）已经到手 824 拍/采样，是基线的 2 倍。
# 报告里剩下的两个哑巴：
#   VITIS_LOOP_86_4  频段循环：4 个频段严格排队，一个算完再算下一个（189 拍 x4）
#   VITIS_LOOP_95_6_VITIS_LOOP_96_7  MAC 循环：II=2 而不是 1
#
# 4 个频段之间**没有依赖**（各算各的、最后才相加），所以本该并排。
# MAC 循环的 II=2 是 hist 还在 RAM 里：RAM 读口不够，一拍喂不进 5 个抽头。
#
# ⚠️ 下面用到了 HLS 自动生成的循环名（VITIS_LOOP_xxx_n），它和源码行号绑死。
#    所以这份指令文件只对当前这一版 fir_multiband.cpp 有效。
#    以后一旦改动源码行数，循环名会变，需要照着新的综合日志把名字换掉。

# 把 hist 和系数表按 5 路循环拆分（cyclic）—— 每一路各占一份寄存器，
# 一拍就能同时读 5 个抽头，正好对上 FIR_PARTIAL=5。
set_directive_array_partition -type cyclic -factor 5 -dim 1 fir_multiband hist
set_directive_array_partition -type cyclic -factor 5 -dim 2 fir_multiband FIR_COEFFS

# 让 4 个频段并排算
set_directive_unroll fir_multiband/VITIS_LOOP_86_4
