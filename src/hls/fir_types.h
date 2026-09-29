/* 核里用到的数据类型 —— 浮点 / 定点由 FIR_FIXED 切换。
 *
 * 为什么单独抽一个头文件：
 *     系数表 fir_coeffs.h 需要知道 coef_t 是什么（这样表里存的就是定点数本身，
 *     而不是"存 float、在硬件里现转" —— 后者实测会生成 65 个 float→double
 *     扩位器，LUT 直接炸到 232%）。
 *     而 fir_coeffs.h 是自动生成的，不想让它夹带一大段类型定义，
 *     所以类型单独放这里，两边都 include 它。
 *
 * 两种模式下的三种类型：
 *     浮点：data_t = coef_t = acc_t = float
 *     定点：data_t = Q(FIR_DW-1).1（采样）
 *           coef_t = Q(FIR_CW-1).1（系数）
 *           acc_t  = Q(FIR_ACC_W-8).8（累加，留 8 位整数位防溢出）
 *
 * 定点时这个头文件依赖 Xilinx 的 ap_fixed.h —— **本机 g++ 没有**，
 * 所以定点版没法用 g++ 先验语法，只能交给 Vitis HLS 编。
 */

#ifndef FIR_TYPES_H
#define FIR_TYPES_H

#ifndef FIR_FIXED
#define FIR_FIXED 0
#endif

#if FIR_FIXED

#include <ap_fixed.h>

#ifndef FIR_DW
#define FIR_DW 16        /* 采样总位宽（含符号位），Q1.(DW-1) */
#endif
#ifndef FIR_CW
#define FIR_CW 18        /* 系数总位宽 */
#endif
#ifndef FIR_ACC_W
#define FIR_ACC_W 40     /* 累加器总位宽 */
#endif

typedef ap_fixed<FIR_DW, 1, AP_TRN, AP_SAT> data_t;
typedef ap_fixed<FIR_CW, 1, AP_RND, AP_SAT> coef_t;

/* 累加器的溢出模式单独给一个开关 —— 因为**它这个饱和是白花的**。
 *
 * 饱和（AP_SAT）在每个运算后面都要挂一个"超范围就钳住"的选择器，
 * 实测这条逻辑占掉定点版表达式层的 43%（7024 LUT / 16201）。
 *
 * 但累加器**永远不会溢出**：它是 65 个 16 位 × 18 位乘积之和，
 * 最坏情况放大 65 倍，而 Q8.32 有 8 位整数位 = 256 倍余量。
 * 也就是说那些选择器**一次都不会触发** —— 白放。
 *
 * 所以这里的 1 / 0 只是"要不要白花这笔逻辑"，**不改变任何数值**：
 *   1（默认）= AP_SAT，和以前逐位相同
 *   0        = AP_WRAP，综合出来更省 LUT，csim 输出应当逐字节不变
 *
 * ⚠️ data_t 那一层的饱和**不能关**：它的输入是外部送进来的 float，
 *    真可能超出 [-1,1)，绕回会变成一个反号的大数（爆音）。
 *    acc_t 的输入全是内部乘积，才有"不可能溢出"这个前提。 */
#ifndef FIR_ACC_SAT
#define FIR_ACC_SAT 1
#endif

#if FIR_ACC_SAT
typedef ap_fixed<FIR_ACC_W, 8, AP_TRN, AP_SAT> acc_t;
#else
typedef ap_fixed<FIR_ACC_W, 8, AP_TRN, AP_WRAP> acc_t;
#endif
/* DRC 内部运算用的宽度 —— 故意比累加器窄。
 *   累加器要 40 位是因为它是 65 个乘积之和,必须留足整数位防溢出;
 *   而 DRC 的输入是**单个频段已经算完的输出**,量级就在 ±1 附近,
 *   给它 40 位纯属浪费:定点乘法器的面积随位宽走,40x40 和 24x24 差四倍,
 *   而且这些乘法器 HLS 不一定都塞得进 DSP,剩下的全落进逻辑格子。
 *   Q2.22 的量化步长是 2^-22,相对 ±1 的信号约 -126 dB,
 *   远低于采样位宽本身带来的 77 dB —— 加进来不会动 SNR。
 *   浮点模式下它等于 float,所以这一层不影响浮点版。 */
typedef ap_fixed<24, 2, AP_TRN, AP_SAT> drc_t;

#else

typedef float data_t;
typedef float coef_t;
typedef float acc_t;
typedef float drc_t;

#endif /* FIR_FIXED */

#endif /* FIR_TYPES_H */
