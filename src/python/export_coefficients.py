import numpy as np
import scipy.signal as signal
import os

# ==========================================
# FIR 滤波器系数导出脚本
# 用途：生成 4 个频段的 65 抽头 FIR 系数，导出为文本文件，供 HLS/RTL 使用
# ==========================================

fs = 48000
numtaps = 65
bands = [(0, 300), (300, 600), (600, 1000), (1000, 8000)]

# 确保导出目录存在
os.makedirs("sim/hls_csim", exist_ok=True)

print("开始生成 FIR 滤波器系数...")

for i, (low, high) in enumerate(bands):
    if low == 0:
        taps = signal.firwin(numtaps, high, fs=fs, pass_zero='lowpass')
        band_name = f"band{i+1}_lowpass_0-{high}Hz"
    elif high == 8000:
        taps = signal.firwin(numtaps, low, fs=fs, pass_zero='highpass')
        band_name = f"band{i+1}_highpass_{low}-8000Hz"
    else:
        taps = signal.firwin(numtaps, [low, high], fs=fs, pass_zero='bandpass')
        band_name = f"band{i+1}_bandpass_{low}-{high}Hz"
    
    # 导出为文本文件（每行一个系数）
    output_file = f"sim/hls_csim/fir_coeffs_{i+1}.txt"
    np.savetxt(output_file, taps, fmt="%.8f")
    print(f"✅ 频段 {i+1} ({band_name}) 系数已导出：{output_file}")

print("🎉 全部 4 组系数导出完成！请将文件发给技术队友，供 HLS/RTL 综合使用。")