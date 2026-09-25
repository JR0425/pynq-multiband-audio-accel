# HLS C 仿真与联调说明书（写给硬件队友）

欢迎来到 Python 与 HLS 的交界处！本目录包含了你在 Vitis HLS 中进行 C 仿真所需的全部素材。

## 📁 文件说明

| 文件名 | 用途 |
| :--- | :--- |
| `test_fir.cpp` | HLS C++ 测试激励（Testbench），包含 FIR 滤波函数和读取数据的 `main` 函数。 |
| `test_input.txt` | Python 导出的 1000 个测试样本（真人语音去直流后的浮点数），作为 C 仿真的输入激励。 |
| `fir_coeffs_1.txt` | 频段 1（0-300Hz）的 65 抽头 FIR 系数。 |
| `fir_coeffs_2.txt` | 频段 2（300-600Hz）的 65 抽头 FIR 系数。 |
| `fir_coeffs_3.txt` | 频段 3（600-1000Hz）的 65 抽头 FIR 系数。 |
| `fir_coeffs_4.txt` | 频段 4（1000-8000Hz）的 65 抽头 FIR 系数。 |

## 🛠️ 在 Vitis HLS 中的使用步骤

1. **新建工程**：在 Vitis HLS 中新建工程，将 `test_fir.cpp` 添加为 Source，并添加一个新文件 `fir_coeffs.h` 用于存放系数数组。
2. **加载系数**：将 `fir_coeffs_1.txt` 等 4 个文件中的 65 个浮点数，分别复制到 `fir_coeffs.h` 中的 4 个 `const float` 数组里。
3. **运行 C 仿真**：直接运行 C Simulation。程序会自动读取 `test_input.txt`，并输出前 10 个滤波结果。
4. **与 Python 结果对比**：将 C 仿真输出的 1000 个结果保存为 `hw_output.txt`，放到 `data/results/` 目录下，运行 `python src/python/compare_results.py` 即可自动比对误差。

## ⚠️ 注意事项
- `test_input.txt` 中的数据是去直流后的浮点数，范围约在 -1.0 到 1.0 之间，HLS 中请使用 `float` 类型处理。
- 后续进行 HLS 优化（数组分割、流水线）时，请保留此 Testbench 不变，以确保输出结果一致。