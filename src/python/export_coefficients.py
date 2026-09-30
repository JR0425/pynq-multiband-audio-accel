"""导出 FIR 系数（纯文本），供 HLS / RTL / Python 基线共用。

改频段划分不要改这个文件 —— 改 src/python/band_design.py，然后重跑本脚本。

用法（在仓库根目录）：
    python src/python/export_coefficients.py            # 默认 193，已定的设计点
    python src/python/export_coefficients.py --taps 65  # 对照版
输出：
    sim/hls_csim/lp_500_n193.txt 等 —— 每个边界一个文件，抽头数写在文件名里
    （两个抽头数的系数完全不同，同名会互相覆盖，所以必须带上 n。）

为什么只导 3 组低通、而不是 4 组频段：
    频段是**相减**出来的（见 band_design.py），硬件里也是当场相减。
    存 4 组等于把同一份信息存两遍，两边还可能对不上。
"""

import argparse
import os

import numpy as np

from band_design import BAND_EDGES, N_BANDS, N_LP, design_lowpasses, group_delay

OUT_DIR = "sim/hls_csim"


def path_for(edge, n):
    return os.path.join(OUT_DIR, f"lp_{edge}_n{n}.txt")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taps", type=int, default=193,
                    help="每个滤波器的抽头数（必须是奇数，线性相位要求）；"
                         "默认 193 是已定的设计点，65 是对照版")
    args = ap.parse_args()
    n = args.taps

    if n % 2 == 0:
        raise SystemExit(f"抽头数必须是奇数，收到 {n}")
    if n < 3:
        raise SystemExit(f"抽头数太小：{n}")

    os.makedirs(OUT_DIR, exist_ok=True)
    lps = design_lowpasses(n)

    print(f"频段划分：{N_BANDS} 段，边界 {BAND_EDGES} Hz，"
          f"{N_LP} 个低通 × {n} 抽头，fs = 48000 Hz")
    print(f"群延迟 D = {group_delay(n)} 采样 = {group_delay(n) / 48.0:.2f} ms")

    for edge, taps in zip(BAND_EDGES, lps):
        # 对称性是这个结构的前提（各段必须严格等延迟），出问题要当场拦下
        asym = float(np.max(np.abs(taps - taps[::-1])))
        if asym > 1e-12:
            raise SystemExit(f"低通 {edge} Hz 的系数不对称（{asym:.2e}）—— 各段延迟会不一致")
        out = path_for(edge, n)
        np.savetxt(out, taps, fmt="%.9e")
        print(f"  低通 {edge:>5d} Hz -> {out}   max|h| = {np.max(np.abs(taps)):.6f}")

    print("完成。")


if __name__ == "__main__":
    main()
