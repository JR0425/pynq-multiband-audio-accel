# v3_limit20：乘法器上限设为 20 个（v3b 是 5 个）
# 扫这条曲线的目的：找"面积换速度"的拐点 ——
# 乘法器给得越多越快，但芯片上只有 220 个 DSP，不能无限给。
set_directive_allocation -limit 20 -type operation fir_multiband fmul
