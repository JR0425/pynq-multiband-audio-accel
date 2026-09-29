# v3a：在 pragma 版基础上，把系数表也按 5 路拆开
# 报告里 FIR_COEFFS 被实现成了 128 块 BRAM —— HLS 靠"把 ROM 复制很多份"来凑读口。
# 拆开之后每个 5 路各占一小块，理论上不用复制那么多。
set_directive_array_partition -type cyclic -factor 5 -dim 2 fir_multiband FIR_COEFFS
