/* 多频段音频处理核 —— HLS 版本
 *
 * 做什么：把一路音频分成 4 个频段，各自做动态范围压缩（DRC），再合回去。
 *          这是助听器里最核心的一步：不同频段的听力损失不一样，得分开处理。
 *
 * 与 Python 基线的对应关系（必须逐点对齐，否则"加速比"无从谈起）：
 *     src/python/multiband_baseline.py 里的
 *         signal.lfilter(taps, 1.0, x)     ->  下面的 FIR 抽头延迟线
 *         np.where(...)                    ->  下面的 drc()
 *         output += compressed             ->  下面的 acc
 *     系数来自同一批文件（sim/hls_csim/fir_coeffs_{1..4}.txt），不是重新设计的。
 *
 * ---- 为什么是"移位寄存器"这个写法 ----
 *     hist[0] 一直是最新进来的那个采样，每个新采样来的时候整体挪一格。
 *     这正好就是硬件里 FIR 的标准结构 —— 抽头延迟线（tapped delay line），
 *     hist 的每个元素对应一个寄存器。写成这样，HLS 综合出来的就是它，
 *     不存在"算法对但结构不对"的返工。
 *     后面加 pragma 做流水线/数组分割，改的也是这个结构的展开方式。
 *
 * ---- 数据类型：本版刻意用 float ----
 *     第一步要验的是"算法对不对"，所以用 float 和 Python 的 float64 结果对，
 *     差异只应来自浮点舍入，量级 1e-6。
 *     定点版本（ap_fixed，也是将来真正要上板的那版）放在后面单独做，
 *     这样"量化带来的误差"才能单独量出来，而不是混在这里说不清。
 */

#include "fir_coeffs.h"   /* N_TAPS / N_BANDS / FIR_COEFFS */

/* DRC 参数 —— 必须与 multiband_baseline.py 里的 0.1 / 0.7 一致 */
#define DRC_THRESHOLD 0.1f
#define DRC_SLOPE 0.7f

/* 单频段动态范围压缩：小信号原样过，超过阈值的部分按 0.7 的比例压下来。
 * 与 np.where(np.abs(f) > 0.1, np.sign(f) * (0.1 + (np.abs(f)-0.1)*0.7), f) 等价。 */
static float drc(float x) {
    float a = (x < 0.0f) ? -x : x;
    if (a <= DRC_THRESHOLD) {
        return x;
    }
    float sign = (x < 0.0f) ? -1.0f : 1.0f;
    return sign * (DRC_THRESHOLD + (a - DRC_THRESHOLD) * DRC_SLOPE);
}

/* 处理 length 个采样：in -> out。
 * 输入输出长度相同，一对一，没有内部缓冲，所以可以流式做。 */
void fir_multiband(const float *in, float *out, int length) {
    /* 抽头延迟线：hist[0] 是最新采样，hist[N_TAPS-1] 是最旧的。
     * 初值全 0，对应 Python lfilter 的零初始状态 —— 两边必须一样。 */
    float hist[N_TAPS];
    for (int k = 0; k < N_TAPS; k++) {
        hist[k] = 0.0f;
    }

    for (int n = 0; n < length; n++) {
        /* 整体右移一格，把新采样放到最前面 */
        for (int k = N_TAPS - 1; k > 0; k--) {
            hist[k] = hist[k - 1];
        }
        hist[0] = in[n];

        /* 4 个频段各自算一遍，各自压缩，再相加 */
        float acc = 0.0f;
        for (int b = 0; b < N_BANDS; b++) {
            float y = 0.0f;
            for (int k = 0; k < N_TAPS; k++) {
                y += hist[k] * FIR_COEFFS[b][k];
            }
            acc += drc(y);
        }
        out[n] = acc;
    }
}
