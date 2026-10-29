"""S2MM 最小参数扫描（临时）

同一份位流下换长度/地址，看 DMAIntErr 是不是跟某个参数绑定。
每轮先软复位 S2MM，再写 DA / LEN，等 200 ms，打印 DMASR 和 LEN 剩余。

用法（板上）：
    echo xilinx | sudo -S sh -c "XILINX_XRT=/usr \\
        /usr/local/share/pynq-venv/bin/python3 dma_s2mm_sweep.py"
"""

import os
import sys
import time

import numpy as np
from pynq import MMIO, Overlay, allocate

HERE = os.path.dirname(os.path.abspath(__file__))
DMA_BASE = 0x40400000
GPIO_CTRL = 0x43C00000

S2MM_DMACR = 0x30
S2MM_DMASR = 0x34
S2MM_DA    = 0x48
S2MM_DA_MSB = 0x4C
S2MM_LEN   = 0x58


SR_BITS = [(0x0001, "Halted"), (0x0002, "Idle"), (0x0008, "IntErr"),
           (0x0010, "SlvErr"), (0x0020, "DecErr"), (0x1000, "IOC"),
           (0x4000, "ErrIrq")]
SR_ANY = 0x0078 | 0x0002


def sr_str(sr):
    names = [n for b, n in SR_BITS if sr & b]
    return "0x%08X[%s]" % (sr, "|".join(names) if names else "-")


def run_one(dma, buf, off, nbytes, ctrl):
    # TLAST 依靠 ctrl[31:16] 给的块长，必须和这次的传输长度一致
    ctrl.write(0, ((nbytes // 4 - 1) & 0xFFFF) << 16)
    dma.write(S2MM_DMACR, 0x0004)           # 软复位
    for _ in range(200000):
        if dma.read(S2MM_DMASR) & 0x1:
            break
    dma.write(S2MM_DMACR, 0x0001)           # RS=1
    pa = buf.physical_address + off
    dma.write(S2MM_DA, pa & 0xFFFFFFFF)
    dma.write(S2MM_DA_MSB, (pa >> 32) & 0xFFFFFFFF)
    t0 = time.perf_counter()
    dma.write(S2MM_LEN, nbytes)

    sr = 0
    while time.perf_counter() - t0 < 0.2:
        sr = dma.read(S2MM_DMASR)
        if sr & SR_ANY:
            break
    dt = (time.perf_counter() - t0) * 1e6
    left = dma.read(S2MM_LEN)
    print("  off=%-5d len=%-6d addr=0x%08X  %7.1f us  %s  left=%-6d"
          % (off, nbytes, pa, dt, sr_str(sr), left))
    return sr, left


def main():
    ol = Overlay(os.path.join(HERE, "fir_dma.bit"))
    dma = MMIO(DMA_BASE, 0x10000)
    ctrl = MMIO(GPIO_CTRL, 0x1000)

    nw = 4096
    buf = allocate(shape=(nw,), dtype=np.int32)
    print("  缓冲物理地址 0x%08X  （%d 字节）"
          % (buf.physical_address, buf.nbytes))

    import cffi
    ffi = cffi.FFI()
    lib = ffi.dlopen("/home/xilinx/pynq/lib/libaudio.so")
    ffi.cdef("void config_audio_pll(int i);")
    ffi.cdef("void config_audio_codec(int i);")
    ffi.cdef("void select_mic(int i);")
    lib.config_audio_pll(1)
    lib.config_audio_codec(1)
    lib.select_mic(1)

    ctrl.write(0, 1 << 2)
    time.sleep(0.02)
    ctrl.write(0, 0)
    time.sleep(0.05)

    print("\n-- 长度扫描（地址不动）--")
    for nbytes in (8, 16, 64, 512, 3840, 16384):
        run_one(dma, buf, 0, nbytes, ctrl)

    print("\n-- 地址扫描（长度固定 3840 = 480 帧）--")
    for off in (0, 4, 8, 64, 4096):
        run_one(dma, buf, off, 3840, ctrl)

    print("\n-- 再试一次 8 字节，看看是不是偶发 --")
    run_one(dma, buf, 0, 8, ctrl)
    run_one(dma, buf, 0, 8, ctrl)

    return 0


if __name__ == "__main__":
    sys.exit(main())
