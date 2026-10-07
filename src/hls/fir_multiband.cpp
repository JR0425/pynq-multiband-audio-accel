/* 多频段音频处理核 —— HLS 版本
 *
 * 做什么：把一路音频分成 4 个频段，各自做动态范围压缩（DRC），再合回去。
 *          这是助听器里最核心的一步：不同频段的听力损失不一样，得分开处理。
 *
 * ============================ ★ 频段怎么分 ============================
 *
 * 相减式（subtractive / derived crossover）—— 不"每段各自设计一个带通"，
 * 而是**只设计 3 个低通，相邻两段相减**：
 *
 *     y1 = FIR(hist, c_LP500)      b1 = y1               0    ~ 500  Hz
 *     y2 = FIR(hist, c_LP1000)     b2 = y2 - y1          500  ~ 1000 Hz
 *     y3 = FIR(hist, c_LP2000)     b3 = y3 - y2          1000 ~ 2000 Hz
 *                                  b4 = hist[D] - y3     2000 ~ 24k  Hz
 *
 * 为什么换（每条都有实测，全过程见 桌面\PYNQ音频项目\频段划分方案_调研与建议.md）：
 *
 *   · **各段之和恒等于原信号**。Σb = y1 + (y2-y1) + (y3-y2) + (hist[D]-y3) = hist[D]。
 *     不做压缩时输出**逐位等于延迟 D 拍的输入**（实测重建误差 −309 dB，就是浮点精度）。
 *     旧写法（每段各自 firwin）在同一份语音上重建误差 **+3.2 dB** ——
 *     还没压缩，声音就已经变了。这是换结构最主要的动机。
 *   · **直流不漏音**。每段是两个低通之差，在 DC 和 Nyquist 处恒为零。
 *     旧写法的 300–600 Hz 带通在直流有 **+1.3 dB**（本该是零）。
 *   · **滤波器从 4 个降到 3 个**。第 4 段的"全通"不需要滤波器，见下。
 *   · **四段延迟天然一致**（都是 D），不需要额外对齐线。靠的是两条：
 *     系数严格对称（Type I 线性相位）+ 各段等长。
 *
 * ⚠️ 代价一：各段**必须等长**。以后想给高频段用短滤波器省资源，会把重建弄坏。
 * ⚠️ 代价二：压缩之后 ΣDRC(b_i) 不再等于输入（旧写法也一样），响信号可能超量程。
 *    这里靠 samp_out 的饱和兜住，但"压缩后整体变大"这件事本身没解决 ——
 *    要真正解决得加一级总增益或限幅器。现在没有，记在风险里。
 *
 * ---- 第 4 段为什么不用额外延迟线 ----
 *     hist[D] 就是 D 拍前那个输入（hist[0] 最新，hist[k] 是第 k 拍前），
 *     它本来就在抽头延迟线上。所以第 4 段是**白拿**的。
 *     D = (N_TAPS-1)/2 = FIR_GROUP_DELAY，编译期常量，取的是一个固定下标，
 *     不产生任何选择器。
 *
 * ---- 抽头数 ----
 *     N_TAPS 是**编译期常量**，不是寄存器（见 fir_coeffs.h）。
 *     它和边界一起决定段间分离度：实测 193 抽头时三条边界的残留都 ≤ −21 dB，
 *     65 抽头最差只有 −6 dB。根本原因是过渡带宽度 ≈ 3.3·fs/N：
 *     48 kHz 下 65 抽头就是 2437 Hz，比 0~1000 Hz 这整块还宽 ——
 *     低频前三段分不开**不是边界选错了，是抽头数不够**。
 *     能不能用 193 要综合说了算（数据通路变成 2.23 倍）。
 *
 * ======================= 下面是原来的结构说明 =======================
 *
 * 与 Python 基线的对应关系（必须逐点对齐，否则"加速比"无从谈起）：
 *     src/python/multiband_baseline.py 里的
 *         signal.lfilter(taps, 1.0, x)     ->  下面的 FIR 抽头延迟线
 *         signal.lfilter 的相减            ->  下面的 b1..b4
 *         np.where(...)                    ->  下面的 drc()
 *         output += compressed             ->  下面的 acc
 *     系数来自同一批文件（sim/hls_csim/lp_{edge}_n{N}.txt），不是重新设计的。
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
 *     于是每 6 拍才喂得进一个新抽头。
 *     实测（v0 基线）：每个采样 1661 拍，而 48 kHz 实时只给 20.8 us = 2083 拍。
 *     —— 也就是说**能跑，但几乎没有余量**，而且这条链会卡死后面所有优化。
 *
 *     破法就是把一个累加器拆成 FIR_PARTIAL 个"部分和"，各加各的，最后再合并。
 *
 *     ⚠️ 这**改变了加法的结合顺序**，结果有 1e-7（浮点）/ 1e-9（定点）量级的差异
 *        （和 Python 的 float64 黄金参考比仍在这个量级，不是算错了）。
 *        FIR_PARTIAL=1 时退化成原来的单一累加器，**与优化前的加法顺序完全相同** ——
 *        "优化前"和"优化后"因此可以用同一份源码跑出来，是可复现的对比。
 *        编译期用 add_files -cflags "-DFIR_PARTIAL=<n>" 切换。
 *
 *     ⚠️ 这个分组要求 N_TAPS_ALLOC 能被 FIR_PARTIAL 整除。193 是质数，不能被 5 整除，
 *        所以系数表在尾巴上补了 0（见 fir_coeffs_n193.h）—— 补零点不改变数值，
 *        省掉了循环里那个 `if (k+p < N_TAPS)`，也就不会有"展开后下标越界"的风险。
 *
 * ---- FIR_FOLD：利用系数对称性做"折叠"，把乘法次数砍掉一半 ----
 *     系数严格对称（c[k] == c[N_TAPS-1-k]，生成脚本每次都核一遍），因为
 *     firwin 生成的线性相位 FIR 必然对称。既然两头乘的是同一个数，就可以先加再乘：
 *
 *         c[k]*h[k] + c[N-1-k]*h[N-1-k]   ==   c[k] * ( h[k] + h[N-1-k] )
 *
 *     代价是多一次加法；FPGA 上加法用普通逻辑格子，乘法要吃掉稀缺的 DSP
 *     （板上只有 220 个），所以这笔换算看着划算。
 *
 *     ⚠️ 实测结论：**在 4 频段那版结构下不划算，默认关闭。**
 *        同为"乘法器上限 5"：直算 327 拍 / 27 DSP，折叠 1247 拍 / 25 DSP。
 *        把 hist 拆成 65 个寄存器能救回一些（663 拍），面积却涨到 79 DSP / 19924 LUT。
 *        原因不是读口不够，而是**瓶颈根本不在乘法**：这条线的瓶颈是浮点加法的
 *        延迟链，乘法靠 allocation 限流就能复用 —— 折叠减掉的是不紧张的资源，
 *        加的却是最紧张的那个。（旁证：乘法器上限从 5 提到 10，拍数一动不动。）
 *
 *     保留这个开关是因为"试过、量过、知道为什么不划算"本身就是结论，
 *     而且**定点化之后加法变便宜了，这个结论值得重测一遍** ——
 *     193 抽头 × 3 个低通 = 579 次乘加，DSP 够不够正是这次要回答的问题。
 *
 *     0 = 直算（与折叠前逐字相同，用于做对照），1 = 折叠。默认 0。
 *
 * ---- 硬件 pragma 的总开关 ----
 *     0 = 只改结构、不展开不拆分；1 = 把并行度真的做进硬件里。
 *     分成两个开关是为了让"改了结构"和"又加了 pragma"两版能分别综合出来对比 ——
 *     否则会分不清提速是哪一步带来的。
 *     ⚠️ 打开时假设 FIR_PARTIAL == 5（pragma 里的因子写死成 5），
 *        HLS 的 pragma 里写不了宏，因子只能写死。
 *
 * ---- FIR_FIXED：把数据通路从浮点换成定点 ----
 *     浮点加法电路（fadd）本身 5 拍延迟，是浮点那条线所有瓶颈的根源；
 *     定点加法只要 1 拍。而且浮点乘法在 FPGA 上要吃掉好几个 DSP。
 *     代价是精度：采样 16 位 / 系数 18 位 → 实测 SNR 77.1 dB。
 *     判据用信噪比（SNR，dB）而不是"误差小于多少" —— 音频里 SNR 才是行话，
 *     而且它和位宽的关系是线性的（每多 1 位约 +6 dB），一眼能看出够不够。
 *
 * ---- 位宽怎么定的 ----
 *   采样  [-0.55, 0.55]，输出 [-0.70, 0.92]  → 都在 [-1,1) 里，用 Q1.15（16 位）
 *   系数  最大 0.083、最小非零 1.14e-4（差 700 倍）→ 16 位不够，给 18 位
 *   累加  最多 193 个乘积之和，最坏情况放大 193 倍 → 留 8 位整数位、共 40 位
 *   ⚠️ 位宽的真实成本第一次量的时候被搞错了：第一版把系数表写成 `const float`、
 *      在循环里再转成定点，HLS 没有在综合时折掉这个转换，而是放了 65 个
 *      float→double 扩位器，系数表从 8 块 BRAM 涨到 128 块，LUT 炸到 123550（232%）。
 *      → 正确做法是让系数表本身就是定点类型（见 fir_coeffs.h + fir_types.h）。
 *
 * 0 = 浮点（只用来做对照/回归，不上板），1 = 定点。
 * 位宽用 -DFIR_DW= / -DFIR_CW= / -DFIR_ACC_W= 单独调，默认 16 / 18 / 40。
 * 类型定义在 fir_types.h 里（系数表也要用它，所以抽出去公用）。
 *
 * ⚠️ 定点版**不能用本机 g++ 先验语法** —— ap_fixed.h 是 Xilinx 的头文件，
 *    g++ 没有。这条只能交给 HLS 编。
 */
#include "fir_coeffs.h"

/* ---- 部分和的个数 ----
 * 1 = 单一累加器（等价于优化前的写法）；越大越并行、越费乘法器。 */
#ifndef FIR_PARTIAL
#define FIR_PARTIAL 5
#endif

#if (FIR_PARTIAL != 1) && ((N_TAPS_ALLOC % FIR_PARTIAL) != 0)
#error "FIR_PARTIAL 必须整除 N_TAPS_ALLOC（表已按 5 补齐，所以 1 和 5 都行）"
#endif

#ifndef FIR_FOLD
#define FIR_FOLD 0
#endif

/* 移位循环的写法开关（见下面 for(n) 里的长注释）：
 * 0 = 直写 `hist[k] = hist[k-1]`（有环依赖，编译器只能一拍一格地跑）
 * 1 = 改成 `prev` 串接写法，语义完全相同，但能被展成真正的移位寄存器 */
#ifndef FIR_SHIFT_CHAIN
#define FIR_SHIFT_CHAIN 0
#endif

#if FIR_FOLD
/* 折叠后要算多少项：N_TAPS/2 对 + 1 个中心抽头 */
#define N_FOLD ((N_TAPS + 1) / 2)
#endif

/* 硬件 pragma 的总开关（0 = 只改结构、不展开不拆分；1 = 把并行度做进硬件里） */
#ifndef FIR_PRAGMA
#define FIR_PRAGMA 1
#endif

/* 单频段动态范围压缩：小信号原样过，超过阈值的部分按 ratio 的比例压下来。
 * 与 np.where(np.abs(f) > thr, np.sign(f) * (thr + (np.abs(f)-thr)*ratio), f) 等价。
 * 写成 drc_t 是为了浮点/定点共用同一份代码 —— 定点下这几种运算的写法是一样的。
 *
 * 阈值和压缩比现在是**运行期参数**（来自 AXI 寄存器），不再是宏：
 * 助听器验配就是"按人调这几个数"，写死在核里的话每换一个参数都要重新综合。
 * 两种运算都是"比较 + 乘"，不需要除法 —— 所以参数化不花额外代价。
 *
 * ⚠️ 参数类型是 drc_t（不是 acc_t）—— 这是**故意的，不是随手**。
 *    DRC 的输入是单个频段，量级在 ±2 附近（相减式下是两段之差，最大到 ±2），
 *    用 40 位的累加器类型去算它，会合出一个 40x40 的乘法器（实测 33x33），
 *    这块逻辑 HLS 塞不进 DSP，全部落进 LUT 里。换成 24 位之后小一个数量级。
 *    drc_t 的整数位是 2 位，正好装得下 ±2 —— 不是凑的，是按这个上限定的。
 *
 * ⚠️ 取绝对值不要写成三元表达式 `(x<0) ? -x : x`：定点下 `-x` 会比 `x` 宽一位，
 *    三元表达式要求两个分支类型相同，会直接编译不过（实测报
 *    "operands to ?: have different types"）。拆成 if 赋值就没这个问题 ——
 *    窄类型接收宽结果是允许的，这正是量化发生的地方。 */
static drc_t drc(drc_t x, drc_t thr, drc_t ratio) {
    drc_t a = x;
    if (x < (drc_t)0) {
        a = -x;
    }
    if (a <= thr) {
        return x;
    }
    drc_t sign = (drc_t)1;
    if (x < (drc_t)0) {
        sign = (drc_t)(-1);
    }
    return sign * (thr + (a - thr) * ratio);
}

/* 处理 length 个采样：in -> out。输入输出等长、一对一，没有内部缓冲，所以可以流式做。
 *
 * ⚠️ 抽头延迟线在**块与块之间保持**，不在每次调用时清零。
 *    分块处理时每块开头清零，会在块边界炸出"咔"声 —— 那是每一块都从头开始
 *    卷一波瞬态。整段处理就在开头发一次 reset；流式处理只在开始时发一次。
 *    两种用法同一份硬件，靠"什么时候发 reset"区分。
 *
 * `bypass`：非 0 = 跳过压缩，输出就是各段之和。
 *    它有两个用处，都不是测试专用的：
 *      · 上板演示"加压缩 / 不加压缩"的对比听感 —— 助听器验配现场就是这么做的；
 *      · 直通检验：各段之和恒等于延迟 D 拍的输入（文件头那条性质），
 *        所以 bypass 时输出必须**逐位等于**输入延后 D 拍。
 *        这是唯一能把"结构接错了"和"量化误差大了"分开的检查。
 *    想靠"把阈值设到最大"来绕过压缩是**不行的**：阈值是 Q1.15，
 *    最大只能到 32767/32768，而相减式下 b_i 是两段之差、幅度能到 ±2，
 *    照样会落进压缩支路 —— 必须有一个真正的旁路。
 *    代价：每段多一个二选一（4 个），可以忽略。 */
void fir_multiband(const int16_t *in, int16_t *out, int length,
                   const int16_t drc_thr[N_BANDS],
                   const int16_t drc_ratio[N_BANDS],
                   int reset,
                   int bypass) {
    /* 抽头延迟线：hist[0] 是最新采样，hist[N_TAPS_ALLOC-1] 是最旧的。
     * 长度用 N_TAPS_ALLOC（补齐后的），尾巴上那几格配的是 0 系数，纯占位。
     * static —— 块间保持，理由见上。 */
    static data_t hist[N_TAPS_ALLOC];
#if FIR_PRAGMA
    /* 按 5 路循环拆分（cyclic），与 FIR_PARTIAL 对齐：
     * 第 k 个抽头固定落在第 (k % 5) 路。这样内层 p 循环展开之后，
     * hist[k+p] 的"落在哪一路"是编译期就知道的常量，不需要任何选择器。
     * 如果改用 -type complete（每格一个寄存器），索引会变成 193 选 1 的大选择器，
     * 面积会失控；用 block 则跨块移动会变多。
     * ⚠️ pragma 里写不了宏（HLS 不展开），因子只能写死成数字，所以这里用
     *    条件编译给出几种写法，靠编译开关切换 —— 用于排查"折叠后读口不够"的猜想：
     *      -DFIR_HIST_CYC13  拆成 13 路
     *      -DFIR_HIST_FULL   完全拆开（每格一个独立寄存器）
     *     默认（都不给）保持 5 路。 */
#if defined(FIR_HIST_FULL)
#pragma HLS ARRAY_PARTITION variable=hist complete dim=1
#elif defined(FIR_HIST_CYC13)
#pragma HLS ARRAY_PARTITION variable=hist cyclic factor=13 dim=1
#else
#pragma HLS ARRAY_PARTITION variable=hist cyclic factor=5 dim=1
#endif
#endif

    if (reset) {
        for (int k = 0; k < N_TAPS_ALLOC; k++) {
#if FIR_PRAGMA
#pragma HLS UNROLL
#endif
            hist[k] = (data_t)0;
        }
    }

    /* DRC 参数：寄存器里是 Q1.15 的整数，drc_t 是 Q2.22。
     * 这一步只是"重新解释小数点在哪"，定点下 samp_in 就是贴一下比特，
     * 硬件里一根线都不花。 */
    drc_t thr[N_BANDS];
    drc_t ratio[N_BANDS];
#pragma HLS ARRAY_PARTITION variable=thr complete dim=1
#pragma HLS ARRAY_PARTITION variable=ratio complete dim=1
    for (int i = 0; i < N_BANDS; i++) {
#if FIR_PRAGMA
#pragma HLS UNROLL
#endif
        thr[i]   = (drc_t)samp_in(drc_thr[i]);
        ratio[i] = (drc_t)samp_in(drc_ratio[i]);
    }

    for (int n = 0; n < length; n++) {
        /* 整体右移一格，把新采样放到最前面。
         *
         * ! 这里**不是**"寄存器之间接几根线"那么便宜 —— 实测它是整个核的瓶颈。
         *   csynth 报告里这个循环 achieved II = 2、194 次迭代、**398 拍**，
         *   而 3 个低通加起来只要 11 拍。也就是每采样 450 拍里，
         *   **398 拍（88%）花在这个移位上**。
         *   原因：hist 同时被 `ARRAY_PARTITION cyclic factor=5` 拆进 5 个 RAM，
         *   跨 RAM 边界搬数据要串行，一层 `#pragma HLS UNROLL` 解决不了 ——
         *   原来那句"从 192 拍降到 1 拍"的假设是错的。
         *   （v13 / v18 两版报告里这三个数完全一样，见 data/results/impl_metrics.md。）
         *
         *   要改的话标准做法是**环形缓冲区 + 转动的读下标**，每采样零搬运。
         *   没做 —— 设计已定案，改它要连带重跑综合、时序和全套上板验证。 */
#if FIR_SHIFT_CHAIN
        /* ---- 移位写成"串起来的临时变量"（FIR_SHIFT_CHAIN=1）----
         * 和下面那段**语义完全相同**（hist[k] 一律取旧的 hist[k-1]），
         * 但写法让 HLS 能真的把它展成移位寄存器：
         *
         *   展开前：hist[k] = hist[k-1] —— 有环依赖（distance 1），
         *           C 语义要求按顺序执行，**编译器必须拒绝展开**
         *           （日志：`II Violation ... carried dependence constraint`）。
         *           所以它只能一拍一格地跑：194 次移位 = 194 拍。
         *   展开后：依赖只落在标量 prev 上，展平之后就是**一根根的线**，
         *           零逻辑、零拍数 —— 这才是"寄存器之间接几根线"的原意。
         *
         * ⚠️ 前提是 hist 必须全在寄存器里（配 -DFIR_HIST_FULL）。 */
        {
            data_t prev = samp_in(in[n]);
            for (int k = 0; k < N_TAPS_ALLOC; k++) {
#if FIR_PRAGMA
#pragma HLS UNROLL
#endif
                data_t old = hist[k];
                hist[k] = prev;
                prev = old;
            }
        }
#else
#if FIR_PRAGMA
#pragma HLS UNROLL
#endif
        for (int k = N_TAPS_ALLOC - 1; k > 0; k--) {
            hist[k] = hist[k - 1];
        }
        /* 入参是 int16/Q1.15，hist 是 Q1.15 —— 定点下这一步是**贴比特**，
         * 不是数值换算，硬件里零成本（见 fir_types.h 的 samp_in）。
         * 浮点模式下这里才是"量化发生的地方"。 */
        hist[0] = samp_in(in[n]);
#endif

        /* ---- 3 个低通各自算一遍 ---- */
        acc_t ylp[N_LP];
#pragma HLS ARRAY_PARTITION variable=ylp complete dim=1
        for (int b = 0; b < N_LP; b++) {
            /* FIR_PARTIAL 个部分和：每个负责 1/FIR_PARTIAL 的抽头。
             * 它们之间没有依赖，硬件里可以同时算 —— 这就是并行度的来源。 */
            acc_t y[FIR_PARTIAL];
            for (int p = 0; p < FIR_PARTIAL; p++) {
                y[p] = (acc_t)0;
            }

#if FIR_FOLD
            /* ---- 折叠结构：先加、后乘 ----
             * 每一项 k 代表"一对"：h[k] 和 h[N_TAPS-1-k]，它们乘的是同一个系数。
             * 最后一项（k == N_FOLD-1）是正中间那个，没有配对的，单独算。
             * 展开之后 "k != N_FOLD-1" 这个判断是编译期常量，硬件里不产生任何比较器。 */
            for (int k = 0; k < N_FOLD; k++) {
#if FIR_PRAGMA
                /* 整个 k 循环展开：97 项全部变成独立的乘加。
                 * 并行度不在这里控制，而是用 allocation 限死乘法器个数 ——
                 * 和直算那条路用的是同一把闸，这样才能公平对比。 */
#pragma HLS UNROLL
#endif
                data_t f = hist[k];
                if (k != N_FOLD - 1) {
                    f += hist[N_TAPS - 1 - k];
                }
                y[k % FIR_PARTIAL] += (acc_t)(f * FIR_LP[b][k]);
            }
#else
            /* k 每轮跳 FIR_PARTIAL 个，内层 p 把这一组的抽头分给各个部分和 */
            for (int k = 0; k < N_TAPS_ALLOC; k += FIR_PARTIAL) {
#if FIR_PRAGMA
                /* 展开内层：y[p] 的下标变成编译期常量，各部分和各自成为
                 * 一组独立的寄存器和加法链，硬件里就不再是一条串行的链。
                 * 不展开的话 y[p] 的下标是变量，HLS 只能把它当一块内存
                 * 一次一步地读写 —— 实测停在 II=2。 */
#pragma HLS UNROLL
#endif
                for (int p = 0; p < FIR_PARTIAL; p++) {
                    /* ⚠️ 两个操作数**不能**先转成 acc_t 再乘。
                     *    原来写的是 (acc_t)hist * (acc_t)coef，两边都撑到 40 位，
                     *    HLS 就去综合 40×40 的乘法器 —— 实测出来是 65 个
                     *    `mul_33s_33s_65`，**一个 DSP 都用不上**，全落在逻辑格子里，
                     *    LUT 因此下不来。
                     *    改成在各自的原始位宽上乘（16 位 × 18 位，正好塞进
                     *    一个 DSP48 的 25×18 输入），乘完再把积转进累加器 ——
                     *    量化仍然发生在"接收"这一步，数值结果完全一样。
                     *    系数是编译期常量，它在硬件里只是一张表，
                     *    所以系数位数不花运算代价，只花存储和布线。 */
                    acc_t prod = (acc_t)(hist[k + p] * FIR_LP[b][k + p]);
                    y[p] += prod;
                }
            }
#endif

            /* 部分和合并（FIR_PARTIAL=1 时这一步就是恒等，不产生额外加法） */
            acc_t ys = (acc_t)0;
            for (int p = 0; p < FIR_PARTIAL; p++) {
                ys += y[p];
            }
            ylp[b] = ys;
        }

        /* ---- 相减式：3 个低通 -> 4 段 ----
         * 减法在 acc_t 上是精确的（Q8.32 的小数位够多，加减不产生舍入），
         * 所以不压缩时 Σb 精确等于 hist[D] —— 这就是"重建误差 −309 dB"的来源。
         * 第 4 段直接取 hist[D]，不加延迟线（见文件头）。 */
        acc_t band[N_BANDS];
#pragma HLS ARRAY_PARTITION variable=band complete dim=1
        band[0] = ylp[0];
        band[1] = ylp[1] - ylp[0];
        band[2] = ylp[2] - ylp[1];
        band[3] = (acc_t)hist[FIR_GROUP_DELAY] - ylp[2];

        /* 4 段各自压缩，再相加。压缩后才会有"总幅度超过输入"的可能 ——
         * 最后一步的饱和是兜底，不是正常路径。 */
        acc_t acc = (acc_t)0;
        for (int i = 0; i < N_BANDS; i++) {
            if (bypass) {
                acc += band[i];
            } else {
                acc += (acc_t)drc((drc_t)band[i], thr[i], ratio[i]);
            }
        }
        /* 出参 int16/Q1.15：定点下 (data_t)acc 就是截断到 16 位（带饱和），
         * samp_out 只是把那串比特当整数交出去。 */
        out[n] = samp_out((data_t)acc);
    }
}
