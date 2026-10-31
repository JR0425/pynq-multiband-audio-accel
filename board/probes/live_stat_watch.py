#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""live_stat_watch.py —— 实时话筒模式下高速采一圈计数，找"啪"对应哪个计数器。

和 popwatch/soak600 的区别：不碰播放文件、不改任何设置，只读。
100 Hz 采 stat0（I2S ovf/und）、stat1、stat2（输出 FIFO）、POP_CNT、PEAK_IN/OUT，
只在值变化时打一行，结尾给 LIVE_WATCH {json}。

判读：
  啪的时候 stat0 涨     → I2S 输入侧丢字/溢出，数字侧问题
  啪的时候 stat2 涨     → 输出 FIFO 溢出
  啪的时候 POP_CNT 涨   → 核入口跳变
  全都不动              → 数字侧干净，问题在 ADC 模拟输入 / codec / 耳机

板上跑（live 服务要在线）：
    sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u live_stat_watch.py --seconds 60
"""
import argparse
import json
import sys
import time

from pynq import MMIO

BASE = 0x43C40000
OFF = {
    "stat0": 0x008,
    "stat1": 0x00C,
    "stat2": 0x010,
    "pin":   0x040,
    "pout":  0x044,
    "pop":   0x080,
    "popmax": 0x084,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--period", type=float, default=0.01)
    a = ap.parse_args()

    regs = MMIO(BASE, 0x1000)
    keys = list(OFF)
    prev = {k: regs.read(OFF[k]) for k in keys}
    # peak 是"自上次清零以来的最大值"，单独跟踪
    peak = {"pin": prev["pin"], "pout": prev["pout"]}
    changed = []
    n = 0

    t0 = time.monotonic()
    nxt = t0
    while True:
        now = time.monotonic()
        if now - t0 >= a.seconds:
            break
        if now < nxt:
            time.sleep(max(0.0, nxt - now))
        nxt += a.period
        n += 1
        t = round(time.monotonic() - t0, 2)
        cur = {k: regs.read(OFF[k]) for k in keys}
        for k in ("pin", "pout"):
            if cur[k] > peak[k]:
                peak[k] = cur[k]
        d = {}
        for k in keys:
            if cur[k] != prev[k]:
                d[k] = [prev[k], cur[k]]
        if d:
            changed.append({"t": t, "d": d})
            print("t=%.2f %s" % (t, json.dumps(d)))
        prev = cur

    out = {
        "seconds": a.seconds,
        "samples": n,
        "changed_events": len(changed),
        "stat0_changed": any("stat0" in c["d"] for c in changed),
        "stat1_changed": any("stat1" in c["d"] for c in changed),
        "stat2_changed": any("stat2" in c["d"] for c in changed),
        "pop_changed": any("pop" in c["d"] for c in changed),
        "peak_in_max": peak["pin"],
        "peak_out_max": peak["pout"],
        "first": min(c["t"] for c in changed) if changed else None,
    }
    print("LIVE_WATCH " + json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
