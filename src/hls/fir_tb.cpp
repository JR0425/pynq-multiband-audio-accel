/* 多频段核的 C 仿真测试台（testbench）
 *
 * 它做的四件事：
 *     1. 从文件读入一批浮点采样（默认 data/audio/test_input.txt），量化成 int16/Q1.15
 *     2. 用**实际参数**跑一遍核 -> data/results/hw_output.txt（浮点，给 SNR 比对）
 *     3. 用**压缩比 = 1.0**（等于不做压缩）再跑一遍 -> data/results/hw_transparent_i16.txt
 *     4. 打印直通检验：第 2 遍的输出应当逐位等于延迟 D 拍的输入
 *
 * 第 3 步为什么重要：
 *     相减式结构有一条可以**精确验证**的性质 —— 不做压缩时，各段之和恒等于
 *     延迟 D 拍的输入（这是"重建误差 −309 dB"的来源）。所以把压缩比设成 1.0，
 *     输出就必须和输入**逐位相同**（只差一个延迟）。
 *     "逐位相同"比"相关系数 0.999"硬得多：它是结构对不对的直接证据，
 *     而且能把"结构错了"和"量化误差大了"两件事分开。
 *
 * 三个路径都可以用命令行参数覆盖 —— csim 的工作目录是它自己临时建的一个
 * 文件夹，相对路径到不了仓库，所以 build/hls/run_csim.tcl 会传绝对路径进来。
 *
 * 注意：运行时输出刻意全部用 ASCII。Windows 控制台是 GBK，
 *       中文经 HLS 的 csim 输出出去会变成问号，排查起来是纯浪费时间。
 */

#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <vector>

#include "fir_coeffs.h"   /* N_TAPS / N_BANDS / FIR_GROUP_DELAY */

/* 核本体在 fir_multiband.cpp 里，是另一个编译单元，这里必须自己声明一遍。
 * ⚠️ 签名要和 fir_multiband.cpp 里的**逐字一致** —— 对不上时编译器不会报错
 *    （C 的连接不检查参数类型），会在运行期变成莫名其妙的数。
 *    所以参数个数/类型改一处就必须改两处。 */
void fir_multiband(const int16_t *in, int16_t *out, int length,
                   const int16_t drc_thr[N_BANDS],
                   const int16_t drc_ratio[N_BANDS],
                   int reset,
                   int bypass);

#define Q15_MAX 32767
#define Q15_MIN (-32768)

/* 浮点 -> int16/Q1.15：四舍五入 + 饱和。
 * 硬件上这一步是 ADC / I2S 收线上做的，核里没有；测试台必须自己补上，
 * 否则"核的输入"和黄金参考的输入不是同一串比特。 */
static int16_t to_q15(float x) {
    float v = x * 32768.0f;
    if (v > (float)Q15_MAX) v = (float)Q15_MAX;
    if (v < (float)Q15_MIN) v = (float)Q15_MIN;
    return (int16_t)(v + (v >= 0.0f ? 0.5f : -0.5f));
}

int main(int argc, char **argv) {
    const char *in_path  = (argc > 1) ? argv[1] : "data/audio/test_input.txt";
    const char *out_path = (argc > 2) ? argv[2] : "data/results/hw_output.txt";
    const char *tr_path  = (argc > 3) ? argv[3] : "data/results/hw_transparent_i16.txt";

    std::cout << "[tb] input      : " << in_path << std::endl;
    std::cout << "[tb] output     : " << out_path << std::endl;
    std::cout << "[tb] transparent: " << tr_path << std::endl;
    std::cout << "[tb] N_TAPS=" << N_TAPS << " N_BANDS=" << N_BANDS
              << " D=" << FIR_GROUP_DELAY << std::endl;

    /* ---- 1. 读输入并量化 ---- */
    std::ifstream fin(in_path);
    if (!fin.is_open()) {
        std::cerr << "[tb] ERROR: cannot open input file." << std::endl;
        return 1;
    }
    std::vector<int16_t> x;
    float v;
    while (fin >> v) {
        x.push_back(to_q15(v));
    }
    fin.close();

    const int length = (int)x.size();
    if (length <= 0) {
        std::cerr << "[tb] ERROR: input file is empty." << std::endl;
        return 1;
    }
    std::cout << "[tb] samples read: " << length << std::endl;

    std::vector<int16_t> y(length, 0);

    /* ---- 2. 实际参数跑一遍 ----
     * 阈值/压缩比是运行期参数（板上来自 AXI 寄存器）。
     * 默认值 0.1 / 0.7 在 Q1.15 下是 3277 / 22938 —— 和 Python 基线一致。 */
    const int16_t thr[N_BANDS]   = {3277, 3277, 3277, 3277};
    const int16_t ratio[N_BANDS] = {22938, 22938, 22938, 22938};
    fir_multiband(x.data(), y.data(), length, thr, ratio, 1 /* reset */, 0 /* bypass */);

    std::ofstream fout(out_path);
    if (!fout.is_open()) {
        std::cerr << "[tb] ERROR: cannot open output file for writing." << std::endl;
        return 1;
    }
    fout << std::scientific << std::setprecision(9);
    for (int i = 0; i < length; i++) {
        fout << (float)y[i] / 32768.0f << "\n";
    }
    fout.close();

    /* ---- 3. 直通检验：bypass = 1，跳过压缩 ----
     * 各段之和恒等于延迟 D 拍的输入（见文件头），所以输出应当**逐位等于**输入。
     * 阈值/压缩比在这种模式下不参与运算，随便给。 */
    const int16_t thr_any[N_BANDS]   = {3277, 3277, 3277, 3277};
    const int16_t ratio_any[N_BANDS] = {22938, 22938, 22938, 22938};
    std::vector<int16_t> t(length, 0);
    fir_multiband(x.data(), t.data(), length, thr_any, ratio_any, 1 /* reset */, 1 /* bypass */);

    std::ofstream ftr(tr_path);
    if (!ftr.is_open()) {
        std::cerr << "[tb] ERROR: cannot open transparent output for writing." << std::endl;
        return 1;
    }
    for (int i = 0; i < length; i++) {
        ftr << (int)t[i] << "\n";
    }
    ftr.close();

    /* 自己先扫一遍位移，把结论直接印出来 —— 免得每次都去 Python 里现算。
     * ⚠️ 比对的窗口对每个位移必须**一样长**：直接比 t[i] 和 x[i-lag] 的话，
     *    位移越大参与比对的点越少、错配数天然越小，结论会偏向大位移。
     *    所以固定从 max_lag 开始比。 */
    const int    max_lag = 2 * FIR_GROUP_DELAY + 8;
    int          win_end = length - 1;
    if (win_end > max_lag + 2000) win_end = max_lag + 2000;
    int  best_lag  = -1;
    long best_bad  = -1;
    for (int lag = 0; lag <= max_lag && lag < length; lag++) {
        long bad = 0;
        for (int i = max_lag; i <= win_end; i++) {
            if (t[i] != x[i - lag]) bad++;
        }
        if (best_bad < 0 || bad < best_bad) {
            best_bad = bad;
            best_lag = lag;
        }
    }
    std::cout << "[tb] transparent check: best lag = " << best_lag
              << " (expect D = " << FIR_GROUP_DELAY << "), mismatches = "
              << best_bad << " / " << (win_end - max_lag + 1) << std::endl;

    /* ---- 4. 顺手报几个数，方便肉眼先扫一眼是否离谱 ---- */
    int16_t ymax = 0;
    for (int i = 0; i < length; i++) {
        int a = (y[i] < 0) ? -(int)y[i] : (int)y[i];
        if (a > ymax) ymax = (int16_t)a;
    }
    std::cout << "[tb] first 5 outputs (int16): ";
    for (int i = 0; i < 5 && i < length; i++) {
        std::cout << (int)y[i] << " ";
    }
    std::cout << std::endl;
    std::cout << "[tb] max |output| = " << (int)ymax << " / 32767" << std::endl;
    std::cout << "[tb] DONE" << std::endl;
    return 0;
}
