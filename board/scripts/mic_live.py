# -*- coding: utf-8 -*-
"""麦克风实时电平表：一边说话一边看两个声道。

为什么需要它
------------
`fir_audio_loop.py` 第 3 节挑声道的方式是：

    e_l = mean(原始缓冲区的左声道 ** 2)
    e_r = mean(原始缓冲区的右声道 ** 2)
    挑能量大的那个声道

缓冲区是无符号 24 位，静音时两边的值都堆在零点 2^23 ≈ 8.39e6 附近。
拿这种原始值去比「哪一路能量大」，比的是一个跟声音无关的偏置量，
不是在比声音本身 —— 所以这里先把无符号还原成有符号再算电平。

实测（2026-10-02）：麦克风是单声道的，被同时送进左右两路，
两边的数字几乎一样，**不能靠比大小挑声道**。

用法（板上 Jupyter，和 fir_audio_loop.py 放同一个目录）：

    %run mic_live.py

看到 `>>> 开始 <<<` 就对着耳机麦说话，或者用指尖轻敲麦克风的小孔。
"""

import time

import numpy as np
from pynq import Overlay

CHUNK_S = 0.5           # 每段多长
N_CHUNK = 8             # 打几段（共 4 秒）
WARMUP = 6              # 前面丢掉几段，给说话的人留出准备时间
HALF = 1 << 23          # 无符号 24 位的零点
FULL = 1 << 24

ol = Overlay("fir.bit")
audio = ol.audio_codec_ctrl_0
audio.configure()


def to_signed(u):
    """无符号 24 位 → 有符号 24 位。"""
    u = u.astype(np.int64)
    return np.where(u >= HALF, u - FULL, u)


def measure():
    """返回 [(左 rms, 左 峰值), (右 rms, 右 峰值)]，单位是 int16 域。

    除以 256 是为了和 fir_audio_loop.py 打印的数对得上 ——
    它那边是 `>> 8`，也就是除以 256。
    """
    flat = np.asarray(audio.buffer).reshape(-1).astype(np.int64)
    st = flat.reshape(-1, 2)
    out = []
    for ch in (0, 1):
        v = to_signed(st[:, ch]).astype(np.float64)
        out.append((float(np.sqrt(np.mean(v ** 2))) / 256.0,
                    float(np.max(np.abs(v))) / 256.0))
    return out


for name, sel in (("MIC", audio.select_microphone),
                  ("LINE_IN", audio.select_line_in)):
    print("\n" + "=" * 66)
    print("=== %s ===" % name)
    print("=" * 66)
    sel()
    time.sleep(0.3)

    print("   准备中…… %d 秒后开始敲/说" % int(WARMUP * CHUNK_S))
    for _ in range(WARMUP):
        audio.record(CHUNK_S)

    print("   >>> 开始！对着耳机麦说话，或用指尖轻敲麦克风的小孔 <<<")
    for i in range(N_CHUNK):
        audio.record(CHUNK_S)
        (l_rms, l_pk), (r_rms, r_pk) = measure()
        print("   %d/%d   左 rms %9.1f 峰 %9.1f   |   右 rms %9.1f 峰 %9.1f"
              % (i + 1, N_CHUNK, l_rms, l_pk, r_rms, r_pk))
    print("   >>> 停 <<<")

print("""
怎么判读
--------
· 判据是 200（和 fir_audio_loop.py 的 SILENT_RMS 一致），但要**看峰**：
  敲麦的时候峰值应该明显跳起来，哪怕 rms 不大 —— 峰值比 rms 灵敏得多。
· **左右分开看**。如果只有一路跳，说明麦接在那一路上，
  那 fir_audio_loop.py 挑声道挑错了，不是麦的问题。
· 两路都纹丝不动（一直停在个位数）→ 信号根本没进 codec。
  这时候依次查：插头是不是插到底了（四段插头要插得更深）、
  `HP + Mic` 有没有插错口。
""")
