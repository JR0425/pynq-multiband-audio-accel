# v3b：直接给乘法器数量设上限
# pragma 版一次用掉 345 个 DSP（芯片只有 220），根因是 HLS 自己把抽头循环
# 整个展开成了全并行。这一版用 allocation 硬性限死"乘法器最多 5 个"，
# 逼 HLS 回到折叠的实现 —— 这是控制面积最直接的一把闸。
set_directive_allocation -limit 5 -type operation fir_multiband fmul
