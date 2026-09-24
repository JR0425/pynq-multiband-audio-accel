import numpy as np
import matplotlib.pyplot as plt
import soundfile as sf
import os

# 1. 读取音频
audio_path = "data/audio/test_tone.wav"
if not os.path.exists(audio_path):
    print(f"找不到音频：{audio_path}，请先运行 generate_test_audio.py")
    exit()

data, fs = sf.read(audio_path)

# 2. 混合左右声道，同时包含 440Hz 和 880Hz
x = data[:, 0] + data[:, 1]

# 3. 去直流偏移
x = x - np.mean(x)

# 4. 计算 FFT
n = min(fs, len(x)) # 取前1秒
freqs = np.fft.fftfreq(n, 1/fs)
# 关键：除以 n 并乘以 2，进行归一化，让 Y 轴读数变成正常的幅度（接近 1）
fft_mag = np.abs(np.fft.fft(x[:n])) / n * 2

# 5. 画图
plt.figure(figsize=(10, 4))
plt.plot(freqs[:n//2], fft_mag[:n//2], color='blue')

plt.title("Audio Spectrum Baseline (Mixed Channels)")
plt.xlabel("Frequency (Hz)")
plt.ylabel("Normalized Magnitude")
plt.xlim(0, 2000)  # 聚焦关键频率范围
plt.ylim(0, 1.5)   # 限制 Y 轴，让波形更美观
plt.grid(True)

os.makedirs("data/figures", exist_ok=True)
output_fig_path = "data/figures/baseline_spectrum.png"
plt.savefig(output_fig_path)
print(f"频谱图已成功保存：{output_fig_path}")