#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
---
name: golden-compare
description: >
  Compare a hardware or HLS output stream against a golden reference the way a
  fixed-point datapath actually needs to be compared: best-shift alignment first,
  then bitwise match, then SNR, then a pass-through identity check. Use when the
  hardware "runs but the numbers are slightly off", when a correlation coefficient
  comes out low and you suspect a group delay rather than a bug, or when you need
  a pass/fail exit code for a regression script.
license: MIT
compatibility: pure Python 3, no dependencies beyond the standard library and optional numpy
metadata:
  version: "1.0.0"
  updated: "2026-11-01"
---

# 逐位比对，而不是"看起来差不多"

## 什么时候用

- 硬件/HLS 输出与参考模型对不上，但"波形看起来一样"。
- 相关系数算出 0.7 一类的中等值，怀疑是群延迟没对齐而不是算错。
- 要给回归脚本一个 0/1 退出码。

## 什么时候别用

- 两边精度不同（float64 vs Q1.15）。这时**不能逐位比** —— 只比 SNR 和峰值，
  且在结论里写明"精度不同"，否则会把定点取舍说成硬件白得的便宜。
- 想拿 SNR 去发现结构错误。SNR 只说"差多少 dB"，说不清"错在哪"。
  结构错误要用下面的**直通检验**。

## 两条判据，硬度不同，必须先跑硬的

    ① 直通检验（结构）：不做处理时，输出应当逐位等于延迟 D 拍的输入
    ② 数值比对（精度）：最佳位移下的逐位相同点数 / SNR / 最大绝对误差

顺序不能反。只跑 ② 的话，"乘法算错一点点"和"群延迟取错"长得一模一样。

## 为什么必须先扫最佳位移

线性相位 FIR 的群延迟是 `(N−1)/2`。逐点直接比会得到一个很低的相关系数、
看起来像算错了 —— 先把位移扫齐，再检查最佳位移是否**等于理论群延迟**。
实测例子：27 抽头 FIR 最佳位移 +13、相关系数 1.000000，正好是 `(27−1)/2`。

**最佳位移对不上理论值，才是真错。**

## 用法

    # 两个纯文本，一列一个整数
    python golden_compare.py hw.txt golden.txt

    # 指定理论群延迟，并跑直通检验
    python golden_compare.py hw.txt golden.txt --delay 96 --passthrough

    # npy
    python golden_compare.py hw.npy golden.npy --npy

退出码：0 = 通过，1 = 不通过。可直接串进 `run_all_checks.sh`。

## 判据阈值怎么定

`--snr-min` 默认 70 dB。这个数不是拍的：采样 16 位本身的理论上限约 96 dB，
只要求"听起来干净"的话 70 dB 已远高于要求。**把阈值写进脚本、不要写进人脑**，
否则下一次换音源就会用"这次信号不好"糊过去。
"""

import argparse
import os
import sys

EPS = 1e-12


def load(path, use_npy):
    """读成一列浮点数。

    刻意不强制转 int：定点输出是整数、浮点参考是 1e-5 量级的小数，两种都要能读。
    "逐位相同"在两种情况下都定义为**数值精确相等**（==），不做容差。
    """
    if use_npy or path.endswith(".npy"):
        try:
            import numpy as np
        except ImportError:
            raise SystemExit("读 .npy 需要 numpy")
        return [float(v) for v in np.load(path).ravel()]
    vals = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for tok in line.split():
                try:
                    vals.append(float(tok))     # 容忍 "1.0e+03" 这类写法
                except ValueError:
                    pass
    return vals


def best_shift(a, b, max_shift):
    """在 ±max_shift 内找一个位移，使逐位相同的点数最多。

    返回 (shift, matched, total)。shift > 0 表示 b 相对 a 滞后 shift 个采样。
    """
    best = (0, -1, 0)
    for s in range(-max_shift, max_shift + 1):
        if s >= 0:
            xa, xb = a[s:], b[: len(b) - s] if s else b
        else:
            xa, xb = a[: len(a) + s], b[-s:]
        n = min(len(xa), len(xb))
        if n <= 0:
            continue
        matched = sum(1 for i in range(n) if xa[i] == xb[i])
        if matched > best[1]:
            best = (s, matched, n)
    return best


def snr_db(ref, got):
    """ref 为参考，got 为被测。"""
    n = min(len(ref), len(got))
    if n == 0:
        return float("-inf")
    sig = sum(ref[i] * ref[i] for i in range(n))
    err = sum((ref[i] - got[i]) ** 2 for i in range(n))
    if err <= EPS:
        return float("inf")
    if sig <= EPS:
        return float("-inf")
    import math

    return 10.0 * math.log10(sig / err)


def main():
    ap = argparse.ArgumentParser(description="硬件/HLS 输出 vs 黄金参考")
    ap.add_argument("got", help="被测输出（硬件或 HLS csim）")
    ap.add_argument("ref", help="黄金参考")
    ap.add_argument("--npy", action="store_true", help="两边都是 .npy")
    ap.add_argument("--delay", type=int, default=None, help="理论群延迟 D（采样）")
    ap.add_argument("--max-shift", type=int, default=256, help="位移搜索半径")
    ap.add_argument("--passthrough", action="store_true",
                    help="跑直通检验：got 是否 == 延迟 D 拍的输入")
    ap.add_argument("--input", help="直通检验用的输入文件（默认同 ref）")
    ap.add_argument("--snr-min", type=float, default=70.0, help="SNR 下限 dB")
    args = ap.parse_args()

    for p in (args.got, args.ref):
        if not os.path.isfile(p):
            print("找不到文件: %s" % p)
            return 2

    got = load(args.got, args.npy)
    ref = load(args.ref, args.npy)
    print("got %d 点 / ref %d 点" % (len(got), len(ref)))

    ok = True

    # ① 直通检验（结构）。最硬的一条，先跑。
    if args.passthrough:
        if args.delay is None:
            print("直通检验需要 --delay")
            return 2
        src = load(args.input, args.npy) if args.input else ref
        d = args.delay
        if len(got) <= d:
            print("输出太短，容不下 delay=%d" % d)
            return 2
        n = min(len(got) - d, len(src))
        mism = sum(1 for i in range(n) if got[i + d] != src[i])
        print("① 直通检验  输出 == 输入延后 %d 拍：错配 %d / %d" % (d, mism, n))
        if mism:
            ok = False

    # ② 数值比对。先对齐再比。
    shift, matched, total = best_shift(got, ref, args.max_shift)
    if shift >= 0:
        xa, xb = got[shift:], ref[: len(ref) - shift] if shift else ref
    else:
        xa, xb = got[: len(got) + shift], ref[-shift:]
    n = min(len(xa), len(xb))
    xa, xb = xa[:n], xb[:n]

    print("② 最佳位移 %+d（滞后为正），对齐后 %d 点" % (shift, n))
    if args.delay is not None:
        exp = args.delay
        verdict = "对得上" if shift == exp else "⚠️ 对不上（理论 %d）" % exp
        print("   理论群延迟 %d → %s" % (exp, verdict))
        if shift != exp:
            ok = False

    print("   逐位相同 %d / %d" % (matched, total))
    s = snr_db(xb, xa)
    mae = max((abs(xa[i] - xb[i]) for i in range(n)), default=0.0)
    print("   SNR %.2f dB，最大绝对误差 %.6g" % (s, mae))

    if s < args.snr_min:
        print("   ⚠️ SNR 低于 %.1f dB" % args.snr_min)
        ok = False
    if matched != total and args.delay is not None:
        print("   ⚠️ 存在非逐位一致的采样（定点对定点应当逐位相同）")
        ok = False
    if args.delay is None and total and matched * 2 < total:
        print("   ⚠️ 逐位命中不到一半。常见原因：两边精度不同（float vs Q1.15）、"
              "对错了参考文件、或位移超出 --max-shift。这不算失败，但也别当通过报。")

    print("\n结果：%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
