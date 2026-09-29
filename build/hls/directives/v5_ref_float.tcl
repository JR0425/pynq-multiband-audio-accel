# v5_ref_float —— 定点那组的对照（同一份源码，只是不开 -DFIR_FIXED）
#
# 和 v5_fixed 用的是同一条指令（乘法器上限 5），唯一的差别是编译开关：
#   这一版      -DFIR_FIXED=0（默认）—— 数据通路是 float
#   v5_fixed 那版 -DFIR_FIXED=1        —— 数据通路是 16 位 × 18 位定点
#
# 放在一起跑，是为了让"定点省了多少"这个结论只归因于定点本身。
#
# ⚠️ 重跑这一版的另一个用处是**回归**：源码为了支持定点，把里面的 float
#    换成了类型别名（data_t / coef_t / acc_t），浮点下它们就等于 float。
#    重跑一遍确认综合结果和改之前那批（v3b_limit_mul / v4_ref_limit5）对得上，
#    否则说明这次重构动了不该动的东西。
#
#   已核对（2026-09-29）：csim 输出与重构前逐字节相同；
#   综合结果 327 拍 / 27 DSP / 9936 LUT，与 v3b_limit_mul、v4_ref_limit5 一致。
set_directive_allocation -limit 5 -type operation fir_multiband fmul
