import numpy as np
import soundfile as sf
import os

# 确保输出文件夹存在
os.makedirs("data/audio", exist_ok=True)

# 1. 设置参数：采样率 48000 Hz，时长 3 秒
fs = 48000
duration = 3.0
t = np.linspace(0, duration, int(fs * duration), endpoint=False)

# 2. 合成一段包含 440Hz 和 880Hz 的正弦波（左右声道不同频率）
audio_left = 0.5 * np.sin(2 * np.pi * 440 * t)
audio_right = 0.5 * np.sin(2 * np.pi * 880 * t)

# 把左右声道叠在一起，变成双声道音频
audio_data = np.stack((audio_left, audio_right), axis=1)

# 3. 保存为 .wav 文件
output_path = "data/audio/test_tone.wav"
sf.write(output_path, audio_data, fs)

print(f"🎉 测试音频已成功生成！路径为：{output_path}")
print(f"采样率：{fs} Hz, 时长：{duration} 秒")