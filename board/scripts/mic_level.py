# -*- coding: utf-8 -*-
"""麦克风 / 线路输入的原始电平诊断。

这个口最大的坑是「无符号 24 位」
--------------------------------
缓冲区里每个采样是 24 位、放在低 3 字节、高字节补零，按无符号整数看。
于是**静音**在这里不是 0，而是分成两堆：

    有符号 +0  →  无符号 0
    有符号 -0  →  无符号 2^24

后果：一个安静的输入，缓冲区的「均值」会显示成 2^23 ≈ 8.39e6，
「减均值后的 rms」会显示成 2^23 —— 两个数都很大，**但它们不是声音**。
照这个下判断，会把静音读成"满量程过载"。（实测见过。）

所以这个脚本先把无符号 24 位还原成有符号，再算电平。

    有符号 = u - 2^24   (当 u >= 2^23)
    有符号 = u          (当 u <  2^23)

这和 fir_audio_loop.py 里 `(x >> 8).astype(int16)` 是同一个换算，
只是这里不右移，好直接看到 24 位的原值。

用法（板上 Jupyter，和 fir_audio_loop.py 放同一个目录）：

    %run mic_level.py
"""

import time

import numpy as np
from pynq import Overlay

FS = 48000.0
SECONDS = 2.0
HALF = 1 << 23          # 2^23，无符号 24 位的零点
FULL = 1 << 24          # 2^24
# 和 fir_audio_loop.py 一致。这个数是 **int16 域**（下面 rms24 / 256 换过来的），
# 满量程 32767。以前这里写 8000 —— 那是拿 24 位的满量程（8.4e6）估的，
# 差了 256 倍，真实人声（实测 400~1200）会被这张表判成"静音"。
SILENT_RMS = 200.0

ol = Overlay("fir.bit")
audio = ol.audio_codec_ctrl_0
audio.configure()
audio.set_volume(62)

print("判据来自 fir_audio_loop.py：SILENT_RMS = %.0f（int16 域，满量程 32767）"
      % SILENT_RMS)
print("无符号 24 位的零点 = 2^23 = %d" % HALF)


def to_signed(u):
    """无符号 24 位 → 有符号 24 位。"""
    u = u.astype(np.int64)
    return np.where(u >= HALF, u - FULL, u)


for name, sel in (("LINE_IN", audio.select_line_in),
                  ("MIC", audio.select_microphone)):
    print("\n=== %s ===" % name)
    sel()
    time.sleep(0.3)
    audio.record(SECONDS)

    flat = np.asarray(audio.buffer).reshape(-1).astype(np.int64)
    st = flat.reshape(-1, 2)

    print("   原始（无符号）均值 %.0f   —— 静音时这个数就是 2^23，不是声音"
          % flat.mean())

    for ch in (0, 1):
        v = to_signed(st[:, ch])
        rms24 = float(np.sqrt(np.mean(v.astype(np.float64) ** 2)))
        rms16 = rms24 / 256.0
        rail = int(np.count_nonzero(np.abs(v) > 0.9 * HALF))
        print("  ch%d  有符号 min %d  max %d  均值 %.0f"
              % (ch, v.min(), v.max(), v.mean()))
        print("       真电平 rms(24位) %.0f  →  int16 域 %.1f   %s"
              % (rms24, rms16,
                 "有信号" if rms16 >= SILENT_RMS else "低于门限＝静音"))
        print("       贴近满量程(|v|>0.9*2^23)的样本 %d / %d   （多＝过载）"
              % (rail, v.size))

print("""
怎么判读
--------
· 只看「int16 域」那个数，和 200 比。大于 200 才算真有信号。
· 原始均值、无符号 rms 都是表示法的副产物，不要拿来判断。
· LINE_IN 和 MIC **都**低于 200 → 那个口上没有声音进来。先看
  `skill/pitfalls/pynq_audio_playback.md` 的坑 5：耳机麦插着却录不到，
  多半是板子上的自动耳机开关没做检测，拔了重插一次就好。
  板子只有两个 3.5mm 口：
    `HP + Mic`  输出 + 麦克风，一口两用，**四段 TRRS**。
                普通三段耳机插上去只有耳机触点，没有麦克风触点。
    `Line-in`   只能输入（接电脑耳机口当音源）。
""")
