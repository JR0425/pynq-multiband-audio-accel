"""加载出货位流 fir.bit，读同一组 codec 寄存器。

如果 fir.bit 下能读到有意义的分布（0x33/0xE5/0x03…），而 fir_pl.bit 下只能读到
0x00/0x01 这种常数，那"读不到"就和我们自己的 PL 有关（最大嫌疑是 MCLK 没送到 codec）。
两边都读不出东西，则说明是板子/codec 本身的状态问题。

板上 print 只能 ASCII。
"""
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")

from pynq import Overlay

import dma_audio_probe as P

REGS = [0x4000, 0x4002, 0x4014, 0x4019, 0x401C, 0x401E, 0x4023, 0x4024, 0x4029]


def read_all(tag):
    parts = []
    for r in REGS:
        v = P.i2c_rd(r)
        parts.append("0x%04X=%s" % (r, "--" if v is None else "%02X" % v))
    print("[%s] %s" % (tag, "  ".join(parts)))
    sys.stdout.flush()


def main():
    print("[load] fir.bit")
    ol = Overlay("/home/xilinx/dma_exp/fir.bit")
    time.sleep(0.5)
    read_all("fir.bit  right after load")

    print("[init] libaudio config_audio_pll/codec/select_mic")
    P.codec_init(1)
    time.sleep(0.3)
    read_all("fir.bit  after codec_init")

    print("[note] 0x4023 出厂默认约 0xE5；若这里读到 0x01/0x00 说明 I2C 没有真正读回")


if __name__ == "__main__":
    main()
