# -*- coding: utf-8 -*-
"""插拔耳机时盯着麦克风电平,判断 U41 的插入检测有没有锁上。

为什么要这么测
--------------
板子的 `HP + MIC` 口里有一颗自动耳机开关(U41,TI TS3A226AE)。
它负责判断插头是 CTIA 还是 OMTP,然后把麦克风接到正确的触点上。
**它只在"插头插入"那一下做一次检测,然后记住结果。**

如果板子上电时插头已经插在里面,这"一下"就没发生,开关可能停在
"麦克风没接上"的状态 —— 录出来只剩本底噪声(实测 2~8 LSB)。
实测插到底之后要等 18 秒左右(有时 35 秒)电平才会跳起来。

所以这个脚本不测"能不能录",只测**插入那一下有没有反应**:
每 0.5 秒打一行电平,什么时候插拔由人自己决定。
判据和 mic_level.py 一致:**int16 域 rms 大于约 200 才算有信号**。

用法(板上)
==========
    cd /home/xilinx/jupyter_notebooks
    echo xilinx | sudo -S env XILINX_XRT=/usr \
        /usr/local/share/pynq-venv/bin/python3 -u mic_watch.py 60

参数是秒数。音量固定 0,板子不出声。也可以 `%run mic_watch.py`。
"""

import os
import sys
import time

import numpy as np
from pynq import Overlay

FS = 48000
L = 480
HALF = 1 << 23          # 无符号 24 位的零点
FULL = 1 << 24
BIG = 200.0             # int16 域,rms 超过这个就算话筒通了
HERE = os.path.dirname(os.path.abspath(__file__))
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0


def adc_i16(buf, ch=0):
    """无符号 24 位 → int16。静音时缓冲区里是 2^23 附近,不是 0。"""
    u = buf.reshape(-1, 2)[:, ch].astype(np.int64) & 0xFFFFFF
    s = np.where(u >= HALF, u - FULL, u)
    return np.clip(np.rint(s.astype(np.float64) / 256.0),
                   -32768.0, 32767.0).astype(np.int16)


def main():
    ol = Overlay(os.path.join(HERE, "fir.bit"))
    audio = ol.audio_codec_ctrl_0
    audio.configure()
    audio.select_microphone()   # 不切这一下,ADC 抓的是线路输入,读数恒为 0.7 LSB
    audio.set_volume(0)

    ffi = audio._ffi
    ffi.cdef("""
        void* stream_begin(unsigned int mmap_size, unsigned int volume,
                           int uio_index, int iic_index);
        int   duplex_block(void* h, unsigned int* in_buf,
                           unsigned int* out_buf, unsigned int nsamples);
        void  stream_end(void* h);
    """)
    lib = ffi.dlopen(os.path.join(HERE, "libaudio_stream.so"))
    h = lib.stream_begin(audio.mmio.length, 0, audio.uio_index, audio.iic_index)
    if h == ffi.NULL:
        raise SystemExit("stream_begin 失败")

    abuf_in = np.zeros(L * 2, dtype=np.int32)
    abuf_out = np.zeros(L * 2, dtype=np.int32)
    pin = ffi.cast("unsigned int*", ffi.from_buffer(abuf_in))
    pout = ffi.cast("unsigned int*", ffi.from_buffer(abuf_out))

    print("每 0.5 秒一行。个位数 = 话筒没通;大于 %.0f = 通了。" % BIG,
          flush=True)
    t0 = time.time()
    while time.time() - t0 < SECONDS:
        acc = []
        for _ in range(25):                   # 25 × 10 ms = 0.5 s
            abuf_out[:] = 0
            lib.duplex_block(h, pin, pout, L)
            acc.append(adc_i16(abuf_in).astype(np.float64))
        x = np.concatenate(acc)
        rms = float(np.sqrt(np.mean(x * x)))
        print("%5.1fs  rms = %7.1f  peak = %6d  %s"
              % (time.time() - t0, rms, int(np.max(np.abs(x))),
                 "<<< 通了" if rms >= BIG else ""), flush=True)

    lib.stream_end(h)


if __name__ == "__main__":
    main()
