# PEAK_IN 偶读 0 的定位探针（批 1 位流）
#
# 板上用 1 kHz 正弦（幅度 12000）当音源 —— 纯音不可能出现 768 采样的全零窗，
# 所以如果 PEAK_IN 还读到 0，就是 RTL/寄存器的问题，不是"曲子有静音段"。
#
# 用法：python3 peak_probe.py [秒数]
import sys
import threading
import time

import numpy as np
from pynq import Overlay, MMIO
import dma_audio_probe as P

BRAM_BASE = 0x43C50000
RING = 16384
CHUNK = 1024
CHUNK_T = CHUNK / 48000.0
REGS = 0x43C40000
BANK0 = 0x100


def words_of(seg):
    return (np.asarray(seg, dtype=np.uint64) & 0xFFFF).astype(np.uint32) << 16


def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
    ol = Overlay("/home/xilinx/dma_exp/fir_pl.bit")
    time.sleep(0.3)
    P.codec_init()

    regs = MMIO(REGS, 0x1000)
    bram = MMIO(BRAM_BASE, 0x10000)
    ctrl = None
    for name, d in ol.ip_dict.items():
        if name.startswith("gpio_ctrl"):
            ctrl = MMIO(d["phys_addr"], 0x1000)

    print("ID=0x%08x" % regs.read(0x00))

    n = 48000 * 4
    t = np.arange(n)
    smp = (12000 * np.sin(2 * np.pi * 1000.0 * t / 48000.0)).astype(np.int64)

    bram.write(0, words_of(smp[:RING]).tobytes())

    # 0 dB 增益（+30 会削顶，这里只看 PEAK_IN）
    for i in range(4):
        regs.write(BANK0 + 4 * i, 256)
    regs.write(0x170, 5)

    stop = threading.Event()

    def feeder():
        pos = RING
        next_t = time.time() + CHUNK_T
        while not stop.is_set():
            idx = np.arange(pos, pos + CHUNK) % len(smp)
            bram.write((pos % RING) * 4, words_of(smp[idx]).tobytes())
            pos += CHUNK
            now = time.time()
            if now < next_t:
                time.sleep(next_t - now)
            next_t += CHUNK_T

    threading.Thread(target=feeder, daemon=True).start()

    time.sleep(0.2)
    regs.write(0x028, RING)
    regs.write(0x024, 0x3)
    ctrl.write(0, 0x03 | 0x20)          # run | drc_en | src=wav
    t_play = time.time()

    print("t_ms,smp_cnt,peak_in,peak_out,stat0,ctrl,cnt_mod768,dt_us")
    t0 = time.time()
    tot = 0
    zeros = 0
    dts = []
    zt = []
    while True:
        now = time.time() - t0
        if now >= dur:
            break
        t1 = time.time()
        sc = regs.read(0x14)
        pi = regs.read(0x40) & 0xFFFF
        po = regs.read(0x44) & 0xFFFF
        s0 = regs.read(0x08)
        c = regs.read(0x04)
        dt = (time.time() - t1) * 1e6
        dts.append(dt)
        print("%.1f,%d,%d,%d,0x%08x,0x%08x,%d,%.0f"
              % (now * 1000, sc, pi, po, s0, c, sc % 768, dt))
        tot += 1
        if pi == 0:
            zeros += 1
            zt.append((time.time() - t_play) * 1000.0)
        sys.stdout.flush()

    print("# 五连读：PEAK_IN 为 0  %d/%d  读取耗时 中位 %.0f us 最大 %.0f us"
          % (zeros, tot, float(np.median(dts)), max(dts)))
    if zt:
        print("# 这些 0 出现在放音后 %.1f ~ %.1f ms（首窗 %d 采样 = 15.7 ms）"
              % (min(zt), max(zt), 768))

    # ---- 分段：只读一个寄存器，看零是不是"多读数打架"造成的 ----
    def solo(addr, label, secs=1.5):
        t0s = time.time()
        n = z = 0
        prev = None
        flips = 0
        while time.time() - t0s < secs:
            v = regs.read(addr) & 0xFFFF
            n += 1
            if v == 0:
                z += 1
            if prev is not None and v != prev:
                flips += 1
            prev = v
        print("# %s：0 出现 %d/%d  变化次数 %d" % (label, z, n, flips))

    solo(0x40, "只读 PEAK_IN(0x40)")
    solo(0x44, "只读 PEAK_OUT(0x44)")
    solo(0x08, "只读 STAT0(0x08)")

    # ---- 五连读里到底谁被读坏：ID 是死值 0x504C0002，绝不可能合法读到 0 ----
    t0b = time.time()
    nb = 0
    id_bad = pi_bad = po_bad = 0
    row_bad = 0
    samples = []
    while time.time() - t0b < 3.0:
        i0 = regs.read(0x00)
        pi = regs.read(0x40) & 0xFFFF
        po = regs.read(0x44) & 0xFFFF
        s0 = regs.read(0x08)
        c = regs.read(0x04)
        nb += 1
        bad = 0
        if i0 != 0x504C0002:
            id_bad += 1
            bad = 1
        if pi == 0:
            pi_bad += 1
            bad = 1
        if po == 0:
            po_bad += 1
            bad = 1
        if bad:
            row_bad += 1
            if len(samples) < 6:
                samples.append("ID=0x%08x PI=%d PO=%d S0=0x%08x C=0x%08x"
                               % (i0, pi, po, s0, c))
    print("# 五连读 %d 轮：ID 坏 %d、PEAK_IN==0 %d、PEAK_OUT==0 %d、坏轮 %d"
          % (nb, id_bad, pi_bad, po_bad, row_bad))
    for s in samples:
        print("#   " + s)

    # ---- 定位：是不是"先读 0x14 再读 0x40"才出零 ----
    def pair(a1, a2, label, secs=3.0):
        t0p = time.time()
        n = z = 0
        while time.time() - t0p < secs:
            regs.read(a1)
            if (regs.read(a2) & 0xFFFF) == 0:
                z += 1
            n += 1
        print("# %s：第二个寄存器读到 0  %d/%d" % (label, z, n))

    pair(0x14, 0x40, "先读 0x14 再读 0x40")
    pair(0x00, 0x40, "先读 0x00 再读 0x40")
    pair(0x40, 0x14, "先读 0x40 再读 0x14")
    pair(0x14, 0x44, "先读 0x14 再读 0x44")

    solo(0x14, "只读 SMP_CNT(0x14)", 1.5)

    regs.write(0x024, 0)
    ctrl.write(0, 0x03)
    print("stopped")


if __name__ == "__main__":
    main()
