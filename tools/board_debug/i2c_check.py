"""最小 I2C 自检：读 → 写 0xF3 → 再读，看 codec 到底认不认我们的写入。

同时打印几个已知寄存器做量纲对照（0x4014 串口控制、0x4029 电源管理）。
板上 print 只能 ASCII。
"""
import sys

sys.path.insert(0, "/home/xilinx/dma_exp")

import dma_audio_probe as P

REGS = [0x4002, 0x4014, 0x4019, 0x401C, 0x401E, 0x4023, 0x4024, 0x4029]


def dump(tag):
    print("[%s]" % tag)
    for r in REGS:
        v = P.i2c_rd(r)
        print("   0x%04X = %s" % (r, "None(读失败)" if v is None else "0x%02X" % v))
    sys.stdout.flush()


def main():
    dump("before")
    if "read" in sys.argv:
        print("[read-only] stop here, nothing written")
        return

    print("[write] 0x401C=0x21 0x401E=0x41 0x4023=0xF3 0x4024=0xF3")
    for reg, val in ((0x401C, 0x21), (0x401E, 0x41),
                     (0x4023, 0xF3), (0x4024, 0xF3)):
        try:
            P.i2c_wr(reg, val)
            print("   wr 0x%04X <- 0x%02X   OK" % (reg, val))
        except Exception as ex:
            print("   wr 0x%04X <- 0x%02X   FAIL %s" % (reg, val, str(ex)[:70]))

    dump("after-write")


if __name__ == "__main__":
    main()
