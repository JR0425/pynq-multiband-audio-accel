# -*- coding: utf-8 -*-
"""拔插耳机，盯着麦克风电平看有没有反应。

为什么要这么测
--------------
板子的 `HP + MIC` 口里有一颗自动耳机开关（U41，TI TS3A226AE）。
它负责判断插头是 CTIA 还是 OMTP，然后把麦克风接到正确的触点上。
**它只在"插头插入"那一下做一次检测，然后记住结果。**

如果板子上电时插头已经插在里面，这"一下"就没发生，
开关可能停在"麦克风没接上"的状态 —— 录出来只剩本底噪声，
两个声道都纹丝不动。这正是之前看到的：有效值 7.7、峰值 33，
说话、敲麦都不动。

所以这个脚本不测"能不能录"，只测**插入那一下有没有反应**。

用法（板上 Jupyter，和 fir_audio_loop.py 放同一个目录）：

    %run mic_watch.py

跑起来后按屏幕提示做，前后约 17 秒。
"""

import time

import numpy as np
from pynq import Overlay

CHUNK_S = 0.5           # 每段 0.5 秒
HALF = 1 << 23          # 无符号 24 位的零点
FULL = 1 << 24
BIG = 40.0              # 有效值超过这个数就算"有响应"（本底只有 7~8）

# (从第几段开始, 这一段的提示语)
PLAN = [
    (0,  "什么都别动，保持现在这样"),
    (6,  ">>> 把耳机拔出来 <<<"),
    (12, ">>> 插回去，使劲插到底 <<<"),
    (18, ">>> 对着麦克风说话，或用指甲轻敲麦上的小孔 <<<"),
]
TOTAL = 34

ol = Overlay("fir.bit")
audio = ol.audio_codec_ctrl_0
audio.configure()
audio.select_microphone()       # 只看麦克风这一路
time.sleep(0.3)


def to_signed(u):
    """无符号 24 位 → 有符号 24 位。静音时缓冲区里是 2^23 附近，不是 0。"""
    u = u.astype(np.int64)
    return np.where(u >= HALF, u - FULL, u)


def level():
    """返回 [(左 rms, 左 峰), (右 rms, 右 峰)]，单位是 int16 域。"""
    st = np.asarray(audio.buffer).reshape(-1).astype(np.int64).reshape(-1, 2)
    out = []
    for ch in (0, 1):
        v = to_signed(st[:, ch]).astype(np.float64)
        out.append((float(np.sqrt(np.mean(v ** 2))) / 256.0,
                    float(np.max(np.abs(v))) / 256.0))
    return out


def tip(i):
    note = PLAN[0][1]
    for start, text in PLAN:
        if i >= start:
            note = text
    return note


print("")

# 先录一段空的，把本底测出来
audio.record(CHUNK_S)
base = max(level()[0][0], level()[1][0])

print("本底（没信号时的有效值）：%.1f" % base)
print("下面只要哪个数明显超过 %.1f，就是有东西进 codec 了\n" % BIG)
print("  秒   左 rms    左 峰  |  右 rms    右 峰  | 现在做什么")
print("  " + "-" * 70)

t0 = time.time()
peaks = []
for i in range(TOTAL):
    audio.record(CHUNK_S)
    (lr, lp), (rr, rp) = level()
    peaks.append(max(lr, rr))
    flag = "  <<< 有响应！" if max(lr, rr) >= BIG else ""
    print("  %4.1f  %8.1f %8.1f  | %8.1f %8.1f  | %s%s"
          % (time.time() - t0, lr, lp, rr, rp, tip(i), flag))

print("""
怎么判读
--------
· 全程都在 7~8 附近不动 → 插入那一下板子也没认出耳机。
  这不是脚本的问题，是这颗自动开关没工作，或者插座触点接触不良。
· "插回去"那几行跳起来了 → 就是它。以后每次跑录音脚本前，
  先把耳机拔掉再插回去就行。
· 只有一路跳 → 麦克风只接在那一路上，fir_audio_loop.py 挑声道挑错了
  （它用无符号域的能量挑，静音时被 2^23 的零点淹没，等于抛硬币）。
""")
