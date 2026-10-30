#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""soak600.py —— E3 长跑：播放 + 每秒采一组寄存器/温度，**只在板子上跑**。

    sudo env XILINX_XRT=/usr PYTHONIOENCODING=utf-8 \
        /usr/local/share/pynq-venv/bin/python3 soak600.py --seconds 600

和 popwatch 的区别：这个连 stat0（i2s 的 ovf/und）、stat2（输出 FIFO 溢出 /
最大 TX 水位）和芯片温度一起采，回答的是「长跑会不会崩/会不会丢字」，
不只是「有没有跳变」。结尾一行 SOAK {json} 给主机侧取。
"""
import argparse
import json
import sys
import time

from pynq import MMIO

BASE = 0x43C40000
OFF_STAT0 = 0x008
OFF_STAT1 = 0x00C
OFF_STAT2 = 0x010
OFF_PEAK_IN = 0x40
OFF_PEAK_OUT = 0x44
OFF_POP_CNT = 0x80
OFF_POP_MAX = 0x84

PLAY_PATH = "/dev/shm/fir_pl_play.txt"
TEMP_PATH = "/sys/class/thermal/thermal_zone0/temp"


def temp_c():
    try:
        with open(TEMP_PATH) as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default="/home/xilinx/dma_exp/wav/demo_bands_48k.wav")
    ap.add_argument("--seconds", type=float, default=600.0)
    ap.add_argument("--period", type=float, default=1.0)
    ap.add_argument("--tag", default="e3")
    a = ap.parse_args()

    with open(PLAY_PATH, "w") as f:
        f.write(a.wav + "\n")

    regs = MMIO(BASE, 0x1000)
    time.sleep(3.0)

    rows = []
    t0 = time.monotonic()
    nxt = t0
    while True:
        now = time.monotonic()
        if now - t0 >= a.seconds:
            break
        if now < nxt:
            time.sleep(nxt - now)
        nxt += a.period
        rows.append({
            "t": round(time.monotonic() - t0, 2),
            "stat0": regs.read(OFF_STAT0),
            "stat1": regs.read(OFF_STAT1),
            "stat2": regs.read(OFF_STAT2),
            "pin": regs.read(OFF_PEAK_IN),
            "pout": regs.read(OFF_PEAK_OUT),
            "pop": regs.read(OFF_POP_CNT),
            "popmax": regs.read(OFF_POP_MAX),
            "temp": temp_c(),
        })

    with open(PLAY_PATH, "w") as f:
        f.write("off\n")

    s0 = [r["stat0"] for r in rows]
    pops = [r["pop"] for r in rows]
    temps = [r["temp"] for r in rows if r["temp"] is not None]
    out = {
        "tag": a.tag,
        "seconds": a.seconds,
        "rows": len(rows),
        "stat0_first": s0[0],
        "stat0_last": s0[-1],
        "stat0_changed": any(v != s0[0] for v in s0),
        "stat1_first": rows[0]["stat1"],
        "stat1_last": rows[-1]["stat1"],
        "stat2_first": rows[0]["stat2"],
        "stat2_last": rows[-1]["stat2"],
        "pop_first": pops[0],
        "pop_last": pops[-1],
        "pop_delta": (pops[-1] - pops[0]) & 0xFFFFFFFF,
        "popmax": rows[-1]["popmax"],
        "peak_in_max": max(r["pin"] for r in rows),
        "peak_out_max": max(r["pout"] for r in rows),
        "temp_first": temps[0] if temps else None,
        "temp_last": temps[-1] if temps else None,
        "temp_max": max(temps) if temps else None,
    }
    print("SOAK " + json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
