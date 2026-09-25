import numpy as np
import os

# ==========================================
# 软硬件结果比对脚本
# 用途：计算 Python 黄金基准与 HLS/RTL 硬件输出的误差
# ==========================================

# 1. 定义文件路径
# Python 黄金参考（软件基线结果，我们之前导出过 test_input.txt，这是输入数据）
# 硬件团队会根据这个输入算出输出，存为 hw_output.txt
python_output_path = "data/audio/test_input.txt"    # 目前用测试数据占位
hw_output_path = "data/results/hw_output.txt"        # 等待技术队友生成

if not os.path.exists(hw_output_path):
    print("⚠️ 硬件输出文件尚未生成，请等 HLS/RTL 测试完成后放入 data/results/ 目录。")
    print("   一旦有了文件，重新运行此脚本即可自动比对。")
    exit()

# 2. 读取两个文件的数据
# 注意：硬件输出的格式可能和 Python 不同，需要根据实际调整
py_data = np.loadtxt(python_output_path, dtype=np.float32)
hw_data = np.loadtxt(hw_output_path, dtype=np.float32)

# 3. 截取相同长度进行对比
min_len = min(len(py_data), len(hw_data))
py_data = py_data[:min_len]
hw_data = hw_data[:min_len]

# 4. 计算误差指标
mse = np.mean((py_data - hw_data) ** 2)         # 均方误差
mae = np.mean(np.abs(py_data - hw_data))        # 平均绝对误差
max_err = np.max(np.abs(py_data - hw_data))     # 最大单点误差

# 计算相关系数（带防除零保护）
if np.std(py_data) > 1e-8 and np.std(hw_data) > 1e-8:
    correlation = np.corrcoef(py_data, hw_data)[0, 1]
else:
    print("⚠️ 提示：数据方差极小（可能是静音段），无法计算相关系数，跳过此项。")
    correlation = 1.0

print("==========================================")
print("📊 软硬件结果比对报告")
print(f"样本数: {min_len}")
print(f"均方误差 (MSE): {mse:.6f}")
print(f"平均绝对误差 (MAE): {mae:.6f}")
print(f"最大单点误差 (Max Error): {max_err:.6f}")
print(f"相关系数 (Correlation): {correlation:.6f}")
print("==========================================")

if correlation > 0.99:
    print("✅ 结论：硬件输出与 Python 基准高度一致，算法正确！")
else:
    print("❌ 结论：误差偏大，请检查硬件逻辑或数据格式。")