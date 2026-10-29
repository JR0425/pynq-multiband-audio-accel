"""上电复位后，在 fir_pl.bit 下重测：读 -> codec_init -> 读 -> 三档听音。

上一轮那些 0x01 是**上电前** codec 被写拧的状态，不是 fir_pl 的问题。
本脚本按正确顺序重做一遍，顺序很重要：
  1. 先加载 overlay（codec_addr 由 PL 驱动，PL 不配置地址脚就悬空）
  2. 读一遍看是不是出厂默认
  3. codec_init 再读一遍，确认写入生效
  4. 三档听音，最后一档 6 秒是 PL 内部 440 测试音
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
    print("[load] fir_pl.bit")
    ol = Overlay(BIT)
    time.sleep(0.4)
    read_all("right after load")

    print("[init] codec_init")
    P.codec_init(1)
    time.sleep(0.2)
    read_all("after codec_init")

    hp = P.i2c_rd(0x4023)
    if hp is None or (hp & 0x02) == 0:
        print("!! HP still muted (0x%02X) -> stop, do not expect sound" % (hp or 0))
        return

    ctrl = ol.gpio_ctrl.mmio
    plan = (("A front-end only  (2s)", RUN, 2.0),
            ("B + DRC           (2s)", RUN | DRC, 2.0),
            ("C + 440 tone      (6s)", RUN | DRC | TONE, 6.0))
    for tag, val, secs in plan:
        ctrl.write(0, val)
        print("[now] %-24s ctrl=0x%02X" % (tag, val))
        sys.stdout.flush()
        time.sleep(secs)

    ctrl.write(0, 0)
    print("[done]")


if __name__ == "__main__":
    main()
