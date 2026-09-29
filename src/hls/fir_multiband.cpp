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
 *     hist 的每个元素对应一个寄存器。整体移位在硬件里只是寄存器之间接几根线，
 *     不消耗任何逻辑资源。
 *
 * ---- ★ FIR_PARTIAL：决定"每一拍能并行算几个抽头" ----
 *     累加器 y 只有一个的时候，`y += ...` 会变成一条**串行的加法链**：
 *     浮点加法电路（fadd）本身有 5 拍延迟，y 又必须等上一轮 y 算完，
 *     于是每 6 拍才喂得进一个新抽头，4x65=260 个抽头要 1560 拍。
 *     实测（v0 基线）：每个采样 1661 拍，而 48 kHz 实时只给 20.8 us = 2083 拍。
 *     —— 也就是说**能跑，但几乎没有余量**，而且这条链会卡死后面所有优化。
 *
 *     破法就是把一个累加器拆成 FIR_PARTIAL 个"部分和"，各加各的，最后再合并。
 *     65 = 5 x 13，取 5 或 13 都能整除，不用补零、不用处理尾巴。
 *
 *     ⚠️ 这**改变了浮点加法的结合顺序**，结果与"单一累加器"版有 1e-7 量级的差异
 *        （和 Python 的 float64 黄金参考比仍在这个量级，不是算错了）。
 *        FIR_PARTIAL=1 时退化成原来的单一累加器，**与优化前的加法顺序完全相同** ——
 *        "优化前"和"优化后"因此可以用同一份源码跑出来，是可复现的对比，不是靠回滚手改。
 *        编译期用 add_files -cflags "-DFIR_PARTIAL=<n>" 切换。
 *
 * ---- 数据类型：本节仍是 float ----
 *     先用 float 把"结构优化"这条线量干净，再单独做定点版（ap_fixed）。
 *     这样"结构改了多少"和"量化误差多少"两个问题不会混在一起说不清。
 */

#include "fir_coeffs.h"   /* N_TAPS / N_BANDS / FIR_COEFFS */

/* DRC 参数 —— 必须与 multiband_baseline.py 里的 0.1 / 0.7 一致 */
#define DRC_THRESHOLD 0.1f
#define DRC_SLOPE 0.7f

/* 部分和的个数。1 = 单一累加器（等价于优化前的写法）；
 * 越大越并行、越费乘法器。必须能整除 N_TAPS（65 的因数是 1/5/13/65）。 */
#ifndef FIR_PARTIAL
#define FIR_PARTIAL 5
#endif

/* ---- FIR_FOLD：利用系数对称性做"折叠"，把乘法次数砍掉一半 ----
 *     四组系数都是严格对称的（c[k] == c[N_TAPS-1-k]，已逐个核对过），
 *     因为 firwin 生成的线性相位 FIR 必然对称。既然两头乘的是同一个数，
 *     就可以先把两个采样加起来，再只乘一次：
 *
 *         c[k]*h[k] + c[64-k]*h[64-k]   ==   c[k] * ( h[k] + h[64-k] )
 *
 *     65 个抽头 -> 33 次乘法（32 对 + 1 个中心抽头）。四段合计 260 -> 132。
 *     代价是多 32 次加法；但 FPGA 上加法用普通逻辑格子，乘法要吃掉稀缺的
 *     DSP（板上只有 220 个），所以这笔换算是划算的。
 *
 *     数学上与直算完全等价，只有浮点加法的结合顺序变了 ——
 *     和 FIR_PARTIAL 是同一量级（1e-7）的差异，改完必须重跑 csim。
 *
 *     ⚠️ 实测结论：**在这套结构下不划算，默认关闭。**
 *        同为"乘法器上限 5"：直算 327 拍 / 27 DSP，折叠 1247 拍 / 25 DSP。
 *        把 hist 拆得更开能救回来一些（拆成 65 个寄存器：663 拍），
 *        面积却涨到 79 DSP / 19924 LUT，仍然比直算慢一倍。
 *
 *        原因不是读口不够，而是**瓶颈根本不在乘法**：
 *          直算：每项 = 1 次乘法 + 1 次累加
 *          折叠：每项 = 1 次先加 + 1 次乘法 + 1 次累加
 *        乘法从 65 次减到 33 次（真的减半了），但加法从 65 次变成 66 次。
 *        而这条线的瓶颈是浮点加法的延迟链（fadd 有 5 拍延迟），乘法靠
 *        allocation 限流就能复用 —— 于是折叠减掉的是不紧张的资源，
 *        加的却是最紧张的那个。旁证：把乘法器上限从 5 提到 10，拍数
 *        一动不动（都是 1247），说明乘法器数量压根不是约束。
 *
 *        留在这里是因为"试过、量过、知道为什么不划算"本身就是结论，
 *        换个数结构（比如定点化之后加法变便宜）时值得再试一次。
 *
 *     0 = 直算（与折叠前逐字相同，用于做对照），1 = 折叠。
 *     默认 0：不开这个开关时，编出来的结构和以前一模一样，
 *     所以 build/hls/reports/ 下早先那批报告仍然对得上。 */
#ifndef FIR_FOLD
#define FIR_FOLD 0
#endif

/* 折叠后要算多少项：32 对 + 1 个中心 */
#define N_FOLD ((N_TAPS + 1) / 2)

/* 硬件 pragma 的总开关（0 = 只改结构、不展开不拆分；1 = 把并行度真的做进硬件里）。
 * 分成两个开关是为了让"改了结构"和"又加了 pragma"两版能分别综合出来对比 ——
 * 否则会分不清提速是哪一步带来的。
 * ⚠️ 打开时假设 FIR_PARTIAL == 5（pragma 里的因子写死成 5）。 */
#ifndef FIR_PRAGMA
#define FIR_PRAGMA 1
#endif

/* 直算那条路要靠"k 每轮跳 FIR_PARTIAL 个"来分组，所以要求整除；
 * 折叠那条路是逐项累加，没有这个要求（33 不能被 5 整除）。 */
#if !FIR_FOLD && ((N_TAPS % FIR_PARTIAL) != 0)
#error "FIR_PARTIAL must divide N_TAPS (65 -> 1, 5, 13, 65)"
#endif

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
#if FIR_PRAGMA
    /* 按 5 路循环拆分（cyclic），与 FIR_PARTIAL 对齐：
     * 第 k 个抽头固定落在第 (k % 5) 路。这样内层 p 循环展开之后，
     * hist[k+p] 的"落在哪一路"是编译期就知道的常量，不需要任何选择器。
     * 如果改用 -type complete（每格一个寄存器），索引会变成 65 选 1 的大选择器，
     * 面积会失控；用 block 则跨块移动会变多。
     * ⚠️ pragma 里写不了宏（HLS 不展开），因子只能写死成数字，所以这里用
     *    条件编译给出几种写法，靠编译开关切换 —— 用于排查"折叠后读口不够"的猜想：
     *      -DFIR_HIST_CYC13  拆成 13 路
     *      -DFIR_HIST_FULL   完全拆开（65 个独立寄存器）
     *     默认（都不给）保持 5 路，和以前一模一样。 */
#if defined(FIR_HIST_FULL)
#pragma HLS ARRAY_PARTITION variable=hist complete dim=1
#elif defined(FIR_HIST_CYC13)
#pragma HLS ARRAY_PARTITION variable=hist cyclic factor=13 dim=1
#else
#pragma HLS ARRAY_PARTITION variable=hist cyclic factor=5 dim=1
#endif
#endif
    for (int k = 0; k < N_TAPS; k++) {
        hist[k] = 0.0f;
    }

    for (int n = 0; n < length; n++) {
        /* 整体右移一格，把新采样放到最前面。
         * 完全展开：移位在硬件里只是寄存器之间重新接几根线，不花逻辑资源，
         * 展开之后 64 次移动变成同一拍的 64 组线，从 64 拍降到 1 拍。 */
#if FIR_PRAGMA
#pragma HLS UNROLL
#endif
        for (int k = N_TAPS - 1; k > 0; k--) {
            hist[k] = hist[k - 1];
        }
        hist[0] = in[n];

        /* 4 个频段各自算一遍，各自压缩，再相加 */
        float acc = 0.0f;
        for (int b = 0; b < N_BANDS; b++) {
            /* FIR_PARTIAL 个部分和：每个负责 1/FIR_PARTIAL 的抽头。
             * 它们之间没有依赖，硬件里可以同时算 —— 这就是并行度的来源。 */
            float y[FIR_PARTIAL];
            for (int p = 0; p < FIR_PARTIAL; p++) {
                y[p] = 0.0f;
            }

#if FIR_FOLD
            /* ---- 折叠结构：先加、后乘 ----
             * 每一项 k 代表"一对"：h[k] 和 h[N_TAPS-1-k]，它们乘的是同一个系数。
             * 最后一项（k == N_FOLD-1 == 32）是正中间那个，没有配对的，单独算。
             * 展开之后 "k != N_FOLD-1" 这个判断是编译期常量，硬件里不产生任何比较器。 */
            for (int k = 0; k < N_FOLD; k++) {
#if FIR_PRAGMA
                /* 整个 k 循环展开：33 项全部变成独立的乘加。
                 * 并行度不在这里控制，而是用 allocation 限死乘法器个数 ——
                 * 和直算那条路用的是同一把闸，这样才能公平对比。 */
#pragma HLS UNROLL
#endif
                float f = hist[k];
                if (k != N_FOLD - 1) {
                    f += hist[N_TAPS - 1 - k];
                }
                y[k % FIR_PARTIAL] += f * FIR_COEFFS[b][k];
            }
#else
            /* k 每轮跳 FIR_PARTIAL 个，内层 p 把这一组的抽头分给各个部分和 */
            for (int k = 0; k < N_TAPS; k += FIR_PARTIAL) {
#if FIR_PRAGMA
                /* 展开内层：y[p] 的下标变成编译期常量，5 个部分和各自成为
                 * 一组独立的寄存器和加法链，硬件里就不再是一条串行的链。
                 * 不展开的话 y[p] 的下标是变量，HLS 只能把它当一块内存
                 * 一次一步地读写 —— 实测停在 II=2。 */
#pragma HLS UNROLL
#endif
                for (int p = 0; p < FIR_PARTIAL; p++) {
                    y[p] += hist[k + p] * FIR_COEFFS[b][k + p];
                }
            }
#endif

            /* 部分和合并（FIR_PARTIAL=1 时这一步就是恒等，不产生额外加法） */
            float ys = 0.0f;
            for (int p = 0; p < FIR_PARTIAL; p++) {
                ys += y[p];
            }

            acc += drc(ys);
        }
        out[n] = acc;
    }
}
