import numpy as np
import matplotlib.pyplot as plt
import soundfile as sf
import os

# 1. 读取刚才生成的测试音频
audio_path = "data/audio/test_tone.wav"

if not os.path.exists(audio_path):
    print(f"找不到音频文件：{audio_path}，请先运行 generate_test_audio.py")
else:
    data, samplerate = sf.read(audio_path)
    print(f"音频读取成功！采样率: {samplerate} Hz, 总样本数: {len(data)}")

    # 2. 取左声道进行快速傅里叶变换 (FFT)
    # data[:, 0] 代表左声道（440Hz），data[:, 1] 是右声道（880Hz）
    left_channel = data[:, 0]
    
    # 为了加快速度，只取前 1 秒的数据做 FFT
    n = min(samplerate, len(left_channel))
    fft_result = np.fft.fft(left_channel[:n])
    freqs = np.fft.fftfreq(n, 1/samplerate)

    # 3. 画图并保存到 data/figures/
    plt.figure(figsize=(10, 4))
    # 只画正半轴的频谱
    plt.plot(freqs[:n//2], np.abs(fft_result)[:n//2])
    plt.xlim(0, 2000)  # 只关注 0-2000 Hz 范围
    plt.title("Audio Spectrum Baseline (Left Channel)")
    plt.xlabel("Frequency (Hz)")
    plt.ylabel("Magnitude")
    plt.grid(True)
    
    # 确保图片保存目录存在
    os.makedirs("data/figures", exist_ok=True)
    output_path = "data/figures/baseline_spectrum.png"
    plt.savefig(output_path)
    print(f"📊 频谱图已成功保存至：{output_path}")