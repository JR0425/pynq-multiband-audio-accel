import soundfile as sf
import sys

# 校验脚本：防止音频处理过程中出现截断
def check_length(input_path, output_path):
    data_in, fs_in = sf.read(input_path)
    data_out, fs_out = sf.read(output_path)
    
    len_in = len(data_in) / fs_in
    len_out = len(data_out) / fs_out
    
    if abs(len_in - len_out) > 0.1:
        print(f"❌ 错误：音频长度不一致！输入 {len_in:.2f} 秒，输出 {len_out:.2f} 秒")
        sys.exit(1) # 报错并退出
    print(f"✅ 长度校验通过：输入与输出均为 {len_in:.2f} 秒")

if __name__ == "__main__":
    # 默认检查的输入输出路径，可自行修改
    check_length("data/audio/real_voice.wav", "data/audio/multiband_output.wav")