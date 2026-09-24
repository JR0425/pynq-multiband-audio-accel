import numpy as np
import soundfile as sf
import os

# 读取真实语音
audio_path = "data/audio/real_voice.wav"
data, fs = sf.read(audio_path)

# 混合左右声道并去直流
if len(data.shape) > 1:
    x = data[:, 0] + data[:, 1]
else:
    x = data.copy()
x = x - np.mean(x)

# 截取前 1000 个点作为测试激励（足够 C++ 仿真用）
x_test = x[:1000]

# 确保目录存在
os.makedirs("data/audio", exist_ok=True)

# 导出为纯文本，每行一个浮点数
np.savetxt("data/audio/test_input.txt", x_test)
print(f"✅ 测试数据导出成功！共 {len(x_test)} 个样本，路径：data/audio/test_input.txt")