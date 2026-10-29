"""PL 直连：ctrl 回读 + 三档听音。

为什么要回读：AXI GPIO 的宽度/通道如果和 RTL 的 ctrl[31:0] 对不上，高位会被截掉
—— 那样 ctrl[3] 永远写不进去，测试音也就永远不响。本脚本把写进去的值立刻读回来
打印，一眼能看出有没有被截。

顺序：A 只过前端 2 秒 → B 再加压缩 2 秒 → C 再叠 440 测试音 6 秒。
板上 print 只能 ASCII，所以全用英文。
"""
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")

from pynq import Overlay, MMIO

import dma_audio_probe as P

BIT = "/home/xilinx/dma_exp/fir_pl.bit"

RUN = 1 << 0
DRC = 1 << 1
FLUSH = 1 << 2
TONE = 1 << 3
CLR = 1 << 8


def find(ol, name, base):
    """按名字找 IP；找不到再退回裸地址（和 pl_direct_probe 一个路数）。"""
    if name in ol.ip_dict:
        return getattr(ol, name).mmio
    for k, v in ol.ip_dict.items():
        if v.get("phys_addr") == base:
            return getattr(ol, k).mmio
    return MMIO(base, 0x1000)


def main():
    ol = Overlay(BIT)
    P.codec_init(1)
    print("[codec] HP=0x%02X  MIX=0x%02X" % (P.i2c_rd(0x4023), P.i2c_rd(0x401C)))

    for k in sorted(ol.ip_dict):
        a = ol.ip_dict[k].get("phys_addr")
        if a is not None:
            print("  %-16s 0x%08X" % (k, a))

    ctrl = find(ol, "gpio_ctrl", 0x43C00000)

    # 1) 写入是否被截:0x10B 同时带 ctrl[3] 与 ctrl[8]
    ctrl.write(0, RUN | DRC | TONE | CLR)
    time.sleep(0.05)
    v = ctrl.read(0)
    print("[readback] wrote 0x0000010B, got 0x%08X" % v)
    if (v & TONE) == 0:
        print("!! ctrl[3] did NOT stick -> GPIO is truncating high bits")
    if (v & CLR) == 0:
        print("   ctrl[8] reads 0 (it is a one-shot pulse, expected)")

    # 2) 三档
    plan = (("A front-end only   (2s)", RUN, 2.0),
            ("B front-end + DRC  (2s)", RUN | DRC, 2.0),
            ("C DRC + 440 tone   (6s)", RUN | DRC | TONE, 6.0))
    for tag, val, secs in plan:
        ctrl.write(0, val)
        time.sleep(0.05)
        print("[now] %-26s wrote 0x%08X  read 0x%08X"
              % (tag, val, ctrl.read(0)))
        sys.stdout.flush()
        time.sleep(secs)

    ctrl.write(0, 0)
    print("[done]")


if __name__ == "__main__":
    main()
