#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""capcheck.py —— 抓取缓冲的地址口径自检，**只在板子上跑**。

    sudo env XILINX_XRT=/usr PYTHONIOENCODING=utf-8 \
        /usr/local/share/pynq-venv/bin/python3 capcheck.py --loop 8

要回答一个问题：0x78 写的下标，是不是 1 个字 = 1 个采样？

做法：冻住写指针，从写指针 +1 开始**连续**读 1024 个下标（下标自己按 4096
回绕），然后看相邻样本有多少对长得一模一样。
    - 真·连续读：相邻样本几乎不会相等。空闲话筒有底噪，播放时更是每个都不同。
      重复率应该接近 0。
    - 还是字节地址：每隔 4 个下标落到同一个字，重复率会接近 3/4。

顺带报一下「相邻差的平均值」和 250 Hz 音源下的 DFT 峰，用来看时间轴对不对。
"""
import argparse
import math
import sys
import time

from pynq import MMIO

BASE = 0x43C40000
OFF_SCOPE_CTL = 0x64
OFF_SCOPE_WR = 0x6C
OFF_CAP_IDX = 0x78
OFF_CAP_DATA = 0x7C

FS = 48828.125
N = 1024
DEPTH = 2048          # 字数；地址口是字节地址，写 0x78 时要乘 4


def grab(regs):
    regs.write(OFF_SCOPE_CTL, 1)
    time.sleep(0.002)
    ptr = regs.read(OFF_SCOPE_WR) & (DEPTH - 1)
    start = (ptr + 1) & (DEPTH - 1)
    words = []
    for i in range(N):
        regs.write(OFF_CAP_IDX, ((start + i) & (DEPTH - 1)) * 4)
        words.append(regs.read(OFF_CAP_DATA))
    regs.write(OFF_SCOPE_CTL, 0)
    return ptr, words


def s16(x):
    x &= 0xFFFF
    return x - 0x10000 if x & 0x8000 else x


def analyze(words):
    ins = [s16(w) for w in words]
    outs = [s16(w >> 16) for w in words]
    dup_in = sum(1 for i in range(1, N) if ins[i] == ins[i - 1])
    dup_out = sum(1 for i in range(1, N) if outs[i] == outs[i - 1])
    return {
        "in_min": min(ins), "in_max": max(ins),
        "in_dup_adj": dup_in, "in_dup_frac": round(dup_in / float(N - 1), 3),
        "out_min": min(outs), "out_max": max(outs),
        "out_dup_adj": dup_out, "out_dup_frac": round(dup_out / float(N - 1), 3),
    }


def tone_peak(ins):
    """找 20 Hz .. 5 kHz 里幅度最大的频点（粗扫，够分辨 250 Hz）。"""
    mean = sum(ins) / float(len(ins))
    x = [v - mean for v in ins]
    best = (0.0, 0.0)
    f = 20.0
    while f <= 5000.0:
        w = 2.0 * math.pi * f / FS
        re = sum(x[n] * math.cos(w * n) for n in range(len(x)))
        im = sum(x[n] * math.sin(w * n) for n in range(len(x)))
        mag = math.hypot(re, im)
        if mag > best[1]:
            best = (f, mag)
        f += 5.0
    return best[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=6, help="采几轮")
    ap.add_argument("--gap", type=float, default=0.5)
    ap.add_argument("--tone", action="store_true", help="顺带报 DFT 峰（慢，约 1 s）")
    a = ap.parse_args()

    regs = MMIO(BASE, 0x1000)
    for k in range(a.loop):
        ptr, words = grab(regs)
        s = analyze(words)
        line = ("CAPCHK n=%d ptr=0x%03X in[%d..%d] dup=%d(%s) "
                "out[%d..%d] dup=%d(%s)"
                % (k, ptr, s["in_min"], s["in_max"], s["in_dup_adj"],
                   s["in_dup_frac"], s["out_min"], s["out_max"],
                   s["out_dup_adj"], s["out_dup_frac"]))
        if a.tone:
            ins = [s16(w) for w in words]
            line += " peak_hz=%.0f" % tone_peak(ins)
        print(line)
        sys.stdout.flush()
        time.sleep(a.gap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
