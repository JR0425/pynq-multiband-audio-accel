#!/usr/bin/env python3
"""PL 直连的数字通路延迟：用抓取窗口同时抓进核/出核两路，互相关求滞后。

抓取窗口（0x78 下标 / 0x7C 数据）每个字低 16 位 = 进核 fe_din、高 16 位 = 出核 mpo_l，
两路在同一拍写入，所以 out[i] 相对 in[i] 的滞后就是核的数字延迟（群延迟 + 流水线）。

为什么值得单独量：报告里 1.97 ms 的核群延迟一直是由 (N-1)/2 = 96 采样推出来的，
这条探针把它变成板上实测值，并顺带量出流水线多出来的那几拍。

用法（板上）：

    # 服务在跑、正在放音时：只读窗口，不碰 CTRL
    XILINX_XRT=/usr python3 pl_delay_capture.py --no-tone --rounds 24

    # 服务停掉时：用内部测试音（注意：测试音在进核抽头之后注入，
    # fe_din 读不到它，这条路量不出延迟，保留仅为对照）
    XILINX_XRT=/usr python3 pl_delay_capture.py --hz 1500 --amp -12

2026-11-02 实测：播放 voice_20261031_184837.wav、--no-tone 时，5 轮有效
窗口全部落在 94—96 采样，中位 96 采样 = 1.966 ms，与 (193-1)/2 一致。
"""
import argparse
import os
import sys
import time

os.environ.setdefault("XILINX_XRT", "/usr")

from pynq import MMIO  # noqa: E402
import numpy as np  # noqa: E402

REGS_BASE = 0x43C40000
CTRL_BASE = 0x43C00000

OFF_CTRL = 0x00
OFF_STAT0 = 0x08
OFF_TONE_SEL = 0x58
OFF_TONE_AMP = 0x5C
OFF_SCOPE_CTL = 0x64
OFF_SCOPE_WR = 0x6C
OFF_CAP_IDX = 0x78
OFF_CAP_DATA = 0x7C

CTRL_RUN = 1 << 0
CTRL_DRC = 1 << 1
CTRL_FLUSH = 1 << 2
CTRL_TONE = 1 << 3

FS = 48828.0
N = 1024


def read_window(regs):
    regs.write(OFF_SCOPE_CTL, 1)
    time.sleep(0.002)
    ptr = regs.read(OFF_SCOPE_WR)
    words = []
    for i in range(N):
        regs.write(OFF_CAP_IDX, (i * 4) & 0xFFF)
        words.append(regs.read(OFF_CAP_DATA))
    regs.write(OFF_SCOPE_CTL, 0)
    rot = (((ptr >> 2) & 0x3FF) + 1) & 0x3FF
    words = words[rot:] + words[:rot]
    ins = np.array([((x & 0xFFFF) ^ 0x8000) - 0x8000 for x in words], dtype=float)
    outs = np.array([(((x >> 16) & 0xFFFF) ^ 0x8000) - 0x8000 for x in words], dtype=float)
    return ins, outs


def best_lag(ins, outs, max_lag=400):
    """返回使相关最大的滞后（out 落后 in 的采样数）与归一化相关值。"""
    a = ins - ins.mean()
    b = outs - outs.mean()
    if not a.any() or not b.any():
        return None, 0.0
    scores = []
    for lag in range(0, max_lag + 1):
        # out[n] 对应 in[n - lag]
        if lag == 0:
            x, y = a, b
        else:
            x, y = a[:-lag], b[lag:]
        den = np.sqrt((x * x).sum() * (y * y).sum())
        scores.append((x * y).sum() / den if den else 0.0)
    scores = np.array(scores)
    k = int(np.argmax(scores))
    return k, float(scores[k])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hz", type=float, default=1500.0)
    ap.add_argument("--amp", type=float, default=-12.0)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--max-lag", type=int, default=400)
    ap.add_argument("--no-tone", action="store_true", help="不碰 CTRL/测试音，只读窗口（服务在跑时用）")
    a = ap.parse_args()

    regs = MMIO(REGS_BASE, 0x1000)
    ctrl = MMIO(CTRL_BASE, 0x1000)

    if not a.no_tone:
        ctrl.write(OFF_CTRL, CTRL_RUN | CTRL_DRC | CTRL_FLUSH)
        time.sleep(0.05)
        ctrl.write(OFF_CTRL, CTRL_RUN | CTRL_DRC)
        time.sleep(0.3)

    if not a.no_tone:
        band = min(range(4), key=lambda i: abs([250, 750, 1500, 3000][i] - a.hz))
        regs.write(OFF_TONE_SEL, band)
        regs.write(OFF_TONE_AMP, max(0, min(32767, int(round(32768.0 * 10.0 ** (a.amp / 20.0))))))
        ctrl.write(OFF_CTRL, CTRL_RUN | CTRL_DRC | CTRL_TONE)
        time.sleep(0.8)

    lags, cors = [], []
    for r in range(a.rounds):
        ins, outs = read_window(regs)
        if max(abs(v) for v in ins) < 100:
            print("  第 %d 轮：进核幅度太小（%d），该段静音，跳过"
                  % (r + 1, max(abs(v) for v in ins)))
            continue
        lag, cor = best_lag(ins, outs, a.max_lag)
        if lag is None:
            continue
        lags.append(lag)
        cors.append(cor)
        print("  第 %d 轮：滞后 %3d 采样（%.3f ms）  相关 %.4f  in峰 %d  out峰 %d"
              % (r + 1, lag, lag * 1000.0 / FS, cor,
                 max(abs(v) for v in ins), max(abs(v) for v in outs)))
        time.sleep(0.2)

    if not a.no_tone:
        ctrl.write(OFF_CTRL, CTRL_RUN | CTRL_DRC)
        regs.write(OFF_TONE_AMP, 0)

    if not lags:
        print("DELAY FAIL：没有拿到有效窗口")
        return 1
    lag = int(np.median(lags))
    print("")
    print("中位滞后 %d 采样 = %.3f ms（%d 轮：%s）"
          % (lag, lag * 1000.0 / FS, len(lags), lags))
    print("理论群延迟 (193-1)/2 = 96 采样 = 1.966 ms，差值 %+d 采样 = 流水线开销"
          % (lag - 96))
    return 0


if __name__ == "__main__":
    sys.exit(main())
