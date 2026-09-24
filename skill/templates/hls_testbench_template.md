# HLS C++ Testbench 模板

```cpp
#include <iostream>
#include <fstream>
#include <vector>

void fir_filter(float *input, float *output, float *taps, int length) {
    for (int i = 0; i < length; i++) {
        float acc = 0.0;
        for (int j = 0; j < N_TAPS; j++) {
            if (i - j >= 0) acc += input[i - j] * taps[j];
        }
        output[i] = acc;
    }
}

int main() {
    // 读取 Python 导出的文本数据
    // 对比 Python 基线的输出结果
}