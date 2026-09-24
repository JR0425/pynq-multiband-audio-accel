import numpy as np
import matplotlib.pyplot as plt
import soundfile as sf
import scipy.signal as signal
import os

audio_path = "data/audio/test_tone.wav"
data, fs = sf.read(audio_path)

# 混合左右声道，去直流
left_channel = data[:, 0] + data[:, 1]
left_channel = left_channel - np.mean(left_channel)

# 设计 600Hz 低通滤波器
taps = signal.firwin(65, 600, fs=fs)
filtered_left = signal.lfilter(taps, 1.0, left_channel)

# 打印能量对比，验证滤波是否生效
print(f"原始信号最大幅度: {np.max(np.abs(left_channel)):.4f}")
print(f"滤波后信号最大幅度: {np.max(np.abs(filtered_left)):.4f}")

# 计算 FFT
n = min(fs, len(left_channel))
freqs = np.fft.fftfreq(n, 1/fs)
fft_orig = np.abs(np.fft.fft(left_channel[:n])) / n * 2
fft_filt = np.abs(np.fft.fft(filtered_left[:n])) / n * 2

plt.figure(figsize=(10, 4))
plt.plot(freqs[:n//2], fft_orig[:n//2], label="Original", color='blue', alpha=0.6)
plt.plot(freqs[:n//2], fft_filt[:n//2], label="FIR Filtered (600Hz)", color='red', linewidth=2)

plt.title("FIR Filter Baseline: Frequency Domain Comparison")
plt.xlabel("Frequency (Hz)")
plt.ylabel("Normalized Magnitude")
plt.xlim(0, 2000)
plt.ylim(0, 1.5) # 限制 Y 轴，防止视觉误判
plt.legend()
plt.grid(True)

os.makedirs("data/figures", exist_ok=True)
plt.savefig("data/figures/fir_filter_comparison.png")
print("✅ 频谱图已保存，请检查！")