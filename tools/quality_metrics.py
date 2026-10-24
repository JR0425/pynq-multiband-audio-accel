#!/usr/bin/env python3
"""输出质量指标：THD+N 与四段频响 —— 全部走和核逐位相同的整数模型。

    python tools/quality_metrics.py            # 打表
    python tools/quality_metrics.py --csv out.csv

两个部分：

1. **THD+N**：单频正弦进 `ref_model_wdrc.process()`（就是 csim 逐点对过的那条
   整数路径），对输出做 FFT。用相干采样（1 kHz × 0.5 s = 24 个整周期）不补窗，
   所以不需要修正泄漏。
2. **四段频响**：直接由**同一张 Q1.17 系数表**算复数响应
   `H(f) = sum(c[k] * exp(-j*w*k)) / 2^17`。核里就是这张表，所以这比"按浮点设计
   系数画图"更贴近实现。再用时域模型在几个点上抽查，确认两边一致。

口径（引用这两个数时要带上）：测的是**核/模型输出，不是模拟端**。DAC、耳机、
ADC 的频响和失真都不在里面。
"""

import argparse
import cmath
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ref_model_wdrc import (EDGES, N_BANDS, N_LP, load_coeffs, process,
                            to_q15)  # noqa: E402

FS = 48000
Q17 = float(1 << 17)
# -20 dBFS 是核的默认阈值（寄存器 3277/32768）；用来做"DRC 打开"那一行。
DEF_THR, DEF_RATIO = 3277, 22938
OFF_THR = 32767                      # 阈值顶到量程 = 压缩器不动作
DEFAULT_ATK, DEFAULT_REL = 136, 7    # 5 ms / 100 ms @ 48 kHz

EDGES_HZ = (20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400,
            500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000,
            6300, 8000, 10000, 12500, 16000, 20000)


def sine_q15(freq, dbfs, n):
    a = 32768.0 * (10.0 ** (dbfs / 20.0))
    return [to_q15(a / 32768.0 * math.sin(2 * math.pi * freq * i / FS))
            for i in range(n)]


def thdn(freq, dbfs, mode="legacy", thr=DEF_THR, ratio=DEF_RATIO,
         gain=256, attack=DEFAULT_ATK, release=DEFAULT_REL, nfft=24000):
    """跑 2×nfft 个采样，丢掉前半段（滤波器和包络的建立过程），对后半段做 FFT。

    返回 (thdn_db, fundamental_dbfs, worst_harmonic_db)。
    """
    x = sine_q15(freq, dbfs, 2 * nfft)
    coeffs, na = load_coeffs(193)
    y = np.asarray(process(x, coeffs, na, mode=mode, thr=thr, ratio=ratio,
                           gain=gain, attack=attack, release=release),
                   dtype=np.float64)[nfft:]
    w = 2 * math.pi * freq / FS
    spec = np.fft.rfft(y)
    k = int(round(freq * nfft / FS))
    assert abs(freq * nfft / FS - k) < 1e-9, "这个频率不满足相干采样"
    # 单边谱 -> 幅度；基波从两格（k-1/k+1 不应有能量）之外全算成噪声/失真
    mag = np.abs(spec) * 2.0 / nfft
    fund = mag[k]
    tot = math.sqrt(max(np.sum(mag ** 2) - mag[0] ** 2 - fund ** 2, 0.0))
    thdn_db = 20 * math.log10(tot / fund) if fund > 0 else float("-inf")
    harm = [(j * freq, mag[j * k] / fund) for j in range(2, 11)
            if j * k < len(mag)]
    worst = max((20 * math.log10(h[1]) for h in harm if h[1] > 0),
                default=float("-inf"))
    return thdn_db, 20 * math.log10(fund / 32768.0), worst


def band_response(freq, coeffs, na):
    """四段在 freq 处的复数响应（已归一化成"满量程 -> 1.0"）。"""
    w = 2 * math.pi * freq / FS
    z = cmath.exp(-1j * w)
    hlp = []
    for b in range(N_LP):
        s = 0j
        zk = 1 + 0j
        for k in range(na):
            if coeffs[b][k]:
                s += coeffs[b][k] * zk
            zk *= z
        hlp.append(s / Q17)
    band = [hlp[0], hlp[1] - hlp[0], hlp[2] - hlp[1],
            cmath.exp(-1j * w * 96) - hlp[2]]
    return band


def db(x):
    return 20 * math.log10(abs(x)) if abs(x) > 0 else float("-inf")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None,
                    help="把频响表写成 CSV（频率 + 四段 + 总和，单位 dB）")
    args = ap.parse_args()

    coeffs, na = load_coeffs(193)

    print("## THD+N（整数模型输出，非模拟端）")
    print()
    print("| 条件 | 输入 | THD+N (dB) | 基波 (dBFS) | 最大谐波 (dBc) |")
    print("|---|---|---|---|---|")
    rows = [
        ("压缩器关闭", -6.0, "legacy", OFF_THR, DEF_RATIO, DEFAULT_ATK,
         DEFAULT_REL),
        ("压缩器关闭", -20.0, "legacy", OFF_THR, DEF_RATIO, DEFAULT_ATK,
         DEFAULT_REL),
        ("压缩器关闭", -40.0, "legacy", OFF_THR, DEF_RATIO, DEFAULT_ATK,
         DEFAULT_REL),
        ("默认无记忆压缩", -6.0, "legacy", DEF_THR, DEF_RATIO, DEFAULT_ATK,
         DEFAULT_REL),
        ("默认无记忆压缩", -30.0, "legacy", DEF_THR, DEF_RATIO, DEFAULT_ATK,
         DEFAULT_REL),
        ("默认 WDRC 5/100 ms", -6.0, "wdrc", DEF_THR, DEF_RATIO, DEFAULT_ATK,
         DEFAULT_REL),
        ("默认 WDRC 5/100 ms", -30.0, "wdrc", DEF_THR, DEF_RATIO, DEFAULT_ATK,
         DEFAULT_REL),
    ]
    for name, lvl, mode, thr, ratio, atk, rel in rows:
        t, f, h = thdn(1000.0, lvl, mode=mode, thr=thr, ratio=ratio,
                       attack=atk, release=rel)
        print("| %s | 1 kHz %+.0f dBFS | %.1f | %.1f | %.1f |"
              % (name, lvl, t, f, h))

    print()
    print("## 四段频响（由核用的 Q1.17 系数表算）")
    print()
    print("| Hz | 段0 <500 | 段1 500–1k | 段2 1k–2k | 段3 >2k | 合成 |")
    print("|---|---|---|---|---|---|")
    csv = ["freq,band0,band1,band2,band3,sum"]
    for f in EDGES_HZ:
        b = band_response(f, coeffs, na)
        vals = [db(v) for v in b]
        tot = db(sum(b))
        print("| %g | %.1f | %.1f | %.1f | %.1f | %.1f |"
              % (f, vals[0], vals[1], vals[2], vals[3], tot))
        csv.append("%g,%s" % (f, ",".join("%.3f" % v for v in vals + [tot])))

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            fh.write("\n".join(csv) + "\n")
        print()
        print("CSV -> %s" % args.csv)


if __name__ == "__main__":
    main()
