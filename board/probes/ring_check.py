#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ring_check.py —— 直接读回播放环（0x43C50000），验 PS 写进去的数据本身对不对。
**只在板子上跑。**

    sudo env XILINX_XRT=/usr PYTHONIOENCODING=utf-8 \
        /usr/local/share/pynq-venv/bin/python3 ring_check.py --rounds 20

为什么要它：`ply_margin.py` 量出来读指针**不卡**（25 秒 0 次停顿）、平均速率正好
48828.125 Hz，可 `POP_CNT` 还在涨。那就说明环没被套圈，坏的是**数据**。

分辨两条路：

    环里读回来是干净的                    → PL 读口（BRAM PORTB / 地址 / 读延迟）
    环里读回来就有整块错位                 → AXI 写路径 / 写端数据

两条判据，逐轮做：

① 斜坡连续性。环里存的是 16 位斜坡（周期 65536）的连续一段，相邻两格高 16 位之差
   正常只有 +1 和 -65535（跨过斜坡自己那圈 32767→-32768 的接缝）。
   但要先扣掉写头那一格：环里一半是上一圈、一半是这一圈，两边差 16384 的整数倍。
   **干净的一轮 bad ≈ 1**（就是写头那格），远超 1 才是真坏了。

② 前后两次读逐字比对。环的内容除了写头附近是**不变的** —— 写端每 21 ms 才盖一块
   4 KB。所以连读两次，不同的字应该只出现在写头那一小段且是连续的。
   散开的不同 = 有东西在背后改环（或者读回来的是噪声）。

输出 RINGCHECK {json}，每轮一行 RINGCHECK_STEP。
"""
import argparse
import json
import sys
import time

from pynq import MMIO

PLY_BASE = 0x43C50000
RING = 16384


def read_ring(ply):
    try:
        arr = ply.array
    except AttributeError:
        arr = None
    if arr is not None:
        return [int(v) & 0xFFFFFFFF for v in arr[:]]
    return [ply.read(i * 4, 4) for i in range(RING)]


def ramp_bad(words):
    s = [(w >> 16) & 0xFFFF for w in words]
    bad = []
    for i in range(1, RING):
        d = s[i] - s[i - 1]
        if d != 1 and d != -65535:
            bad.append([i, d])
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--gap", type=float, default=0.5)
    ap.add_argument("--tag", default="run")
    a = ap.parse_args()

    ply = MMIO(PLY_BASE, RING * 4)
    steps = []
    for r in range(a.rounds):
        t0 = time.monotonic()
        wa = read_ring(ply)
        t1 = time.monotonic()
        wb = read_ring(ply)
        t2 = time.monotonic()

        bad = ramp_bad(wa)
        diff = [i for i in range(RING) if wa[i] != wb[i]]

        line = {
            "round": r,
            "read_ms": [round((t1 - t0) * 1e3, 1), round((t2 - t1) * 1e3, 1)],
            "ramp_bad": len(bad),
            "ramp_bad_first": bad[:6],
            "diff_n": len(diff),
            "diff_span": [diff[0], diff[-1]] if diff else None,
            "diff_scattered": (len(diff) > 0 and
                               (diff[-1] - diff[0] + 1) > 2 * len(diff)),
        }
        steps.append(line)
        print("RINGCHECK_STEP " + json.dumps(line), flush=True)
        time.sleep(a.gap)

    worst = max(steps, key=lambda s: s["ramp_bad"])
    summary = {
        "tag": a.tag,
        "rounds": a.rounds,
        "ramp_bad_max": worst["ramp_bad"],
        "ramp_bad_worst_round": worst["round"],
        "diff_n_max": max(s["diff_n"] for s in steps),
        "n_scattered": sum(1 for s in steps if s["diff_scattered"]),
        "verdict": "",
    }
    if summary["ramp_bad_max"] > 3 or summary["n_scattered"] > 0:
        summary["verdict"] = "RING_CORRUPT"
    else:
        summary["verdict"] = "RING_CLEAN"
    print("RINGCHECK " + json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
