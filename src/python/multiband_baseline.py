import numpy as np
import scipy.signal as signal
import soundfile as sf
import matplotlib.pyplot as plt
import os

# 1. 读取测试音频
audio_path = "data/audio/test_tone.wav"
data, fs = sf.read(audio_path)
x = data[:, 0].copy() # 取左声道

# 2. 去直流偏移
x = x - np.mean(x)
print(f"去直流后的均值（应接近0）：{np.mean(x)}")

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

# 5. 绘制对比图
n = min(fs, len(x))
freqs = np.fft.fftfreq(n, 1/fs)
fft_orig = np.abs(np.fft.fft(x[:n]))
fft_multi = np.abs(np.fft.fft(output[:n]))

plt.figure(figsize=(10, 4))
plt.plot(freqs[:n//2], fft_orig[:n//2], label="Original (440Hz + 880Hz)", alpha=0.7)
plt.plot(freqs[:n//2], fft_multi[:n//2], label="Multiband Processed", linewidth=2)

plt.title("Multiband Baseline: Frequency Domain Comparison")
plt.xlabel("Frequency (Hz)")
plt.ylabel("Magnitude")
plt.xlim(0, 2000)  # 限制横坐标
plt.legend()
plt.grid(True)

os.makedirs("data/figures", exist_ok=True)
plt.savefig("data/figures/multiband_comparison.png")
print("多频段频谱对比图已保存！")