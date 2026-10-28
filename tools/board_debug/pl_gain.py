"""pl_gain —— 在板上单独改四段增益（不重启、不放音）。

用法（板上）：

    python3 pl_gain.py --read            # 只读回当前四段增益 + 是否还在爬坡
    python3 pl_gain.py 18 18 18 18       # 四段都 +18 dB（说话能听清的档）
    python3 pl_gain.py 0 6 12 18         # 低频不补、高频补满（"高频听损"的处方）
    python3 pl_gain.py 0 0 0 0           # 归零（A/B 的对照档）
    python3 pl_gain.py --div 8 18 18 18 18   # 斜坡慢一点（div 越大越慢）

它只碰 fir_pl_regs（0x43C40000）里的 GAIN 与 RAMP_DIV，不碰 ctrl，
所以可以在放音过程中随时改 —— 这就是演示里"拖滑杆"的最小可验证版本。

板上 print 只能 ASCII。
"""
import argparse
import math
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")

BANK0 = 0x100
BANK1 = 0x200
OFF_GAIN = 0x00
OFF_RAMP_DIV = 0x70
OFF_RAMP_BUSY = 0x74
GAIN_UNITY = 256
BIT = "/home/xilinx/dma_exp/fir_pl.bit"


def db_to_gain(db):
    return max(-32768, min(32767, int(round(GAIN_UNITY * 10.0 ** (float(db) / 20.0)))))


def gain_to_db(g):
    g = int(g) & 0xFFFF
    if g == 0:
        return -120.0
    if g > 32767:                      # 负数按 16 位补码解释
        g -= 65536
    if g <= 0:
        return -120.0
    return 20.0 * math.log10(float(g) / GAIN_UNITY)


def find_regs(ol):
    from pynq import MMIO
    for name, d in ol.ip_dict.items():
        if name.startswith("fir_pl_regs") and d.get("phys_addr") is not None:
            return MMIO(d["phys_addr"], 0x1000), d["phys_addr"]
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gain_db", nargs="*", type=float,
                    help="四段增益（dB）。给 1 个值就四段相同，给 4 个按段")
    ap.add_argument("--read", action="store_true")
    ap.add_argument("--div", type=int, default=None, help="斜坡移位量（默认不动，6 = 1.3 ms）")
    ap.add_argument("--bit", default=BIT)
    args = ap.parse_args()

    from pynq import Overlay
    ol = Overlay(args.bit)
    time.sleep(0.2)

    regs, base = find_regs(ol)
    if regs is None:
        print("no fir_pl_regs in this overlay -- this bitstream has no runtime gains")
        return 1
    print("fir_pl_regs @ 0x%08X" % base)

    if args.read or not args.gain_db:
        cur = [regs.read(BANK0 + OFF_GAIN + 4 * i) & 0xFFFF for i in range(4)]
        print("GAIN raw  = %s" % cur)
        print("GAIN dB   = %s" % [round(gain_to_db(g), 1) for g in cur])
        print("RAMP_DIV  = %d" % regs.read(OFF_RAMP_DIV))
        print("RAMP_BUSY = 0x%X  (bit0 left, bit1 right)" % (regs.read(OFF_RAMP_BUSY) & 3))
        return 0

    vals = args.gain_db
    if len(vals) == 1:
        vals = vals * 4
    if len(vals) != 4:
        print("need 1 or 4 gain values, got %d" % len(vals))
        return 2

    if args.div is not None:
        regs.write(OFF_RAMP_DIV, int(args.div) & 0xFFFF)

    raw = [db_to_gain(v) for v in vals]
    for i in range(4):
        regs.write(BANK0 + OFF_GAIN + 4 * i, raw[i])
        regs.write(BANK1 + OFF_GAIN + 4 * i, raw[i])
    print("wrote dB %s -> raw %s" % ([round(v, 1) for v in vals], raw))

    # 斜坡 6.5 ms 就爬完，等 100 ms 再看 busy
    time.sleep(0.1)
    back = [regs.read(BANK0 + OFF_GAIN + 4 * i) & 0xFFFF for i in range(4)]
    busy = regs.read(OFF_RAMP_BUSY) & 3
    print("readback  = %s  (expect %s)" % (back, raw))
    print("ramp_busy = 0x%X  (0 = 已到位)" % busy)
    ok = (back == raw) and busy == 0
    print("OK" if ok else "MISMATCH")
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
