"""fir_dma 的 DMA 现场诊断（临时）

不走 PYNQ 的 DMA 类，直接读写寄存器，把 S2MM/MM2S 各自的行为分开看：

  1. 打印两路的 DMACR/DMASR/地址/长度，确认 IP 活着、地址对得上
  2. 手动发一次 S2MM（run=1 让 I2S 采起来，FIFO 里垫上数据），
     每 0.5 ms 采一次 DMASR，把状态跳变连同时刻打出来
  3. 读回 S2MM_LENGTH，看搬没搬、搬了多少
  4. 软复位 S2MM，再用同样方式试 MM2S（灌一段正弦），
     同时看 stat1 的 tx FIFO 占用有没有在降

用法（板上）：
    echo xilinx | sudo -S sh -c "XILINX_XRT=/usr \\
        /usr/local/share/pynq-venv/bin/python3 dma_audio_diag.py"
"""

import os
import sys
import time

import numpy as np
from pynq import MMIO, Overlay, allocate

HERE = os.path.dirname(os.path.abspath(__file__))
DMA_BASE = 0x40400000
GPIO_CTRL = 0x43C00000
GPIO_STAT = 0x43C10000

# AXI DMA 寄存器
MM2S_DMACR = 0x00
MM2S_DMASR = 0x04
MM2S_SA    = 0x18
MM2S_SA_MSB = 0x1C
MM2S_LEN   = 0x28
S2MM_DMACR = 0x30
S2MM_DMASR = 0x34
S2MM_DA    = 0x48
S2MM_DA_MSB = 0x4C
S2MM_LEN   = 0x58

SR_BITS = [(0, "Halted"), (1, "Idle"), (2, "SGIncld"), (3, "SGIntErr"),
           (4, "DMAIntErr"), (5, "DMASlvErr"), (6, "DMADecErr"),
           (8, "SGDecErr"), (12, "IOC_Irq"), (13, "Dly_Irq"), (14, "Err_Irq")]


def sr_text(v):
    on = [n for b, n in SR_BITS if v & (1 << b)]
    return "0x%08X %s" % (v, "|".join(on) if on else "(none)")


def regs(dma, label):
    print("  %s" % label)
    print("    DMACR = 0x%08X" % dma.read(0 if label == "MM2S" else 0x30))
    print("    DMASR = %s" % sr_text(dma.read(MM2S_DMASR if label == "MM2S"
                                             else S2MM_DMASR)))


def main():
    ol = Overlay(os.path.join(HERE, "fir_dma.bit"))
    dma = MMIO(DMA_BASE, 0x10000)
    ctrl = MMIO(GPIO_CTRL, 0x1000)
    stat = MMIO(GPIO_STAT, 0x1000)

    def st():
        s0 = stat.read(0x00)
        s1 = stat.read(0x08)
        return "ovf=%d und=%d tx=%d cap=%d" % (s0 & 0xFFFF, s0 >> 16,
                                               s1 & 0xFFFF, s1 >> 16)

    print("== 0. 上电状态 ==")
    regs(dma, "MM2S")
    regs(dma, "S2MM")
    print("  stat:", st())

    # codec 只要出数据就得先配好（录音方向）
    sys.path.insert(0, HERE)
    import cffi
    ffi = cffi.FFI()
    lib = ffi.dlopen("/home/xilinx/pynq/lib/libaudio.so")
    ffi.cdef("void config_audio_pll(int i);")
    ffi.cdef("void config_audio_codec(int i);")
    ffi.cdef("void select_mic(int i);")
    lib.config_audio_pll(1)
    lib.config_audio_codec(1)
    lib.select_mic(1)

    nw = 960
    buf = allocate(shape=(nw,), dtype=np.int32)

    # ---------------- S2MM ----------------
    print("\n== 1. S2MM：直接写寄存器 ==")
    ctrl.write(0, 1 << 2)          # flush
    time.sleep(0.02)
    ctrl.write(0, 0)               # run=0，只收不发

    # 软复位 S2MM，清掉可能已经挂上的错误
    dma.write(S2MM_DMACR, 0x0004)
    for _ in range(10000):
        if dma.read(S2MM_DMASR) & 0x1:
            break
    else:
        print("  !! S2MM 软复位一直没回到 Halted")

    dma.write(S2MM_DMACR, 0x0001)
    print("  复位后 DMASR = %s" % sr_text(dma.read(S2MM_DMASR)))

    time.sleep(0.05)               # 让 I2S 往采集 FIFO 里垫 50 ms
    print("  垫完 stat:", st(), "  （cap 应该接近 1024）")

    dma.write(S2MM_DA, buf.physical_address & 0xFFFFFFFF)
    dma.write(S2MM_DA_MSB, (buf.physical_address >> 32) & 0xFFFFFFFF)
    print("  目标物理地址 = 0x%X" % buf.physical_address)
    t0 = time.perf_counter()
    dma.write(S2MM_LEN, nw * 4)

    last = None
    while True:
        sr = dma.read(S2MM_DMASR)
        t = (time.perf_counter() - t0) * 1e6
        if sr != last:
            print("  %8.1f us  %s" % (t, sr_text(sr)))
            last = sr
        if sr & 0x10 or sr & 0x60 or sr & 0x2:
            break
        if t > 200000:
            print("  !! 200 ms 还没结束")
            break
        time.sleep(0.0005)

    print("  S2MM_LEN 剩余 = %d 字节（写进去的是 %d）"
          % (dma.read(S2MM_LEN), nw * 4))
    buf.invalidate()
    x = np.array(buf, dtype=np.int32)
    x24 = x >> 8
    print("  rms=%.1f peak=%d  前 8 个: %s"
          % (np.sqrt(np.mean(x24.astype(np.float64) ** 2)), np.max(np.abs(x24)),
             " ".join("%d" % v for v in x[:8])))

    # ---------------- MM2S ----------------
    print("\n== 2. MM2S：直接写寄存器 ==")
    ctrl.write(0, 1)               # run=1
    dma.write(MM2S_DMACR, 0x0004)
    for _ in range(10000):
        if dma.read(MM2S_DMASR) & 0x1:
            break
    else:
        print("  !! MM2S 软复位一直没回到 Halted")
    dma.write(MM2S_DMACR, 0x0001)
    print("  复位后 DMASR = %s" % sr_text(dma.read(MM2S_DMASR)))

    tone = (np.arange(nw * 2) % 64 * 1000).astype(np.int32)
    pbuf = allocate(shape=(nw * 2,), dtype=np.int32)
    pbuf[:] = tone
    pbuf.flush()

    print("  发之前 stat:", st())
    dma.write(MM2S_SA, pbuf.physical_address & 0xFFFFFFFF)
    dma.write(MM2S_SA_MSB, (pbuf.physical_address >> 32) & 0xFFFFFFFF)
    t0 = time.perf_counter()
    dma.write(MM2S_LEN, nw * 2 * 4)

    last = None
    while True:
        sr = dma.read(MM2S_DMASR)
        t = (time.perf_counter() - t0) * 1e6
        if sr != last:
            print("  %8.1f us  %s   %s" % (t, sr_text(sr), st()))
            last = sr
        if sr & 0x10 or sr & 0x60 or sr & 0x2:
            break
        if t > 200000:
            print("  !! 200 ms 还没结束")
            break
        time.sleep(0.0005)
    print("  MM2S_LEN 剩余 = %d 字节（写进去的是 %d）"
          % (dma.read(MM2S_LEN), nw * 2 * 4))
    print("  发完 stat:", st())

    ctrl.write(0, 0)
    print("\n== 结束 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
