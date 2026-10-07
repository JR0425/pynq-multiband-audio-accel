#!/usr/bin/env python3
"""把浮点系数转成 RTL 用的定点整数表。

  用法：python tools/gen_rtl_coeffs.py [--taps 193] [--edges 500,1000,2000]

  输入：sim/hls_csim/lp_<edge>_n<taps>.txt   （每行一个 float）
  输出：src/rtl/coeffs_n<taps>.mem          （每行一个 18 位十六进制，给 $readmemh）

如果给 --check-rom，会顺手和 HLS 综合出来的系数 ROM 对一遍（见下）。

================ 量化规则不是猜的，是实测反推出来的 ================

HLS 综合完之后，它的系数 ROM 落在
    build/hls/<proj>/solution1/syn/verilog/fir_multiband_FIR_LP_V_rom.dat
把它和浮点系数对一遍，能反推出 HLS 到底怎么量化的。

踩过的坑：这个 .dat 里的数是**有符号 15 位二补数**，不是 16 位。
    第一行 0000      -> 0
    第二行 7FFE      -> 按 16 位无符号读是 32766，按 15 位有符号读才是 -2
    第 50 行 0020    -> +32（正数两种读法一样，所以只看正数会以为没问题）
按 "+32768 偏移" 或 "16 位无符号" 去读，579 个系数里有 305 个对不上；
按 15 位有符号读，**0 处不符**。原因是 HLS 对 const 数组做了常量值域裁剪 ——
这批系数实际只落到 [-2210, +10943]，15 位就装得下，于是 ROM 只有 15 位宽。

对上的规则就是本脚本用的规则：

    q = round_half_away_from_zero(x * 2^17)        # 有符号 Q1.17，18 位

四舍五入是**远离零**，不是 Python 内置 round() 的银行家舍入 ——
用 round() 会在 .5 的地方错，所以这里用 Decimal 的 ROUND_HALF_UP。
（Decimal 的 ROUND_HALF_UP 语义就是"远离零"，不是"向上"。）

为什么值得这么较真：RTL 和 HLS 用**同一张系数表**是"逐位相同"的前提。
差一个 LSB，最后对拍就会差好几百个采样点，而且看起来像"结构写错了"。
"""

import argparse
import os
import sys
from decimal import Decimal, ROUND_HALF_UP

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CW = 18          # 系数位宽，Q1.17
FRAC = 17        # 小数位


def q17(x):
    """浮点系数 -> 有符号 Q1.17 整数。四舍五入，远离零。"""
    v = (Decimal(x) * (Decimal(2) ** FRAC)).quantize(Decimal(1),
                                                     rounding=ROUND_HALF_UP)
    lo, hi = -(1 << (CW - 1)), (1 << (CW - 1)) - 1
    if v < lo or v > hi:
        raise OverflowError(f"系数 {x} 量化成 {v}，超出 Q1.17 的 [{lo}, {hi}]")
    return int(v)


def read_coeffs(edge, taps):
    path = os.path.join(REPO, "sim", "hls_csim", f"lp_{edge}_n{taps}.txt")
    with open(path) as f:
        return [float(line) for line in f if line.strip()]


def nalloc(taps, quantum=5):
    """补齐后的长度：HLS 那边要求能被 FIR_PARTIAL(=5) 整除（见 fir_multiband.cpp）。
    RTL 不强制，但保持同一个长度，两边的系数表就能逐行对上。"""
    return ((taps + quantum - 1) // quantum) * quantum


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taps", type=int, default=193)
    ap.add_argument("--edges", default="500,1000,2000")
    ap.add_argument("--check-rom", action="store_true",
                    help="和 HLS 综合出的系数 ROM 对一遍（需要那份 .dat 还在）")
    ap.add_argument("--rom", default=None, help="HLS 系数 ROM 的路径")
    args = ap.parse_args()

    edges = [int(e) for e in args.edges.split(",")]
    n = args.taps
    na = nalloc(n)

    out_dir = os.path.join(REPO, "src", "rtl")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"coeffs_n{n}.mem")

    all_q = []
    lines = []
    for e in edges:
        c = read_coeffs(e, n)
        if len(c) != n:
            print(f"ERROR: lp_{e}_n{n}.txt 有 {len(c)} 行，期望 {n}", file=sys.stderr)
            return 1
        q = [q17(x) for x in c]
        all_q.append(q)
        # 补齐位写 0：数值上等于没补，但能让三个滤波器长度一致
        padded = q + [0] * (na - n)
        for v in padded:
            lines.append(f"{(v & ((1 << CW) - 1)):05x}")
        print(f"  LP{e:5d}: {n} 个系数，范围 [{min(q):+d}, {max(q):+d}]"
              f"  ({min(q)/2**FRAC:+.6f} .. {max(q)/2**FRAC:+.6f})"
              f"  定点求和 = {sum(q)/2**FRAC:.9f}")

    with open(out_path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n写出 {out_path}")
    print(f"  {len(lines)} 行 = {len(edges)} 个滤波器 x {na} 个系数")
    print(f"  每行 5 位十六进制 = 18 位二补数，$readmemh 直接吃")

    # 再按滤波器各写一份：RTL 一个模块只想读自己那 195 个系数，
    # 让它去读 585 行的合并文件再偏移，是把"位置"这件事写进两处，容易错。
    for e, q in zip(edges, all_q):
        per = os.path.join(out_dir, f"coeffs_lp{e}_n{n}.mem")
        with open(per, "w", newline="\n") as f:
            f.write("\n".join(
                f"{(v & ((1 << CW) - 1)):05x}" for v in q + [0] * (na - n)) + "\n")
        print(f"  + {os.path.basename(per)}")

    if args.check_rom:
        rom_path = args.rom or os.path.join(
            REPO, "build", "hls", "impl_proj_pre_shiftchain", "solution1",
            "syn", "verilog", "fir_multiband_FIR_LP_V_rom.dat")
        if not os.path.exists(rom_path):
            print(f"\n跳过 --check-rom：找不到 {rom_path}", file=sys.stderr)
            return 0
        with open(rom_path) as f:
            rom = [int(s.strip(), 16) for s in f if s.strip()]
        # ROM 是 15 位有符号（见文件头），转回普通整数再比
        dec = lambda h: h if h < 0x4000 else h - 0x8000
        bad = 0
        for b in range(len(edges)):
            for k in range(n):
                if dec(rom[b * na + k]) != all_q[b][k]:
                    bad += 1
        total = len(edges) * n
        print(f"\n--check-rom: 和 {os.path.basename(rom_path)} 比 {total} 个系数，"
              f"不符 {bad} 个")
        if bad:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
