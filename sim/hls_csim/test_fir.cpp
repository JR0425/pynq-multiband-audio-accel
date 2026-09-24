#include <iostream>
#include <fstream>
#include <cmath>
#include <vector>

// 定义 FIR 滤波器抽头数
#define N_TAPS 65

// 简单的 FIR 滤波函数（HLS 队友会把这个函数改造为硬件加速核）
void fir_filter(float *input, float *output, float *taps, int length) {
    for (int i = 0; i < length; i++) {
        float acc = 0.0;
        for (int j = 0; j < N_TAPS; j++) {
            if (i - j >= 0) {
                acc += input[i - j] * taps[j];
            }
        }
        output[i] = acc;
    }
}

int main() {
    // 1. 读取 Python 生成的测试数据
    std::ifstream infile("../../data/audio/test_input.txt");
    if (!infile.is_open()) {
        std::cerr << "找不到输入文件 test_input.txt，请先运行 Python 生成脚本！" << std::endl;
        return 1;
    }

    std::vector<float> input;
    float val;
    while (infile >> val) {
        input.push_back(val);
    }
    infile.close();
    
    int length = input.size();
    std::cout << "成功读取测试数据，样本数：" << length << std::endl;

    // 2. 模拟 FIR 滤波器系数（实际使用时会由 Python 生成）
    // 这里硬编码一个简单的 65 抽头全通滤波器作为占位，队友会替换成真实的
    std::vector<float> taps(N_TAPS, 0.0f);
    taps[N_TAPS/2] = 1.0f; 

    // 3. 执行滤波
    std::vector<float> output(length, 0.0f);
    fir_filter(input.data(), output.data(), taps.data(), length);

    // 4. 输出前 10 个结果，供验证比对
    std::cout << "C++ 仿真输出（前 10 个样本）：" << std::endl;
    for (int i = 0; i < 10 && i < length; i++) {
        std::cout << "Sample " << i << ": " << output[i] << std::endl;
    }

    std::cout << "✅ C++ 仿真完成，请与 Python 基线输出进行比对。" << std::endl;
    return 0;
}