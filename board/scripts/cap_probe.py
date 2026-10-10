# -*- coding: utf-8 -*-
"""对照实验：同一个麦克风口，用**自己 C 库的 capture_block** 单独收一遍。

为什么要做这个实验
------------------
现在有这么一对矛盾：

  · `mic_level.py`（走 PYNQ 驱动的 `audio.record()`）对 MIC 口读出来
    int16 域 28 → 安静。
  · `fir_live.py`（走我们自己的 `lib.duplex_block()`）对**同一个口**
    读出来满量程（in_rms 中位 29803），而且把耳机音量设成 0 也压不下去。

有可能是输入那一路本来就在大范围漂；也有可能是**我们写出去的东西
从别的地方绕回来了**（那 in_rms 就会跟着自己输出的电平走）。

`capture_block` **一个字节都不往 TX 写**，所以它这一趟不存在任何环路。
于是：

  · 它也是满量程  → 问题在"读"这一侧，和输出无关；
  · 它是安静的    → 问题在"写出去又绕回来"，是环路。

用法：sudo python3 cap_probe.py
"""
import os
import sys
import time

import numpy as np
from pynq import Overlay

HERE = os.path.dirname(os.path.abspath(__file__))
L = 480          # 和 fir_live 一样，10 ms
N = 30           # 收 30 块 = 0.3 秒

SO = os.path.join(HERE, "libaudio_stream.so")
if not os.path.exists(SO):
    sys.exit("找不到 %s" % SO)

ol = Overlay(os.path.join(HERE, "fir.bit"))
audio = ol.audio_codec_ctrl_0
audio.configure()
audio.set_volume(62)
audio.select_microphone()
time.sleep(0.3)

ffi = audio._ffi
MMIO_LEN = audio.mmio.length
UIO = audio.uio_index

ffi.cdef("""
    void* capture_begin(unsigned int mmap_size, int uio_index);
    int   capture_block(void* h, unsigned int* buf, unsigned int nsamples);
    void  capture_end(void* h);
""")
lib = ffi.dlopen(SO)

h = lib.capture_begin(MMIO_LEN, UIO)
if h == ffi.NULL:
    sys.exit("capture_begin 失败了")

buf = ffi.new("unsigned int[]", 2 * L)
print("capture_block 单独收 %d 块（每块 %d 采样）——**这一步完全不写 TX**" % (N, L))
print()
print("  块  原始min    原始max     均值    摆幅    | 当有符号看 16 位域 rms")
print("  ---- --------- --------- --------- -------- | ----------------------")

rms_all = []
for k in range(N):
    got = lib.capture_block(h, buf, L)
    if got != L:
        print("  第 %d 块只收到 %d/%d" % (k, got, L))
        break
    a = np.frombuffer(bytes(ffi.buffer(buf, 4 * 2 * L)), dtype=np.int32)
    st = a.reshape(-1, 2)
    v = st[:, 0]                      # 只看左声道，和 fir_live 一致
    r16 = float(np.sqrt(np.mean((v.astype(np.float64) / 256.0) ** 2)))
    rms_all.append(r16)
    if k < 10:
        print("  %4d %9d %9d %9d %8d | %8.1f"
              % (k, v.min(), v.max(), v.mean(), v.max() - v.min(), r16))

lib.capture_end(h)

print()
print("  这一趟 16 位域 rms：中位 %.1f  峰 %.1f" % (np.median(rms_all), max(rms_all)))
print("  对照 —— 同一个口、同一时刻，mic_level.py 读到的是 28（安静）")
print()
if np.median(rms_all) > 5000:
    print("  → **只收不放也是满量程**：问题出在'读'这一侧，和耳机/输出无关。")
elif np.median(rms_all) < 500:
    print("  → **只收不放是安静的**：问题出在'写出去又绕回来'，是环路。")
    print("     那就要查为什么音量 0 也压不住它。")
else:
    print("  → 介于两者之间（中位 %.0f），是个中间电平 — 把上面那 10 行波形看仔细点。"
          % np.median(rms_all))
