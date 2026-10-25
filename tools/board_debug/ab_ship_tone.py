# -*- coding: utf-8 -*-
"""A/B 对照放音：先出货位流，再我们的位流，同一个 codec、同一副耳机。

为什么要这一步：i2sdbg 的探针已经证明 PL 在 G18 上送出了正确的 I2S 波形
（自收解出 ±2^21、翻转计数在涨），引脚落点也和出货版逐脚一致，但耳机
一点声都没有。于是需要一个"出声/不出声"的对照，且两轮之间不改任何
codec 寄存器 —— 唯一的变量就是 PL。

    echo xilinx | sudo -S env XILINX_XRT=/usr \
        /usr/local/share/pynq-venv/bin/python3 ab_ship_tone.py

两轮各 10 秒 440 Hz、-6 dBFS。
"""
import os
import sys
import time

os.environ.setdefault("XILINX_XRT", "/usr")
sys.path.insert(0, "/home/xilinx/dma_exp")

import dma_audio_probe as P  # noqa: E402
import hearing_tone as HT     # noqa: E402
from pynq import Overlay  # noqa: E402

WAV = "/dev/shm/ab_tone.wav"
SECS = 10.0


def s24(v):
    v &= 0xFFFFFF
    return v - 0x1000000 if v & 0x800000 else v


def ship():
    print(">>> 第一轮：出货位流 fir.bit（官方 audio IP），响 %d 秒" % SECS)
    sys.stdout.flush()
    ol = Overlay("/home/xilinx/fir.bit")
    a = ol.audio_codec_ctrl_0
    a.configure()
    a.set_volume(40)
    HT._write_tone(WAV, 440.0, -6.0, SECS)
    a.load(WAV)
    t0 = time.time()
    a.play()
    print("    放完，用了 %.1f 秒" % (time.time() - t0))
    sys.stdout.flush()


def ours():
    print(">>> 第二轮：i2sdbg.bit（我们的 i2s_duplex），响 %d 秒" % SECS)
    sys.stdout.flush()
    P.BITFILE = "i2sdbg.bit"
    P.HWHFILE = "i2sdbg.hwh"
    dma, ctrl, stat = P.load()
    dbg = P.MMIO(0x43C30000, 0x1000)
    dbg2 = P.MMIO(0x43C40000, 0x1000)
    P.set_ctrl(ctrl, 0)
    time.sleep(0.05)
    P.set_ctrl(ctrl, P.CTRL_RUN | (1 << 3))     # run | tone_en
    t0 = time.time()
    while time.time() - t0 < SECS:
        time.sleep(1.0)
        d0 = dbg.read(0x00)
        d2 = dbg2.read(0x00)
        print("    L=%8d  tog=%5d  wr=%5d"
              % (s24(d0 >> 8), (d2 >> 16) & 0xFFFF, d2 & 0xFFFF))
        sys.stdout.flush()
    P.set_ctrl(ctrl, 0)


def main():
    P.codec_init(1)
    ship()
    time.sleep(2.0)
    ours()
    print("== 结束，ctrl=0 ==")


if __name__ == "__main__":
    main()
