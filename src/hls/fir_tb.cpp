/* 多频段核的 C 仿真测试台（testbench）
 *
 * 它做的三件事：
 *     1. 从文件读入一批 float 采样（默认 data/audio/test_input.txt）
 *     2. 调用核 fir_multiband() 处理
 *     3. 把结果按同样格式写到文件（默认 data/results/hw_output.txt）
 * 然后用 src/python/compare_golden_vs_hw.py 和 Python 黄金参考比对。
 *
 * 两个路径都可以用命令行参数覆盖 —— csim 的工作目录是它自己临时建的一个
 * 文件夹，相对路径到不了仓库，所以 build/hls/run_csim.tcl 会传绝对路径进来。
 *
 * 注意：运行时输出刻意全部用 ASCII。Windows 控制台是 GBK，
 *       中文经 HLS 的 csim 输出出去会变成问号，排查起来是纯浪费时间。
 */

#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <vector>

/* 核本体在 fir_multiband.cpp 里 */
void fir_multiband(const float *in, float *out, int length);

int main(int argc, char **argv) {
    const char *in_path = (argc > 1) ? argv[1] : "data/audio/test_input.txt";
    const char *out_path = (argc > 2) ? argv[2] : "data/results/hw_output.txt";

    std::cout << "[tb] input : " << in_path << std::endl;
    std::cout << "[tb] output: " << out_path << std::endl;

    /* ---- 1. 读输入 ---- */
    std::ifstream fin(in_path);
    if (!fin.is_open()) {
        std::cerr << "[tb] ERROR: cannot open input file." << std::endl;
        return 1;
    }
    std::vector<float> x;
    float v;
    while (fin >> v) {
        x.push_back(v);
    }
    fin.close();

    const int length = (int)x.size();
    if (length <= 0) {
        std::cerr << "[tb] ERROR: input file is empty." << std::endl;
        return 1;
    }
    std::cout << "[tb] samples read: " << length << std::endl;

    /* ---- 2. 跑核 ---- */
    std::vector<float> y(length, 0.0f);
    fir_multiband(x.data(), y.data(), length);

    /* ---- 3. 写输出 ---- */
    std::ofstream fout(out_path);
    if (!fout.is_open()) {
        std::cerr << "[tb] ERROR: cannot open output file for writing." << std::endl;
        return 1;
    }
    fout << std::scientific << std::setprecision(9);
    for (int i = 0; i < length; i++) {
        fout << y[i] << "\n";
    }
    fout.close();

    /* 顺手报几个数，方便肉眼先扫一眼是否离谱
     * （比如全 0、或者量级差好几个数量级） */
    float ymax = 0.0f;
    for (int i = 0; i < length; i++) {
        float a = (y[i] < 0.0f) ? -y[i] : y[i];
        if (a > ymax) ymax = a;
    }
    std::cout << "[tb] first 5 outputs: ";
    for (int i = 0; i < 5 && i < length; i++) {
        std::cout << y[i] << " ";
    }
    std::cout << std::endl;
    std::cout << "[tb] max |output| = " << ymax << std::endl;
    std::cout << "[tb] DONE" << std::endl;
    return 0;
}
