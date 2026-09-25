import numpy as np
import soundfile as sf
import os

os.makedirs("data/audio", exist_ok=True)
fs = 48000
t = np.linspace(0, 5, int(fs * 5), endpoint=False)

# 1. 生成一段带有背景噪声的语音
voice_data, _ = sf.read("data/audio/real_voice.wav")
if len(voice_data.shape) > 1:
    voice_data = voice_data[:, 0]
noise = np.random.normal(0, 0.01, len(voice_data))
noisy_voice = voice_data + noise
sf.write("data/audio/noisy_voice.wav", noisy_voice, fs)
print("✅ 已生成带噪语音：data/audio/noisy_voice.wav")

# 2. 生成一段扫频信号（20Hz - 20kHz），用于测试系统全频段响应
sweep = np.sin(2 * np.pi * (20 * t + (20000 - 20) / (2 * 5) * t**2))
sf.write("data/audio/sweep_20-20k.wav", sweep, fs)
print("✅ 已生成扫频信号：data/audio/sweep_20-20k.wav")