"""PL 直连：一条命令做完整验收（板上跑）。

顺序（顺序本身就是结论，别改）：
  1. 加载 overlay      —— codec_addr 由 PL 驱动，不加载就读不到寄存器
  2. 读一遍            —— 应是出厂值（0x4023=0xE5 一类）
  3. codec_init()      —— libaudio 配置 + codec_unmute()
  4. 再读一遍          —— 0x4023 应变成 0xF3，且 bit1=1（未静音）
  5. ctrl=0x0B 放 5 秒  —— PL 内部 440 方波，不经 ADC，验"播放链路"
  6. 静音 1 秒
  7. ctrl=0x03 放 10 秒 —— 话筒→前端→压缩→+18 dB，验"话筒链路"
  8. ctrl=0 收尾

判读：
  第 5 段听到「嘟」   ⇒ PL 音源 → 输出 FIFO → i2s_duplex → 引脚 → codec → 耳机 全通
  第 7 段吹气听到声   ⇒ 话筒 → ADC → 前端 → 压缩 → 增益 → 同上 全通
  两段都听不到，先看第 4 步有没有输出 "HP ok"，再按 POWER-CYCLE 提示处理。

板上 print 只能 ASCII。
"""
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")

from pynq import Overlay

import dma_audio_probe as P

BIT = "/home/xilinx/dma_exp/fir_pl.bit"

REGS = [0x4000, 0x4014, 0x4019, 0x401C, 0x401E, 0x4023, 0x4024, 0x4029]

RUN = 1 << 0
DRC = 1 << 1
TONE = 1 << 3


def read_all(tag):
    parts = []
    for r in REGS:
        v = P.i2c_rd(r)
        parts.append("0x%04X=%s" % (r, "--" if v is None else "%02X" % v))
    print("[%s] %s" % (tag, "  ".join(parts)))
    sys.stdout.flush()


def main():
    print("[1] load overlay")
    ol = Overlay(BIT)
    time.sleep(0.4)

    print("[2] registers right after load (factory-ish)")
    read_all("load")

    print("[3] codec_init()")
    P.codec_init(1)
    time.sleep(0.2)

    print("[4] registers after codec_init (expect 0x4023=F3)")
    read_all("init")

    hp = P.i2c_rd(0x4023)
    if hp is None or (hp & 0x02) == 0:
        print("!! HP=0x%02X has bit1=0 -> headphone muted." % (hp or 0))
        print("!! POWER-CYCLE the board (unplug 5 s), then run again.")
        return

    print("[4] HP ok (bit1=1, unmuted)")

    ctrl = ol.gpio_ctrl.mmio

    print("[5] tone 440 Hz, 5 s  (ctrl=0x%02X)  <<< LISTEN: beep" % (RUN | DRC | TONE))
    ctrl.write(0, RUN | DRC | TONE)
    time.sleep(5.0)

    print("[6] mute 1 s")
    ctrl.write(0, 0)
    time.sleep(1.0)

    print("[7] mic path 10 s  (ctrl=0x%02X)  <<< BLOW at the mic" % (RUN | DRC))
    ctrl.write(0, RUN | DRC)
    time.sleep(10.0)

    ctrl.write(0, 0)
    print("[8] done, ctrl=0")


if __name__ == "__main__":
    main()
