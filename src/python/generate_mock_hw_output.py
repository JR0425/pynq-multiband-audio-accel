import numpy as np
import os

# ==========================================
# 模拟硬件输出生成脚本（仅用于测试比对脚本）
# ==========================================

# 1. 读取 Python 的测试输入
input_path = "data/audio/test_input.txt"
data = np.loadtxt(input_path, dtype=np.float32)

# 2. 模拟硬件输出：在原始数据上叠加微小随机噪声，模拟量化误差
# 真实硬件会有一个非常小的误差，这样我们就能测试对比脚本
noise = np.random.normal(0, 1e-5, len(data))
mock_hw_output = data + noise

# 3. 保存为硬件输出文件，放入 data/results/ 目录
os.makedirs("data/results", exist_ok=True)
output_path = "data/results/hw_output.txt"
np.savetxt(output_path, mock_hw_output, fmt="%.8f")
print(f"✅ 模拟硬件输出已生成：{output_path}")
print("现在请运行 compare_results.py 查看比对结果！")