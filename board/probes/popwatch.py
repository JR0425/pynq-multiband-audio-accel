#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""popwatch.py —— 只用 RTL 的 POP_CNT(0x80)/POP_MAX(0x84) 数「啪」，**只在板子上跑**。

    sudo env XILINX_XRT=/usr PYTHONIOENCODING=utf-8 \
        /usr/local/share/pynq-venv/bin/python3 popwatch.py \
        --wav /home/xilinx/dma_exp/wav/ramp.wav --seconds 20 --tag ctrl

为什么不看 /api/scope：抓取 BRAM 的读口被当成字节地址解释（读下标 i 拿到第
i>>2 个字），窗口只够到 2048 字缓冲的前 1024 字，读数本身就是拼出来的。
POP_CNT 是 RTL 在核入口上算的 |Δ|>8192，不经过那条通路。

判据：换成 16384 样本的斜坡 wav 后，`words[q]` 和环槽位 q 一一对应，
写端每圈写回去的还是同一份内容 —— 环等于静态的。
    静态环下 POP 还在涨  → 坏在读通路（PL 读 BRAM / fe_din_vld）
    静态环下 POP 归零    → 坏在写端（Player 写的 pos / AXI 写）
"""
import argparse
import json
import sys
import time

from pynq import MMIO

BASE = 0x43C40000
OFF_POP_CNT = 0x80
OFF_POP_MAX = 0x84
PLAY_PATH = "/dev/shm/fir_pl_play.txt"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--settle", type=float, default=3.0)
    ap.add_argument("--tag", default="run")
    a = ap.parse_args()

    with open(PLAY_PATH, "w") as f:
        f.write(a.wav + "\n")
    regs = MMIO(BASE, 0x1000)
    time.sleep(a.settle)

    cnt0 = regs.read(OFF_POP_CNT)
    max0 = regs.read(OFF_POP_MAX)
    rows = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < a.seconds:
        rows.append((round(time.monotonic() - t0, 3),
                     regs.read(OFF_POP_CNT), regs.read(OFF_POP_MAX)))
        time.sleep(0.05)

    # 每个采样点相对上一点涨了几个：0 = 这段时间干净
    incs = [rows[i][1] - rows[i - 1][1] for i in range(1, len(rows))]
    d = (rows[-1][1] - cnt0) & 0xFFFFFFFF
    summary = {
        "tag": a.tag,
        "wav": a.wav.rsplit("/", 1)[-1],
        "seconds": a.seconds,
        "pop_first": cnt0,
        "pop_last": rows[-1][1],
        "pop_delta": d,
        "pop_per_s": round(d / a.seconds, 2),
        "pop_max_before": max0,
        "pop_max_after": rows[-1][2],
        "n_interval_zero": sum(1 for v in incs if v == 0),
        "n_interval": len(incs),
        "max_in_one_interval": max(incs) if incs else 0,
    }
    print("POPWATCH " + json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
