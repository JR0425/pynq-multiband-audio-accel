#!/usr/bin/env python3
"""WDRC 版的逐位参考模型 —— 纯整数，和 HLS 核一对一。

  用法：
      python tools/ref_model_wdrc.py --mode legacy   # 对 v20_baseline_output.txt
      python tools/ref_model_wdrc.py --mode wdrc     # 对 v21_wdrc_output.txt

为什么要这个模型：
    csim 只证明"核能跑"，不证明"算得对"。这里用同一套定点规则独立算一遍，
    逐点相同才算数。两条路都要过：
      legacy：attack = release = 0，应当和 v20_gain 的输出**逐字节相同**。
              这一条同时验证模型本身的 FIR / 相减 / 增益部分没写错。
      wdrc  ：attack = 136 / release = 7（5 ms / 100 ms @ 48 kHz），
              和 csim 的 v21_wdrc_output.txt 逐点相同。

定点规则（全部和 fir_types.h 对齐）：
    采样 data_t   Q1.15    原始整数就是 int16
    系数 coef_t   Q1.17    round_half_away(x * 2^17)
    累加 acc_t    Q8.32    乘积 16x18 精确，加法不溢出，所以整条 FIR 精确
    DRC  drc_t    Q2.22    每次赋值都 floor + 饱和（AP_TRN / AP_SAT）
    增益 gain_t   Q7.8     1.0 = 256

⚠️ ap_fixed 的 AP_TRN 是**向负无穷取整**，所以 Python 里一律用 `>>` / `//`，
   不能用 int() 或 round()。负数上差这一点就是 1 个 LSB，对拍会成片失败。
"""

import argparse
import os
import sys
from decimal import Decimal, ROUND_HALF_UP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_rtl_coeffs import q17, nalloc, read_coeffs  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

N_BANDS = 4
N_LP = 3
EDGES = (500, 1000, 2000)
D24_MIN, D24_MAX = -(1 << 23), (1 << 23) - 1


def to_q15(x):
    """浮点 -> int16/Q1.15。和 fir_tb.cpp 的 to_q15 逐字一致：
    乘 32768、加 ±0.5、向零截断、先饱和。"""
    v = x * 32768.0
    if v > 32767.0:
        v = 32767.0
    if v < -32768.0:
        v = -32768.0
    return int(v + (0.5 if v >= 0.0 else -0.5))


def sat24(v):
    return D24_MIN if v < D24_MIN else (D24_MAX if v > D24_MAX else v)


def sat16(v):
    return 32767 if v > 32767 else (-32768 if v < -32768 else v)


def drc_legacy(x, thr, ratio):
    """v20 的无记忆压缩，全部是 Q2.22 原始整数。

    ⚠️ HLS 是先把整个带符号的表达式算完、在**返回时**才截断成 drc_t：
           return sign * (thr + (a - thr) * ratio);
       所以负值必须对"取符号之后"的数做 floor，不能先 floor 正的再取反。
       两种写法只在小数部分非零时差 1 个 LSB —— 实测 1000 个点里差 3 个，
       全部是负值，就是这里。"""
    a = -x if x < 0 else x
    if a <= thr:
        return x
    num = (thr << 22) + (a - thr) * ratio     # Q4.44，非负
    r = (-num) >> 22 if x < 0 else num >> 22  # 对带符号的值 floor
    return sat24(r)


def wdrc(x, thr, ratio, atk, rel, env):
    """包络压缩，返回 (y, 新 env)，全部是 Q2.22 原始整数。"""
    a = -x if x < 0 else x

    diff = a - env
    step = atk if diff > 0 else rel
    # env = env + diff * step：乘出 Q4.44，加完一次性 floor + 饱和
    env = sat24(((env << 22) + diff * step) >> 22)
    if env < 0:
        env = 0

    gr = 0
    if env > thr:
        omr = (1 << 22) - ratio           # (drc_t)1 - ratio，精确
        gr = sat24(((env - thr) * omr) >> 22)

    a2 = a - gr
    if a2 < 0:
        a2 = 0
    return (-a2 if x < 0 else a2), env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("legacy", "wdrc"), required=True)
    ap.add_argument("--taps", type=int, default=193)
    ap.add_argument("--in", dest="inp", default="data/audio/test_input.txt")
    ap.add_argument("--ref", default=None,
                    help="要对比的 csim 输出（默认按 mode 选）")
    ap.add_argument("--thr", type=int, default=3277)
    ap.add_argument("--ratio", type=int, default=22938)
    ap.add_argument("--gain", type=int, default=256)
    ap.add_argument("--attack", type=int, default=136)
    ap.add_argument("--release", type=int, default=7)
    args = ap.parse_args()

    n, na = args.taps, nalloc(args.taps)
    ref_path = args.ref or (
        "data/results/v20_baseline_output.txt" if args.mode == "legacy"
        else "data/results/v21_wdrc_output.txt")

    with open(os.path.join(REPO, args.inp)) as f:
        xraw = [float(s) for s in f if s.strip()]
    x = [to_q15(v) for v in xraw]
    L = len(x)

    # 系数表：Q1.17，尾部补 0 到 nalloc（和 HLS 的表逐行对齐）
    coeffs = []
    for e in EDGES:
        c = [q17(v) for v in read_coeffs(e, n)]
        coeffs.append(c + [0] * (na - n))

    atk = args.attack << 7 if args.mode == "wdrc" else 0
    rel = args.release << 7 if args.mode == "wdrc" else 0
    thr = args.thr << 7
    ratio = args.ratio << 7
    env = [0] * N_BANDS

    out = []
    for i in range(L):
        # ---- 3 个低通，Q8.32 原始整数 ----
        ylp = [0, 0, 0]
        for b in range(N_LP):
            s = 0
            for k in range(na):
                m = i - k
                if m >= 0:
                    s += x[m] * coeffs[b][k]
            ylp[b] = s

        # ---- 相减式：4 段 ----
        band = [ylp[0], ylp[1] - ylp[0], ylp[2] - ylp[1],
                ((x[i - 96] << 17) if i >= 96 else 0) - ylp[2]]

        # ---- 每段：先乘增益（Q7.8），再压缩 ----
        acc = 0
        for b in range(N_BANDS):
            gb = sat24(band[b] >> 10)                  # acc_t -> drc_t
            gb = sat24((gb * args.gain) >> 8)          # * gain_t -> drc_t
            if args.mode == "legacy":
                yv = drc_legacy(gb, thr, ratio)
            else:
                yv, env[b] = wdrc(gb, thr, ratio, atk, rel, env[b])
            acc += yv << 10                            # drc_t -> acc_t

        out.append(sat16(acc >> 17))                   # acc_t -> data_t

    with open(os.path.join(REPO, ref_path)) as f:
        raw = [s.strip() for s in f if s.strip()]
    # legacy 的输出是浮点（tb 里 y/32768，科学计数），wdrc 的输出是整数。
    # 浮点那一路用 round(v*32768) 还原 —— 9 位有效数字足够还原 int16，不会歧义。
    try:
        ref = [int(s) for s in raw]
    except ValueError:
        ref = [int(round(float(s) * 32768.0)) for s in raw]

    if len(ref) != L:
        print(f"长度不一致：模型 {L}，参考 {len(ref)}")
        return 1

    bad = [(i, out[i], ref[i]) for i in range(L) if out[i] != ref[i]]
    print(f"mode={args.mode}  ref={ref_path}")
    print(f"逐点对比：{L - len(bad)} / {L} 相同，{len(bad)} 处不同")
    if bad:
        worst = max(abs(a - b) for _, a, b in bad)
        print(f"最大偏差 |model - csim| = {worst}")
        for i, a, b in bad[:10]:
            print(f"  i={i}: model={a} csim={b}")
        return 1

    print("PASS：与 csim 逐点相同")
    return 0


if __name__ == "__main__":
    sys.exit(main())
