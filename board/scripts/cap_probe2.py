# -*- coding: utf-8 -*-
"""再进一步：把"是不是那 4 次 I2C 写干的"单独验出来。

上一轮已经钉死的事实：
  · `capture_begin` + `capture_block`（**完全不写 TX、也不写 I2C**）
      → 原始值 6e4~1.1e5，16 位域 rms 200~400 ＝ 安静，和 mic_level.py 一致。
  · `stream_begin` + `duplex_block`（写 4 次 I2C + 一直写 TX）
      → 原始值 2.4e6，16 位域 ~9800 ＝ 大 100 倍。
  · 而且把四段增益压到 -41.9 dB（输出只剩 0.008 倍）**照样满量程** ——
    所以跟送出去的信号幅度无关，只跟"有没有在写 TX"有关。声学反馈排除了。

现在两个脚本只剩一处不同：`stream_begin` 会写这 4 次 I2C
    R22=0x21  R24=0x41  R29=vol  R30=vol   （播放混音 + 耳机音量）
`capture_begin` 一次都不写。别的一切（Overlay、configure、set_volume、
select_microphone）两边完全一样。

所以这个脚本分三段，**同一进程里连着做**，一次就能把答案问出来：

  第一段 不写 I2C，只收            → 预期安静（复现 cap_probe）
  第二段 先做那 4 次 I2C 写，再只收  → 变大了 = 是 I2C 写干的
                                    没变  = 是 duplex 里写 TX 干的
  第三段 （只在第二段没变大时才跑）开 duplex 但 TX 全填 0，再收

用法：sudo python3 cap_probe2.py
"""
import os
import sys
import time

import numpy as np
from pynq import Overlay

HERE = os.path.dirname(os.path.abspath(__file__))
L = 480
N = 20

SO = os.path.join(HERE, "libaudio_stream.so")
ol = Overlay(os.path.join(HERE, "fir.bit"))
audio = ol.audio_codec_ctrl_0
audio.configure()
audio.set_volume(62)
audio.select_microphone()
time.sleep(0.3)

ffi = audio._ffi
MMIO_LEN = audio.mmio.length
UIO = audio.uio_index
IIC = audio.iic_index

ffi.cdef("""
    void* stream_begin(unsigned int mmap_size, unsigned int volume,
                       int uio_index, int iic_index);
    int   duplex_block(void* h, unsigned int* in_buf,
                       unsigned int* out_buf, unsigned int nsamples);
    void  stream_end(void* h);
    void* capture_begin(unsigned int mmap_size, int uio_index);
    int   capture_block(void* h, unsigned int* buf, unsigned int nsamples);
    void  capture_end(void* h);
""")
lib = ffi.dlopen(SO)

rbuf = ffi.new("unsigned int[]", 2 * L)
wbuf = ffi.new("unsigned int[]", 2 * L)


def stats(tag):
    """收 N 块，打中位 rms 和头两块的原始统计。只收不放。"""
    h = lib.capture_begin(MMIO_LEN, UIO)
    if h == ffi.NULL:
        sys.exit("capture_begin 失败")
    rs = []
    for k in range(N):
        if lib.capture_block(h, rbuf, L) != L:
            print("   %s：第 %d 块没搬满" % (tag, k))
            break
        a = np.frombuffer(bytes(ffi.buffer(rbuf, 4 * 2 * L)), dtype=np.int32)
        v = a.reshape(-1, 2)[:, 0]
        rs.append(float(np.sqrt(np.mean((v.astype(np.float64) / 256.0) ** 2))))
        if k < 2:
            print("   %s 块%d 原始 min=%d max=%d 均值=%d（摆幅 %d）"
                  % (tag, k, v.min(), v.max(), v.mean(), v.max() - v.min()))
    lib.capture_end(h)
    med = float(np.median(rs))
    print("   %s → 16 位域 rms 中位 %.1f" % (tag, med))
    print()
    return med


print("=" * 66)
print("第一段：不写 I2C，只收")
print("=" * 66)
m1 = stats("段1")

print("=" * 66)
print("第二段：先做 stream_begin 那 4 次 I2C 写（R22/R24/R29/R30），再只收")
print("=" * 66)
hs = lib.stream_begin(MMIO_LEN, 62, UIO, IIC)     # 只为了那 4 次 I2C 写
if hs == ffi.NULL:
    sys.exit("stream_begin 失败")
time.sleep(0.3)
m2 = stats("段2")

print("=" * 66)
print("第三段：TX 全填 0，开 duplex 再收")
print("=" * 66)
for i in range(2 * L):
    wbuf[i] = 0
rs = []
for k in range(N):
    if lib.duplex_block(hs, rbuf, wbuf, L) != L:
        print("   段3：第 %d 块没搬满" % k)
        break
    a = np.frombuffer(bytes(ffi.buffer(rbuf, 4 * 2 * L)), dtype=np.int32)
    v = a.reshape(-1, 2)[:, 0]
    rs.append(float(np.sqrt(np.mean((v.astype(np.float64) / 256.0) ** 2))))
    if k < 2:
        print("   段3 块%d 原始 min=%d max=%d 均值=%d（摆幅 %d）"
              % (k, v.min(), v.max(), v.mean(), v.max() - v.min()))
m3 = float(np.median(rs))
print("   段3 → 16 位域 rms 中位 %.1f" % m3)
lib.stream_end(hs)

print()
print("=" * 66)
print("结论")
print("=" * 66)
print("  段1（什么都不写）      %.1f" % m1)
print("  段2（只写了 4 次 I2C） %.1f" % m2)
print("  段3（TX 填 0 的 duplex）%.1f" % m3)
print()
if m2 > 10 * max(m1, 1):
    print("  → **是那 4 次 I2C 写把输入搞大的。** 段3 可以不管。")
    print("     下一步：把 4 次写逐个拆开试，看是哪一次。")
elif m3 > 10 * max(m1, 1):
    print("  → **是 duplex 里写 TX 这个动作本身。** I2C 那 4 次是清白的。")
    print("     那就要看 RX/TX 哪个寄存器被踩了 —— TX 填 0 都这样，")
    print("     说明不是内容的问题，是时序/握手的问题。")
else:
    print("  → 段2 段3 都没变大 —— 和上一轮对不上，把这三行数字记下来。")
