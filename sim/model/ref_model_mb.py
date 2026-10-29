#!/usr/bin/env python3
"""多频段前端（3 低通 + 相减 + 4 段综合）的逐位整数参考模型。

  用法：python sim/model/ref_model_mb.py [--taps 193]

产出（都在 sim/rtl_sim/ 下）：
      ref_fe_ylp0/1/2.mem   三个低通累加器（40 位十六进制）
      ref_fe_band0..3.mem   四段（40 位十六进制）
      ref_fe_sum.mem        四段之和（40 位十六进制）
      ref_fe_out.txt        最终输出（十进制 int16）

============ 和 ref_model_int.py 的分工 ============

ref_model_int.py 只算 3 个低通，对应 src/rtl/fir_lp.v（单路验证）。
这个脚本算到 4 段 + 综合，对应 src/rtl/fir_mb_fe.v（前端整体）。
两个模型用的是**同一套规则**（同一个 q17、同一个 to_q15、同一个右移饱和），
所以 fir_lp 的结论在这里原样成立：

     乘积 16x18 精确，40 位累加不溢出 -> 加法顺序无关 -> 树怎么摆都对

============ 这个脚本自己会验的三件事 ============

1. Σ band 恒等于 x[D] << 17（D = 96）。相减式结构的定义性质，
   定点下没有舍入，所以必须是**精确相等**，不是"误差很小"。
2. 最终输出恒等于延迟 D 拍的输入（饱和之后）。
3. 如果 data/results/hw_transparent_i16.txt 在，逐位比一遍 ——
   那是 HLS 那版核在 bypass=1 时的 csim 输出。
   这一条才是"RTL 和 HLS 是同一个功能"的证据，前两条只证明自己自洽。
"""

import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_rtl_coeffs import q17, nalloc, read_coeffs  # noqa: E402
from ref_model_int import to_q15, sat16              # noqa: E402

FRAC = 17
ACCW = 40
MASK = (1 << ACCW) - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taps", type=int, default=193)
    ap.add_argument("--edges", default="500,1000,2000")
    ap.add_argument("--in", dest="inp", default="data/audio/test_input.txt")
    args = ap.parse_args()

    edges = [int(e) for e in args.edges.split(",")]
    n, na = args.taps, nalloc(args.taps)
    GD = (n - 1) // 2

    out_dir = os.path.join(REPO, "sim", "rtl_sim")
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(REPO, args.inp)) as f:
        xraw = [float(s) for s in f if s.strip()]
    x = [to_q15(v) for v in xraw]
    L = len(x)
    print(f"输入 {args.inp}: {L} 个采样，量化后范围 [{min(x)}, {max(x)}]")

    # 三张系数表，和 gen_rtl_coeffs.py 写出去的 .mem 同一套值
    cs = []
    for e in edges:
        q = [q17(v) for v in read_coeffs(e, n)]
        cs.append(q + [0] * (na - n))

    ylp = [[0] * L for _ in range(3)]
    for b in range(3):
        c = cs[b]
        for i in range(L):
            s = 0
            # 抽头延迟线：只有 i-j >= 0 的项存在，前面补零
            for j in range(na):
                if i - j >= 0:
                    s += x[i - j] * c[j]
            ylp[b][i] = s

    band = [[0] * L for _ in range(4)]
    summ = [0] * L
    outs = [0] * L
    for i in range(L):
        band[0][i] = ylp[0][i]
        band[1][i] = ylp[1][i] - ylp[0][i]
        band[2][i] = ylp[2][i] - ylp[1][i]
        # 第 4 段 = 延迟 D 拍的输入（Q8.32）减 y2
        xd = (x[i - GD] << FRAC) if i >= GD else 0
        band[3][i] = xd - ylp[2][i]
        summ[i] = sum(band[k][i] for k in range(4))
        outs[i] = sat16(summ[i] >> FRAC)

    def wr_mem(name, vals):
        with open(os.path.join(out_dir, name), "w", newline="\n") as f:
            for v in vals:
                f.write(f"{v & MASK:010x}\n")

    for b in range(3):
        wr_mem(f"ref_fe_ylp{b}.mem", ylp[b])
    for b in range(4):
        wr_mem(f"ref_fe_band{b}.mem", band[b])
    wr_mem("ref_fe_sum.mem", summ)
    with open(os.path.join(out_dir, "ref_fe_out.txt"), "w", newline="\n") as f:
        f.write("\n".join(str(v) for v in outs) + "\n")

    # ---- 自检 1：Σ band 精确等于 x[D] << 17 ----
    bad_sum = 0
    for i in range(L):
        xd = (x[i - GD] << FRAC) if i >= GD else 0
        if summ[i] != xd:
            bad_sum += 1
    print(f"自检 1  Σband == x[{GD}]<<17 : {L - bad_sum}/{L} 处相等")

    # ---- 自检 2：输出等于延迟 D 拍的输入 ----
    bad_out = 0
    for i in range(L):
        want = sat16(x[i - GD]) if i >= GD else 0
        if outs[i] != want:
            bad_out += 1
    print(f"自检 2  out == sat16(x[{GD}]) : {L - bad_out}/{L} 处相等")

    # ---- 自检 3：和 HLS csim 的 bypass 输出逐位比 ----
    tr_path = os.path.join(REPO, "data", "results", "hw_transparent_i16.txt")
    rc = 0
    if os.path.exists(tr_path):
        with open(tr_path) as f:
            tr = [int(s) for s in f if s.strip()]
        if len(tr) != L:
            print(f"自检 3  长度不一致：HLS {len(tr)} vs 参考 {L}")
            rc = 1
        else:
            bad_hls = sum(1 for a, b in zip(outs, tr) if a != b)
            print(f"自检 3  out == HLS bypass csim : {L - bad_hls}/{L} 处相等")
            if bad_hls:
                rc = 1
    else:
        print(f"自检 3  跳过：找不到 {tr_path}")

    if bad_sum or bad_out:
        rc = 1
    print(f"\n产出在 sim/rtl_sim/：ref_fe_{{ylp0..2,band0..3,sum}}.mem + ref_fe_out.txt")
    return rc


if __name__ == "__main__":
    sys.exit(main())
