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
left_channel = data[:, 0]
print(f"音频读取成功。采样率：{fs} Hz, 长度：{len(left_channel)}")

# 2. 设计 FIR 低通滤波器
# 我们的测试音频左声道是 440Hz，右声道是 880Hz
# 这里设计一个截止频率为 600Hz 的低通滤波器，目的是把 880Hz 滤掉
numtaps = 65  # 滤波器阶数（抽头数），奇数
cutoff = 600  # 截止频率 600 Hz
taps = signal.firwin(numtaps, cutoff, fs=fs)
print(f"FIR 滤波器设计完成，抽头数：{numtaps}，截止频率：{cutoff} Hz")

# 3. 执行 FIR 滤波 (时域卷积)
# 我们只对左声道进行滤波
filtered_left = signal.lfilter(taps, 1.0, left_channel)
# 把右声道复制一份，保持双声道格式
filtered_data = np.stack((filtered_left, data[:, 1]), axis=1)

# 4. 保存滤波后的音频
output_audio_path = "data/audio/test_tone_filtered.wav"
sf.write(output_audio_path, filtered_data, fs)
print(f"滤波后音频已保存：{output_audio_path}")

# 5. 绘制原始音频与滤波后音频的频谱对比图
n = min(fs, len(left_channel)) # 取前1秒
freqs = np.fft.fftfreq(n, 1/fs)
fft_orig = np.abs(np.fft.fft(left_channel[:n]))
fft_filt = np.abs(np.fft.fft(filtered_left[:n]))

plt.figure(figsize=(12, 5))
plt.plot(freqs[:n//2], fft_orig[:n//2], label="Original (440Hz + 880Hz)", alpha=0.7)
plt.plot(freqs[:n//2], fft_filt[:n//2], label="FIR Filtered (Cutoff 600Hz)", linewidth=2)
plt.title("FIR Filter Baseline: Frequency Domain Comparison")
plt.xlabel("Frequency (Hz)")
plt.ylabel("Magnitude")
plt.legend()
plt.grid(True)

os.makedirs("data/figures", exist_ok=True)
output_fig_path = "data/figures/fir_filter_comparison.png"
plt.savefig(output_fig_path)
print(f"频谱对比图已保存：{output_fig_path}")