import numpy as np
import scipy.signal as signal
import soundfile as sf

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

# 5. 保存音频（注意这里没有任何画图逻辑）
sf.write("data/audio/multiband_output.wav", output, fs)
print("✅ 音频已成功保存！请去文件夹里右键查看属性！")