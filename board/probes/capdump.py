#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""capdump.py —— 把抓取窗口的原始字打出来，分半验证「输入/输出」两半到底对不对。"""
import sys
import time

import numpy as np
from pynq import MMIO

BASE = 0x43C40000
N = 1024
DEPTH = 2048


def grab(regs):
    regs.write(0x64, 1)
    time.sleep(0.002)
    ptr = regs.read(0x6C) & (DEPTH - 1)
    start = (ptr + 1) & (DEPTH - 1)
    w = []
    for i in range(N):
        regs.write(0x78, ((start + i) & (DEPTH - 1)) * 4)
        w.append(regs.read(0x7C))
    regs.write(0x64, 0)
    return ptr, w


def s16(a):
    a = np.asarray(a, dtype=np.uint32) & 0xFFFF
    return np.where(a >= 0x8000, a.astype(np.int64) - 0x10000, a.astype(np.int64))


def stats(v, name):
    v = np.asarray(v, dtype=np.float64)
    v = v - v.mean()
    X = np.fft.rfft(v * np.hanning(N))
    p = np.abs(X) ** 2
    top = np.argsort(p[1:])[::-1][:8] + 1
    print("%s: min=%d max=%d rms=%.1f dc=%.0f"
          % (name, v.min(), v.max(), np.sqrt((v ** 2).mean()), v.mean()))
    ref = p[top[0]]
    print("   top bins: " + " ".join("%.0fHz(%.1fdB)"
                                     % (k * 48828.125 / N, 10 * np.log10(p[k] / ref + 1e-30))
                                     for k in top))
    d = np.abs(np.diff(v))
    print("   max|diff|=%.0f  median|diff|=%.1f" % (d.max(), np.median(d)))


def main():
    regs = MMIO(BASE, 0x1000)
    ptr, w = grab(regs)
    a = np.array(w, dtype=np.uint32)
    ins = s16(a)
    outs = s16(a >> 16)
    print("ptr=%d" % ptr)
    print("raw[0:8]  " + " ".join("%08X" % x for x in a[:8]))
    print("ins[0:16] " + " ".join("%6d" % x for x in ins[:16]))
    print("outs[0:16]" + " ".join("%6d" % x for x in outs[:16]))
    stats(ins, "IN ")
    stats(outs, "OUT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
