import numpy as np
import matplotlib.pyplot as plt
import soundfile as sf
import scipy.signal as signal
import os

# 1. 读取原始音频
audio_path = "data/audio/test_tone.wav"
if not os.path.exists(audio_path):
    print(f"找不到音频：{audio_path}，请先运行 generate_test_audio.py")
    exit()

data, fs = sf.read(audio_path)
left_channel = data[:, 0] + data[:, 1] # 左右声道叠加

# 2. 核心步骤：去直流偏移（必须在 FFT 之前执行！）
left_channel = left_channel - np.mean(left_channel)
print(f"去直流后的均值（应接近0）：{np.mean(left_channel)}")

# 3. 设计 600Hz 低通 FIR 滤波器
numtaps = 65
cutoff = 600
taps = signal.firwin(numtaps, cutoff, fs=fs)
print(f"FIR 滤波器设计完成，抽头数：{numtaps}，截止频率：{cutoff} Hz")

# 4. 执行 FIR 滤波
filtered_left = signal.lfilter(taps, 1.0, left_channel)

# 5. 绘制原始与滤波后的频谱对比图
n = min(fs, len(left_channel)) # 取前1秒数据
freqs = np.fft.fftfreq(n, 1/fs)

# 对信号进行 FFT 并取绝对值
fft_orig = np.abs(np.fft.fft(left_channel[:n]))
fft_filt = np.abs(np.fft.fft(filtered_left[:n]))

plt.figure(figsize=(10, 4))
# 只画正半轴频率 (0 - 2000Hz)
plt.plot(freqs[:n//2], fft_orig[:n//2], label="Original (440Hz + 880Hz)", alpha=0.7)
plt.plot(freqs[:n//2], fft_filt[:n//2], label="FIR Filtered (Cutoff 600Hz)", linewidth=2)

plt.title("FIR Filter Baseline: Frequency Domain Comparison")
plt.xlabel("Frequency (Hz)")
plt.ylabel("Magnitude")
plt.xlim(0, 2000)  # 限制横坐标，放大 0-2000Hz 区域
plt.ylim(0,1000)
plt.legend()
plt.grid(True)

os.makedirs("data/figures", exist_ok=True)
output_fig_path = "data/figures/fir_filter_comparison.png"
plt.savefig(output_fig_path)
print(f"频谱对比图已保存：{output_fig_path}")