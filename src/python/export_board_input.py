"""把 real_voice.wav 转成「板子上跑的那一条固定输入」。

为什么要有这个文件：
    板子上原来每次跑都要现场对麦克风念一段。同一次运行里所有参数用的是同一段
    录音，但**换一次运行就换一段声音** —— 参数组之间就没法比，而且板子那个
    耳机麦口有颗自动检测开关（U41），只在插头插入那一下检测，时不时插不进去。
    固定一条输入，跑多少遍都是同一串比特，比出来的差别才只来自参数。

为什么在电脑上转、不在板子上转：
    real_voice.wav 是 44100 Hz，硬件的系数是按 48000 Hz 设计的。在电脑上转一次，
    板子拿到的就是最终那串 int16 —— 板子不用装 scipy，也不用把重采样再写一遍。

条件必须和 `multiband_baseline.py` 第 1 步一致：
    混声道（两声道相加）→ 去直流 → resample_poly 到 48 kHz。
    不一样的话，「板子的输入」和「软件基线的输入」就不是同一串比特，
    两边的输出没法对撞。那边改了，这里要跟着改。

用法（仓库根目录）：
    python src/python/export_board_input.py                   # 默认 2.0 秒起、截 6 秒
    python src/python/export_board_input.py --seconds 10.58   # 整段
输出：
    data/audio/board_input_48k.wav   48 kHz 单声道 int16
"""

import argparse
import os
from math import gcd

import numpy as np
import scipy.signal as signal
from scipy.io import wavfile

IN_PATH = "data/audio/real_voice.wav"
OUT_PATH = "data/audio/board_input_48k.wav"
FS_OUT = 48000

# 满量程 32767。混完两声道后本来就可能超过 ±1.0（基线那边也是这么混的），
# 这里按 int16 的规则裁掉，裁掉多少要打印出来 —— 裁得多说明这段素材不适合，
# 不能装作没发生。
I16_MAX = 32767
I16_MIN = -32768

# 核的工作电平，满量程的比例。和板子上 fir_audio_loop.py 的 TARGET_RMS 是同一个数。
# 两处必须一致，否则板子会对已经归一化过的文件再缩一次。
TARGET_RMS = 0.15 * 32767.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=2.0,
                    help="从第几秒开始截（默认 2.0，避开开头）")
    ap.add_argument("--seconds", type=float, default=6.0,
                    help="截多长（默认 6.0 —— A/B 时太长了记不住上一组）")
    args = ap.parse_args()

    # 注意顺序：scipy.io.wavfile.read 返回的是 (采样率, 数据)，不是 (数据, 采样率)
    fs_in, data = wavfile.read(IN_PATH)
    x = data.astype(np.float64) / 32768.0
    if x.ndim > 1:
        x = x[:, 0] + x[:, 1]            # 混声道，和基线一致
    x = x - np.mean(x)                   # 去直流，和基线一致
    print("读入 %s：%.2f 秒 @ %d Hz，%d 个采样" % (IN_PATH, len(x) / fs_in, fs_in, len(x)))

    g = gcd(int(fs_in), FS_OUT)
    x = signal.resample_poly(x, FS_OUT // g, int(fs_in) // g)
    print("重采样 %d → %d Hz（比 %d/%d）：%d 个采样"
          % (fs_in, FS_OUT, FS_OUT // g, int(fs_in) // g, len(x)))

    a = max(0, int(round(args.start * FS_OUT)))
    b = min(len(x), a + int(round(args.seconds * FS_OUT)))
    if b - a < int(0.5 * FS_OUT):
        raise SystemExit("截出来只有 %d 个采样，太短了 —— 检查 --start / --seconds"
                         % (b - a))
    seg = x[a:b]
    print("截取 %.2f ～ %.2f 秒：%d 个采样（%.2f 秒）"
          % (a / FS_OUT, b / FS_OUT, len(seg), len(seg) / FS_OUT))

    over = int(np.count_nonzero(seg > 1.0))
    under = int(np.count_nonzero(seg < -1.0))
    if over or under:
        print("注意：混完声道后有 %d 个采样超出满量程（下面归一化时会一起解决）"
              % (over + under))

    # 归一化到核的工作电平 —— 和板子上 fir_audio_loop.py 的增益匹配**同一个公式**，
    # 只是这里在浮点域（±1.0）算，那边在 int16 域算，两边等价。
    # 因为文件里已经归一化过，板子那边算出来就是 1.00 倍，不会再动第二遍。
    # 电平只在这一个地方定，跑多少遍都一样。
    # （核里压缩器的门限是 0.1，是按**单个频段**的幅度比的 —— 四段一分每段只剩
    #   一小块，信号不抬到 15% 有效值根本够不到门限，压缩等于没做。）
    rms_f = float(np.sqrt(np.mean(seg ** 2)))
    pk_f = float(np.abs(seg).max())
    gain = min(TARGET_RMS / 32768.0 / max(rms_f, 1e-9), 0.90 / max(pk_f, 1e-9))
    print("归一化：有效值 %.0f → %.0f（满量程的 %.1f%% → %.1f%%），"
          "峰值 %.0f → %.0f（%.1f%% → %.1f%%），乘 %.4f 倍"
          % (rms_f * 32768, TARGET_RMS, 100 * rms_f, 100 * TARGET_RMS / 32768,
             pk_f * 32768, pk_f * gain * 32768, 100 * pk_f, 100 * pk_f * gain, gain))

    x16 = np.clip(np.round(seg * gain * 32768.0), I16_MIN, I16_MAX).astype(np.int16)
    rms_v = float(np.sqrt(np.mean(x16.astype(np.float64) ** 2)))
    pk_v = int(np.abs(x16).max())
    print("结果：有效值 %.0f（满量程的 %.1f%%）、峰值 %d（%.1f%%）、峰均比 %.2f"
          % (rms_v, 100 * rms_v / 32768, pk_v, 100 * pk_v / 32768, pk_v / rms_v))
    print("板子上再算一遍这个公式会得到 1.0000 倍 —— 可以拿这个数对账。")
    if np.count_nonzero(x16 == I16_MAX) or np.count_nonzero(x16 == I16_MIN):
        print("注意：还有采样顶在满量程上 —— 这段素材削过顶，换一段截取再试")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    wavfile.write(OUT_PATH, FS_OUT, x16)
    print("已写出：%s（%d 字节）" % (OUT_PATH, os.path.getsize(OUT_PATH)))
    print("把它传到板子的 /home/xilinx/jupyter_notebooks/ 下，")
    print("fir_audio_loop.py 会自动认这个名字，不用再录。")


if __name__ == "__main__":
    main()
