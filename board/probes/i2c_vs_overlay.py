"""同一套 I2C 读法，分别在有名的出货位流和我们的 fir_pl 下读 codec。

目的:判断"全读 0x01"是**overlay 相关**还是**板子/codec 状态**相关。
I2C 是 PS 外设，理论上跟 PL 无关 —— 如果不相关却能读出不同结果，
说明是 codec 本身的状态（比如被之前某次写弄拧了）。
板上 print 只能 ASCII。
"""
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")

from pynq import Overlay

import dma_audio_probe as P

REGS = [0x4000, 0x4002, 0x4014, 0x4019, 0x401C, 0x4023, 0x4029]


def read_all(tag):
    print("[%s]" % tag)
    out = []
    for r in REGS:
        v = P.i2c_rd(r)
        out.append("0x%04X=%s" % (r, "None" if v is None else "0x%02X" % v))
    print("   " + "  ".join(out))
    sys.stdout.flush()


def main():
    read_all("no overlay (bare)")

    print("[load] fir.bit (shipping overlay)")
    ol = Overlay("/home/xilinx/dma_exp/fir.bit")
    time.sleep(0.3)
    read_all("fir.bit")
    del ol
    time.sleep(0.3)

    print("[load] fir_pl.bit (ours)")
    ol = Overlay("/home/xilinx/dma_exp/fir_pl.bit")
    time.sleep(0.3)
    read_all("fir_pl.bit")


if __name__ == "__main__":
    main()
