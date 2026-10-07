# -*- coding: utf-8 -*-
"""`play()` 每次调用多花的那 3.5 ms 到底花在哪？—— 只测时间，不放音。

============================ 为什么问这个 ============================
`fir_live.py`（真·实时那条流水线）实测只有 **0.65 倍实时**：
每 10 ms 一块，墙钟要 15.5 ms。核只占 214 µs，所以不是核的问题。

拆开量各段（那次的表）：

             期望     实测     多出来
    play    10.00 ms  13.31 ms  **+3.31**
    record  10.00 ms   9.94 ms   −0.06

三个块长拟合出来是 `play_ms = 3.47 + 0.0205·L`：**截距 3.47 ms 是每次调用的
固定开销**；`record()` 没有（截距 +0.1 ms）。

**这意味着光调块长永远到不了实时** —— 每播 1 秒音频要花 `1 + 166/L` 秒，
L 再大也只是逼近 1，永远超不过。所以必须找到这 3.47 ms 是什么。

============================ 读源码读出来的 ============================
驱动是 `pynq/lib/_pynq/_audio/audio_adau1761.cpp`（板上那份，源码就在旁边）。
两个函数的主体都是同一个"轮询状态寄存器 → 搬一个采样"的循环，**唯一的差别**：

    record()                               play()
    ------                                 ------
    setUIO / setI2C                        setUIO / setI2C
                                           write_audio_reg(R22, 0x21)   ← 4 次 I2C 写
                                           write_audio_reg(R24, 0x41)      给 DAC 开声
                                           write_audio_reg(R29, vol)
                                           write_audio_reg(R30, vol)
    for (i...) { 收一个采样 }              for (i...) { 放一个采样 }
                                           write_audio_reg(R22, 0x01)   ← 4 次 I2C 写
                                           write_audio_reg(R24, 0x01)      把 DAC 静音
                                           write_audio_reg(R23, 0x00)
                                           write_audio_reg(R25, 0x00)
    unsetUIO / unsetI2C                    unsetUIO / unsetI2C

**`play()` 每调一次，多 8 次 I2C 写。** `record()` 一次都不写。

============================ 这一测怎么判 ============================
拿 `nsamples=0` 调一次：传数据的循环体一次都不执行，
剩下的就只有 mmap + 开 i2c + 那 8 次写。

    play(0) 耗时  ≈ record(0) 耗时   → 3.47 ms 不在 I2C 上，得另找
    play(0) 耗时 − record(0) 耗时 ≈ 3.4 ms
                                     → **就是那 8 次 I2C 写**，
                                       解法是绕开它（自己写一个流式 play）

顺带：这 8 次写还意味着**每块都把 DAC 静音再开声一次**。
600 块/分钟 = 每分钟 600 次静音循环 —— 那不只是慢，还会**咔咔响**。

用法（板上，要 root，和 fir.bit 同目录）：

    echo xilinx | sudo -S env XILINX_XRT=/usr \
      /usr/local/share/pynq-venv/bin/python3 audio_overhead_probe.py

不发声音。`play()` 在 nsamples>0 时会真往 TX FIFO 塞数据，所以非零那两测
塞的是**全零缓冲**（静音），耳朵听不到东西。
"""

import ctypes
import os
import sys
import time

import numpy as np
from pynq import Overlay


def hr(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_bit():
    for c in (os.path.join(os.getcwd(), "fir.bit"),
              os.path.join(_HERE, "fir.bit"),
              os.path.join(_HERE, "..", "overlay", "fir.bit")):
        if os.path.isfile(c):
            return os.path.abspath(c)
    raise SystemExit("找不到 fir.bit")


hr("0. 加载 overlay")
ol = Overlay(_find_bit())
audio = ol.audio_codec_ctrl_0
audio.configure()
audio.set_volume(0)                     # 音量 0，就算出声也听不见
print("   用到的是：%s" % _find_bit())

ffi = audio._ffi
libaudio = audio._libaudio
MMIO_LEN = audio.mmio.length
UIO = audio.uio_index
IIC = audio.iic_index
VOLUME = 62
print("   uio=%s iic=%s mmio.length=0x%x" % (UIO, IIC, MMIO_LEN))

# 缓冲：够放 L 个采样就行。**填全零** —— play() 会真把它们推进 TX FIFO，
# 全零就是静音，不会吓着耳朵。
L = 480
buf = np.zeros(L * 2, dtype=np.int32)
aptr = ffi.cast("unsigned int*", ffi.from_buffer(buf))


def timeit(fn, n=7):
    """取 min —— 第一次调用会慢（页表、i2c 打开），取最快的一次更像稳态。"""
    best = None
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return best


hr("1. 热身")
try:
    libaudio.record(MMIO_LEN, aptr, 0, UIO, IIC)
    libaudio.play(MMIO_LEN, aptr, 0, VOLUME, UIO, IIC)
    print("   热身过")
except Exception as e:                                   # noqa: BLE001
    print("   热身失败：%r" % (e,))
    raise

hr("2. nsamples=0：只有 mmap + 开 i2c（play 多那 8 次 I2C 写）")
t_rec0 = timeit(lambda: libaudio.record(MMIO_LEN, aptr, 0, UIO, IIC))
t_play0 = timeit(lambda: libaudio.play(MMIO_LEN, aptr, 0, VOLUME, UIO, IIC))
print("   record(0)   %8.3f ms" % (t_rec0 * 1e3))
print("   play(0)     %8.3f ms" % (t_play0 * 1e3))
print("   差          %8.3f ms   ← 这就是那 8 次 I2C 写" % ((t_play0 - t_rec0) * 1e3))

hr("3. 真传数据（L=%d = %.2f ms 音频）" % (L, L / 48000 * 1e3))
t_recL = timeit(lambda: libaudio.record(MMIO_LEN, aptr, L, UIO, IIC))
t_playL = timeit(lambda: libaudio.play(MMIO_LEN, aptr, L, VOLUME, UIO, IIC))
exp = L / 48000.0
print("   record(L)   实测 %8.3f ms   期望 %.2f   超出 %+8.3f ms"
      % (t_recL * 1e3, exp * 1e3, (t_recL - exp) * 1e3))
print("   play(L)     实测 %8.3f ms   期望 %.2f   超出 %+8.3f ms"
      % (t_playL * 1e3, exp * 1e3, (t_playL - exp) * 1e3))

hr("4. 怎么读")
over_play = (t_playL - exp)
over_rec = (t_recL - exp)
print("""   判据：`play(0) − record(0)` 和 `play(L) 的超额` 这两个数是不是一回事。

     两个数都在 3 ms 上下、彼此贴近
       → **固定开销就在那 8 次 I2C 写上**，和传多少数据无关。
         解法：自己编一个只做"循环搬数据"的流式 play，
         unmute/mute 只在会话开始和结束各做一次。
         板上 **有 gcc（9.3.0）**，驱动源码就在旁边，`make` 那份 Makefile 也在。
         **不用重跑 Vivado、不用换 bit。**

     两个数对不上（比如 play(0) 很快、play(L) 却慢）
       → 开销在传数据本身（I2S TX 那边有额外的等待/握手），
         那就得去看 PL 里 audio_codec_ctrl 的寄存器手册，是另一件事。""")
print()
print("   顺便：这 8 次写里有一半是**把 DAC 静音**。每块静音一次再开声，")
print("   按 L=480 算就是每秒 100 次 —— 那还在动听的范畴之外，会直接听到咔声。")
print("   所以不管够不够快，这一处都得改成「开会话说一次、说完关一次」。")
