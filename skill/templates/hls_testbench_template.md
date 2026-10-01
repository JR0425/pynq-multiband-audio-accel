# HLS C++ Testbench 模板

> **这只是最小骨架，不是本项目的真实接口。** 下面这个签名（`float*` 进、整段一次处理）
> 为了好读才简化成这样。本项目真实的核是**分块流式、int16 / Q1.15**：
>
> ```cpp
> void fir_multiband(const int16_t *in, int16_t *out, int length,
>                    const int16_t drc_thr[N_BANDS], const int16_t drc_ratio[N_BANDS],
>                    int reset, int bypass);
> ```
>
> 也就是"每调一次处理一块、靠 `reset` 维持块间延迟线连续"，而且抽头数是**编译期常量**
> 不是参数。真实写法看 `src/hls/fir_tb.cpp`，接口契约看 `report/hardware_interface_spec.md` §一。

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