#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""thdn.py —— 量 PL 直连通路的 THD+N，**只在板子上跑**。

    sudo env XILINX_XRT=/usr PYTHONIOENCODING=utf-8 \
        /usr/local/share/pynq-venv/bin/python3 thdn.py \
        --wav /home/xilinx/dma_exp/wav/tone1001.wav --loop 5

前提（不满足这轮就不算数）：
  1. 音源要尽量是**整数周期单音** —— 频率 = k * FS / N，N=1024、FS=48828.125，
     所以 bin 间隔 47.6837 Hz，k=21 → 1001.36 Hz。上位机会把 WAV 重采样到
     FS，采样点并不严格等间隔，所以这里加 hanning 窗并把基波左右各一个 bin
     一起挖掉，压住残余泄漏。
  2. 不能触发限幅器 —— peak_out 要明显低于 26000，否则量到的是限幅器的失真。

口径：THD+N = 除基波（和 DC）以外所有 bin 的能量 / 基波能量。
这里量的是**整条 PL 链**（EQ + 压缩 + 限幅 + 定点位宽）的总失真加噪声，
不是单个 FIR 的。

同时量「进核」和「出核」两条：进核那条的噪声地板里含上位机把 WAV 重采样到
48828.125 Hz 时引入的抖动，不算 PL 的账。真正的链贡献看 out - in 这个差。

结尾打印 THDN {json}。
"""
import argparse
import json
import sys
import time

import numpy as np
from pynq import MMIO

BASE = 0x43C40000
OFF_SCOPE_CTL = 0x64
OFF_SCOPE_WR = 0x6C
OFF_CAP_IDX = 0x78
OFF_CAP_DATA = 0x7C
OFF_PEAK_IN = 0x40
OFF_PEAK_OUT = 0x44

FS = 48828.125
N = 1024
DEPTH = 2048          # 字数；0x78 写的是字节地址，所以 idx*4
PLAY_PATH = "/dev/shm/fir_pl_play.txt"


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
    x = np.array(words, dtype=np.uint32)
    # 低 16 位 = 进核，高 16 位 = 出核，都是二进制补码
    def s16(v):
        v = v & 0xFFFF
        return np.where(v >= 0x8000, v.astype(np.int64) - 0x10000, v.astype(np.int64))
    return s16(x), s16(x >> 16)


def harm_table(x, k0, n_h=9):
    """基波相对 dBc：k0*2..k0*n_h 每个整数倍 bin 的电平（相对基波）。

    离散的 3/5/7 次峰 = 无记忆非线性；整片抬高没有峰 = 抖动/噪声。
    这是 T7 用来分开「压缩器包络纹波」和「定点链截断」的主要判据。
    """
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    X = np.fft.rfft(x * np.hanning(N))
    p = np.abs(X) ** 2
    fund = p[k0]
    out = {}
    for h in range(2, n_h + 1):
        k = k0 * h
        if k >= len(p):
            continue
        # 取峰所在 bin（±1 内最大），避开重采样把频率推偏半个 bin
        kk = k + int(np.argmax(p[max(1, k - 1):k + 2])) - 1
        out[h] = (round(kk * FS / N, 1),
                  round(10.0 * np.log10(p[kk] / fund + 1e-30), 1))
    return out


def thdn(x):
    """返回 (THD+N dB, 百分比, 基波 bin, 基波幅度)。"""
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    w = np.hanning(N)          # 音源已经在 bin 中心，窗只用来压住残余泄漏
    X = np.fft.rfft(x * w)
    p = np.abs(X) ** 2
    k0 = int(np.argmax(p[1:]) + 1)
    fund = p[k0]
    mask = np.ones_like(p, dtype=bool)
    mask[0] = False
    mask[max(1, k0 - 1):k0 + 2] = False     # 基波 + 左右各一个 bin
    noise = p[mask].sum()
    if fund <= 0:
        return None
    ratio = noise / fund
    return (10.0 * np.log10(ratio + 1e-30), 100.0 * np.sqrt(ratio), k0,
            float(np.sqrt(fund)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default="/home/xilinx/dma_exp/wav/tone1001.wav")
    ap.add_argument("--loop", type=int, default=5)
    ap.add_argument("--settle", type=float, default=3.0)
    ap.add_argument("--tag", default="thdn")
    a = ap.parse_args()

    with open(PLAY_PATH, "w") as f:
        f.write(a.wav + "\n")
    regs = MMIO(BASE, 0x1000)
    time.sleep(a.settle)

    runs = []
    best = None
    for _ in range(a.loop):
        ins, outs = grab(regs)
        di = thdn(ins)
        do = thdn(outs)
        rec = {
            "peak_in": int(np.max(np.abs(ins))),
            "peak_out": int(np.max(np.abs(outs))),
            "in_db": None if di is None else round(di[0], 1),
            "in_pct": None if di is None else round(di[1], 4),
            "in_bin": None if di is None else di[2],
            "in_fund_hz": None if di is None else round(di[2] * FS / N, 1),
            "out_db": None if do is None else round(do[0], 1),
            "out_pct": None if do is None else round(do[1], 4),
            "out_bin": None if do is None else do[2],
            "fund_hz": round(di[2] * FS / N, 1) if di else None,
        }
        if di is not None and do is not None:
            rec["harm_out"] = harm_table(outs, do[2])
            rec["harm_in"] = harm_table(ins, di[2])
            # 取「进核最干净」的那一帧做谐波代表：重采样污染帧会把整片抬高
            if best is None or di[0] < best[0]:
                best = (di[0], rec["harm_out"], rec["harm_in"], rec)
        runs.append(rec)
        time.sleep(0.2)

    outs_db = [r["out_db"] for r in runs if r["out_db"] is not None]
    ins_db = [r["in_db"] for r in runs if r["in_db"] is not None]
    out_pct = [r["out_pct"] for r in runs if r["out_pct"] is not None]
    summary = {
        "tag": a.tag,
        "wav": a.wav,
        "loop": len(runs),
        "peak_in_max": max(r["peak_in"] for r in runs),
        "peak_out_max": max(r["peak_out"] for r in runs),
        "in_db_median": round(float(np.median(ins_db)), 1) if ins_db else None,
        "out_db_median": round(float(np.median(outs_db)), 1) if outs_db else None,
        "out_pct_median": round(float(np.median(out_pct)), 4) if out_pct else None,
        "fund_hz": runs[0]["fund_hz"],
        "chain_db": (round(float(np.median(outs_db)) - float(np.median(ins_db)), 1)
                     if (ins_db and outs_db) else None),
        "clipped": any(r["peak_out"] >= 26000 for r in runs),
        "runs": runs,
    }
    print("THDN " + json.dumps(summary))
    if best is not None:
        print("HARM  fund=%s  (dBc, best-in frame: in=%s dB)"
              % (summary["fund_hz"], best[0]))
        for h in sorted(best[1]):
            hz, db = best[1][h]
            hz_i, db_i = best[2].get(h, (0, 0))
            print("  %dx  %8.1f Hz  out %6.1f dBc   in %6.1f dBc" % (h, hz, db, db_i))
    return 0


if __name__ == "__main__":
    sys.exit(main())
