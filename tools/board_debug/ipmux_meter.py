"""用 codec 内部电平表判断：DAC 到底有没有收到 fir_ipmux 里那路数据。

sel=0 和 sel=1 用**完全相同的 codec 初始化**（a.configure + set_volume(60)），
只有 GPIO 那一位不同，避免上次 A/B 的初始化污染。

流程：
  1. sel=0 + 放 440 Hz wav（已知能出声）→ 扫出"会跟着信号动"的寄存器；
  2. 对这些寄存器，在 sel=1（我们的方波）和 sel=0（官方）下各采样一段，
     比跨度。

  两者跨度相当  -> codec 收到了我们的数据，问题在 codec 之后（模拟/耳机）
  sel=1 几乎不动 -> codec 没收下我们的数据

用法（板上，root）：
    python3 ipmux_meter.py --bit /home/xilinx/dma_exp/fir_ipmux_ila.bit
"""

import argparse
import os
import sys
import threading
import time

sys.path.insert(0, "/home/xilinx/dma_exp")
os.environ.setdefault("XILINX_XRT", "/usr")

import numpy as np

SWEEP = range(0x4000, 0x4100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default="/home/xilinx/dma_exp/fir_ipmux_ila.bit")
    ap.add_argument("--on", type=float, default=2.0)
    args = ap.parse_args()

    import gc
    gc.disable()

    import dma_audio_probe as P
    import hearing_tone as HT
    from pynq import MMIO, Overlay

    ol = Overlay(args.bit)
    a = ol.audio_codec_ctrl_0
    a.configure()
    a.set_volume(60)
    g = MMIO(0x43C10000, 0x1000)
    g.write(0, 0)

    HT._write_tone("/dev/shm/meter.wav", 440.0, -6.0, 8.0)
    a.load("/dev/shm/meter.wav")

    print("STEP1 sel=0 + 440wav 扫活动寄存器 ...")
    sys.stdout.flush()
    th = threading.Thread(target=a.play)
    th.start()
    time.sleep(0.4)
    hist = {}
    for _ in range(3):
        for r in SWEEP:
            v = P.i2c_rd(r)
            if v is not None:
                hist.setdefault(r, set()).add(v)
        time.sleep(0.15)
    live = sorted(r for r, vs in hist.items() if len(vs) > 1)
    print("  活动寄存器 %d 个: %s" % (len(live), " ".join("0x%04X" % r for r in live)))
    sys.stdout.flush()

    def sample(secs):
        out = {r: [] for r in live}
        t_end = time.time() + secs
        while time.time() < t_end:
            for r in live:
                out[r].append(P.i2c_rd(r))
        return out

    # 官方分支还在放音
    off_sel0 = sample(args.on)
    th.join(timeout=15)

    print("STEP2 sel=1（我们的方波，PS 不放音）...")
    sys.stdout.flush()
    g.write(0, 1)
    time.sleep(0.4)
    our = sample(args.on)
    g.write(0, 0)

    print("")
    print("  %-8s %-22s %-22s" % ("寄存器", "sel=0 官方 440", "sel=1 我们的方波"))
    for r in live:
        def stat(vals):
            v = [x for x in vals if x is not None]
            if not v:
                return "无读数"
            a_ = np.array(v, dtype=np.int64)
            return "min=%02X max=%02X 跨度=%d" % (a_.min(), a_.max(), a_.max() - a_.min())
        print("  %-8s %-22s %-22s" % ("0x%04X" % r, stat(off_sel0[r]), stat(our[r])))
    print("")
    print("  怎么读：sel=1 的跨度接近 sel=0 -> codec 收到数据，问题在模拟/耳机侧")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
