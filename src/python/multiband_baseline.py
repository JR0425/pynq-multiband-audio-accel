import numpy as np
import scipy.signal as signal
import soundfile as sf
import matplotlib.pyplot as plt
import os

# 1. 读取音频
audio_path = "data/audio/test_tone.wav"
data, fs = sf.read(audio_path)

# 2. 混合左右声道并去直流
x = data[:, 0] + data[:, 1]
x = x - np.mean(x)
print(f"去直流后的均值：{np.mean(x)}")

# 3. 定义 4 个频段
bands = [(0, 300), (300, 600), (600, 1000), (1000, 8000)]
taps_list = []

for low, high in bands:
    if low == 0:
        taps = signal.firwin(65, high, fs=fs, pass_zero='lowpass')
    elif high == 8000:
        taps = signal.firwin(65, low, fs=fs, pass_zero='highpass')
    else:
        taps = signal.firwin(65, [low, high], fs=fs, pass_zero='bandpass')
    taps_list.append(taps)

print("4 个频段的 FIR 滤波器设计完成！")

# 4. 对每个频段进行滤波并叠加
output = np.zeros_like(x)
for taps in taps_list:
    filtered = signal.lfilter(taps, 1.0, x)
    output += filtered * 0.5 

# 5. 计算 FFT（带归一化）
n = min(fs, len(x))
freqs = np.fft.fftfreq(n, 1/fs)
# 关键：除以 n 并乘以 2，让 Y 轴刻度正常
fft_orig = np.abs(np.fft.fft(x[:n])) / n * 2
fft_multi = np.abs(np.fft.fft(output[:n])) / n * 2

# 6. 画图
plt.figure(figsize=(10, 4))
# 蓝色线用淡色，橙色线用粗线，防止遮盖
plt.plot(freqs[:n//2], fft_orig[:n//2], label="Original (440Hz + 880Hz)", color='blue', alpha=0.5)
plt.plot(freqs[:n//2], fft_multi[:n//2], label="Multiband Processed", color='orange', linewidth=2)

plt.title("Multiband Baseline: Frequency Domain Comparison")
plt.xlabel("Frequency (Hz)")
plt.ylabel("Normalized Magnitude")
plt.xlim(0, 2000)
plt.ylim(0, 1.5)  # 限制 Y 轴范围，让曲线可视化更清晰
plt.legend()
plt.grid(True)

os.makedirs("data/figures", exist_ok=True)
plt.savefig("data/figures/multiband_comparison.png")
print("✅ 多频段频谱图已成功保存！")