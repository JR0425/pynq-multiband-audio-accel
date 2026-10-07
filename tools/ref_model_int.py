#!/usr/bin/env python3
"""RTL 的逐位参考模型 —— 纯整数运算。

  用法：python tools/ref_model_int.py [--taps 193] [--in data/audio/test_input.txt]

  产出（都在 sim/rtl_sim/ 下）：
      in_q15.mem          输入，每行一个 16 位十六进制，给 testbench 的 $readmemh
      ref_acc_lp<edge>.mem 累加器原值，每行一个 40 位十六进制（逐位对拍用）
      ref_out_lp<edge>.txt 最终输出，每行一个十进制 int16
      ref_f32_lp<edge>.txt 同一遍但用浮点算的结果（看量化掉了多少 SNR）

============ 为什么参考模型可以做得这么"死" ============

定点 FIR 里**每一步都没有舍入**，所以参考模型能写成纯整数、和顺序无关：

  乘积   16 位 x 18 位 -> 34 位有符号。**精确**，一个位都不丢 ——
         而且和 HLS 的 ap_fixed<34,2> 完全一致（2 个整数位 + 32 个小数位）。
  累加   全部都是 32 位小数的整数相加，**加法本身不舍入**。
         溢出检查：|乘积| 最大 32768 x 10943 = 3.59e8，
         195 项求和上界 6.99e10 < 2^39 = 5.50e11，装得进 40 位有符号。
         -> **不会溢出**，所以加法是精确的，而且**顺序无关**。
  输出   acc >>> 17，再饱和到 16 位有符号。
         17 = 32（累加器小数位）- 15（输出小数位）
         算术右移 = 向下取整，对应 ap_fixed 的 AP_TRN；饱和对应 AP_SAT。

"顺序无关"这条是整件事的支点：HLS 把乘加拆成 5 路部分和（FIR_PARTIAL=5），
RTL 想怎么摆加法树都行 —— 只要累加器不溢出，两边结果就逐位相同。
**不需要**让 RTL 去模仿 HLS 的加法顺序。

输入量化（和 src/hls/fir_tb.cpp 的 to_q15 逐字一致）：
      q = round_half_away(x * 32768)，饱和到 [-32768, 32767]

⚠️ 这个模型只做 3 个低通，**不做**相减/DRC —— 和第一版 RTL 的范围对齐。
   相减和 DRC 是纯整数加/比较/乘，后面补上去不难，但一次只加一块。
"""

import argparse
import math
import os
import sys
from decimal import Decimal, ROUND_HALF_UP

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_rtl_coeffs import q17, nalloc, read_coeffs  # noqa: E402

FRAC = 17        # 系数小数位 / 输出右移量 = 32-15
ACCW = 40
CW = 18


def to_q15(x):
    """浮点 -> int16/Q1.15。四舍五入 + 饱和，和 fir_tb.cpp 的 to_q15 一致。"""
    v = x * 32768.0
    if v > 32767.0:
        v = 32767.0
    if v < -32768.0:
        v = -32768.0
    return int(Decimal(v).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def sat16(v):
    return 32767 if v > 32767 else (-32768 if v < -32768 else v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taps", type=int, default=193)
    ap.add_argument("--edges", default="500,1000,2000")
    ap.add_argument("--in", dest="inp", default="data/audio/test_input.txt")
    args = ap.parse_args()

    edges = [int(e) for e in args.edges.split(",")]
    n, na = args.taps, nalloc(args.taps)

    out_dir = os.path.join(REPO, "sim", "rtl_sim")
    os.makedirs(out_dir, exist_ok=True)

    in_path = os.path.join(REPO, args.inp)
    with open(in_path) as f:
        xraw = [float(s) for s in f if s.strip()]
    x = [to_q15(v) for v in xraw]
    L = len(x)
    print(f"输入 {args.inp}: {L} 个采样，量化后范围 "
          f"[{min(x)}, {max(x)}]")

    with open(os.path.join(out_dir, "in_q15.mem"), "w", newline="\n") as f:
        for v in x:
            f.write(f"{v & 0xFFFF:04x}\n")

    coeffs = {}
    for e in edges:
        c = read_coeffs(e, n)
        coeffs[e] = ([q17(v) for v in c] + [0] * (na - n), c + [0.0] * (na - n))

    worst_acc = 0
    for e in edges:
        cq, cf = coeffs[e]
        accs, outs, f32 = [], [], []
        for i in range(L):
            s = 0
            for j in range(na):
                m = i - j
                if m >= 0:
                    s += x[m] * cq[j]
            accs.append(s)
            worst_acc = max(worst_acc, abs(s))
            outs.append(sat16(s >> FRAC))
            f32.append(sum((x[i - j] if i - j >= 0 else 0) * cf[j]
                           for j in range(na)))

        with open(os.path.join(out_dir, f"ref_acc_lp{e}.mem"), "w",
                  newline="\n") as f:
            for v in accs:
                f.write(f"{v & ((1 << ACCW) - 1):010x}\n")
        with open(os.path.join(out_dir, f"ref_out_lp{e}.txt"), "w",
                  newline="\n") as f:
            f.write("\n".join(str(v) for v in outs) + "\n")
        with open(os.path.join(out_dir, f"ref_f32_lp{e}.txt"), "w",
                  newline="\n") as f:
            f.write("\n".join(f"{v:.9e}" for v in f32) + "\n")

        # 量化掉了多少：定点输出和纯浮点结果都在 Q1.15 整数尺度上
        # （f32 = Σ 采样(整数) x 浮点系数，所以直接就是同一把尺子），
        # 相减就是量化噪声，量级对得上才有意义。
        err = sum((o - v) ** 2 for o, v in zip(outs, f32))
        sig = sum(v * v for v in f32)
        snr = float("inf") if err == 0 else 10 * math.log10(sig / err)
        print(f"  LP{e:5d}: 输出范围 [{min(outs)}, {max(outs)}]，"
              f"定点 vs 浮点 SNR = {snr:.1f} dB")

    lo, hi = -(1 << (ACCW - 1)), (1 << (ACCW - 1)) - 1
    print(f"\n累加器峰值 |acc| = {worst_acc} ({worst_acc / 2**32:.4f})，"
          f"40 位有符号上限 = {hi}")
    if worst_acc > hi:
        print("  !! 溢出，参考模型失效", file=sys.stderr)
        return 1
    print(f"  余量 {hi / worst_acc:.2f} 倍 —— 加法精确且顺序无关这条成立")
    print(f"\n产出在 sim/rtl_sim/：in_q15.mem + ref_{{acc,out,f32}}_lp*.mem/txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
