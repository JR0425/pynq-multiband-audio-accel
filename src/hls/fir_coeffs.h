/* 抽头数在这里选。
 *
 * ⚠️ 抽头数**不是寄存器**，是编译期常量 —— AXI 寄存器改不动它。
 *    换抽头数 = 换一个 -DFIR_N_TAPS，重新综合。
 *    抽头数是编译期常量，不存在 ID 寄存器；板端驱动在 fir_core.py 中记录并核对该值。
 *
 * 为什么不把系数写成"运行时可写的数组"：
 *    系数一旦不是常量，HLS 就没法把它当 ROM 处理，很可能撑出一大片逻辑
 *    （本项目实测过一个反例：系数表写成 const float、在循环里现转定点，
 *     LUT 炸到 123550，232% 装不下）。
 *    代价是换边界要重新综合。这一条是**刻意的取舍**，不是漏想。
 *
 * 每份系数表的抽头数不同、数值也完全不同，不能混用，所以按抽头数分成几个文件。
 * 只 include 一个 —— 由 FIR_N_TAPS 决定。
 */

#ifndef FIR_COEFFS_H
#define FIR_COEFFS_H

#ifndef FIR_N_TAPS
/* 默认 193 —— 这是已定下来的设计点（report/hardware_interface_spec.md §1.5：
 * 65 抽头四段根本分不开，实测最差边界残留只有 −6 dB）。
 * 跑 65 抽头的对照版就显式加 -DFIR_N_TAPS=65，别指望默认值帮你切。 */
#define FIR_N_TAPS 193
#endif

#if FIR_N_TAPS == 65
#include "fir_coeffs_n65.h"
#elif FIR_N_TAPS == 193
#include "fir_coeffs_n193.h"
#else
#error "没有这个抽头数的系数表。先生成：python src/python/export_coeffs_header.py --taps <N>"
#endif

/* 相减式要求 N_TAPS 是奇数：
 *   · Type I 线性相位要求奇数长度，否则各段延迟不是整数、对不齐；
 *   · 第 4 段用 hist[D] 取"D 拍前的输入"，D = (N-1)/2 也必须是整数。
 * 生成脚本那边已经拦了一道，这里再拦一道 —— 手改宏的时候容易忘。 */
#if (N_TAPS % 2) == 0
#error "N_TAPS 必须是奇数"
#endif

/* 群延迟 = (N-1)/2。第 4 段直接取 hist[FIR_GROUP_DELAY]，不需要额外延迟线。 */
#define FIR_GROUP_DELAY ((N_TAPS - 1) / 2)

#endif /* FIR_COEFFS_H */
