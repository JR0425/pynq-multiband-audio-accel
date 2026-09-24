import numpy as np
import scipy.signal as signal
import soundfile as sf
import matplotlib.pyplot as plt
import os

# 1. 读取音频
audio_path = "data/audio/real_voice.wav"
data, fs = sf.read(audio_path)
print(f"1. 原始读取长度：{len(data)/fs:.2f} 秒")

# 2. 混合声道并去直流
if len(data.shape) > 1:
    x = data[:, 0] + data[:, 1]
else:
    x = data.copy()
x = x - np.mean(x)
print(f"2. 去直流后长度：{len(x)/fs:.2f} 秒")

# 3. 滤波器设计
bands = [(0, 300), (300, 600), (600, 1000), (1000, 8000)]
output = np.zeros_like(x)

# 4. 滤波与 DRC 压缩
for low, high in bands:
    if low == 0: taps = signal.firwin(65, high, fs=fs, pass_zero='lowpass')
    elif high == 8000: taps = signal.firwin(65, low, fs=fs, pass_zero='highpass')
    else: taps = signal.firwin(65, [low, high], fs=fs, pass_zero='bandpass')
    
    filtered = signal.lfilter(taps, 1.0, x)
    # DRC 压缩算法
    compressed = np.where(np.abs(filtered) > 0.1, 
                          np.sign(filtered) * (0.1 + (np.abs(filtered) - 0.1) * 0.7), 
                          filtered)
    output += compressed

print(f"3. 最终输出长度：{len(output)/fs:.2f} 秒")
sf.write("data/audio/multiband_output.wav", output, fs)
print("✅ 音频已成功保存！")

# 5. 画图（严格使用切片副本，绝不污染原变量）
n = min(fs, len(x)) # 只取前1秒画图
freqs = np.fft.fftfreq(n, 1/fs)
fft_orig = np.abs(np.fft.fft(x[:n])) / n * 2
fft_multi = np.abs(np.fft.fft(output[:n])) / n * 2

plt.figure(figsize=(10, 4))
plt.plot(freqs[:n//2], fft_orig[:n//2], label="Original", color='blue', alpha=0.5)
plt.plot(freqs[:n//2], fft_multi[:n//2], label="Multiband Processed", color='orange', linewidth=2)
plt.title("Multiband Baseline: Frequency Domain Comparison")
plt.xlabel("Frequency (Hz)")
plt.ylabel("Normalized Magnitude")
plt.xlim(0, 2000)
plt.ylim(0, 1.5)
plt.legend()
plt.grid(True)

os.makedirs("data/figures", exist_ok=True)
plt.savefig("data/figures/multiband_comparison.png")
print("✅ 图片已重新保存，且音频长度未被截断！")