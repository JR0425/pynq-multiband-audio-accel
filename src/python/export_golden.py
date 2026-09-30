"""生成硬件比对的"黄金参考"（golden reference）。

它是什么：拿与 HLS 核**完全相同的那组系数和同一个频段结构**，在 Python 里
用 float64 把同一批输入算一遍。硬件输出与它比对，才算"算法正确"。

为什么要独立算一遍（而不是直接用 multiband_baseline.py 的结果）：
    multiband_baseline.py 处理的是 real_voice.wav，而那个文件是 44100 Hz；
    给 HLS 的系数是按 48000 设计的（板载 codec 实际就是 48 kHz，RTL 里写死）。
    两套系数不是同一组滤波器，直接对撞没有意义。

为什么输入要先量化成 int16：
    核的入出口就是 int16/Q1.15（板上 I2S 送来的就是这串比特）。
    黄金参考必须吃**同一串比特**，否则量到的差异里混着"输入不一样"，
    和核自己的量化误差分不开 —— 那样这个数就白测了。

输出为什么保持浮点（不再量化回去）：
    这样量到的误差包含"核把结果压成 16 位"这一项，也就是板上真实会发生的全部误差。
    黄金参考是理想值，不是"另一台硬件"。

用法（在仓库根目录）：
    python src/python/export_golden.py            # 默认 193，和固件一致
    python src/python/export_golden.py --taps 65  # 对照版
输出：
    data/results/python_golden.txt  —— 每行一个浮点数，顺序与输入一致
"""

import argparse
import os

import numpy as np
import scipy.signal as signal

from band_design import (BAND_EDGES, DRC_SLOPE, DRC_THRESHOLD, group_delay)

IN_PATH = "data/audio/test_input.txt"
OUT_PATH = "data/results/python_golden.txt"
COEFF_FMT = "sim/hls_csim/lp_{}_n{}.txt"


def to_q15(x):
    """浮点 -> int16/Q1.15。必须和 src/hls/fir_tb.cpp 的 to_q15 完全一致。"""
    v = np.clip(np.round(np.asarray(x, dtype=np.float64) * 32768.0), -32768, 32767)
    return v.astype(np.int16)


def drc(band, thr, ratio):
    """与核里 fir_multiband.cpp 的 drc() 必须完全一致。"""
    a = np.abs(band)
    return np.where(a > thr,
                    np.sign(band) * (thr + (a - thr) * ratio),
                    band)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taps", type=int, default=193,
                    help="必须和硬件固件里的 FIR_N_TAPS 一致，否则两边比的是两回事；"
                         "默认 193 是已定的设计点")
    args = ap.parse_args()
    n = args.taps

    x_raw = np.loadtxt(IN_PATH, dtype=np.float64)
    x = to_q15(x_raw).astype(np.float64) / 32768.0
    print(f"输入：{IN_PATH}  共 {len(x)} 个采样（已量化成 int16/Q1.15）")

    lps = []
    for edge in BAND_EDGES:
        path = COEFF_FMT.format(edge, n)
        if not os.path.exists(path):
            raise SystemExit(f"找不到 {path}\n先跑：python src/python/export_coefficients.py --taps {n}")
        taps = np.loadtxt(path, dtype=np.float64)
        assert len(taps) == n, f"{path} 里是 {len(taps)} 个系数，期望 {n}"
        lps.append(taps)

    # 3 个低通
    ys = [signal.lfilter(taps, 1.0, x) for taps in lps]

    # 相减得到 4 段。第 4 段 = D 拍前的输入 − 第 3 个低通。
    d = group_delay(n)
    delayed = np.concatenate([np.zeros(d), x[:len(x) - d]])
    bands = [ys[0], ys[1] - ys[0], ys[2] - ys[1], delayed - ys[2]]

    # 自检：不做压缩时各段之和应当精确等于延迟 D 拍的输入。
    # 这是相减式结构的定义性质，不是近似 —— 对不上就说明结构搭错了。
    err = np.max(np.abs(np.sum(bands, axis=0) - delayed))
    print(f"  重建自检（各段之和 vs 延迟 {d} 拍的输入）：最大误差 {err:.3e}")

    total = np.zeros_like(x)
    for i, b in enumerate(bands):
        c = drc(b, DRC_THRESHOLD, DRC_SLOPE)
        total += c
        print(f"  段 {i + 1}: 滤波后 max|y| = {np.max(np.abs(b)):.6f}"
              f"  DRC 后 max|y| = {np.max(np.abs(c)):.6f}")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    np.savetxt(OUT_PATH, total, fmt="%.9e")
    print(f"\n黄金参考已保存：{OUT_PATH}")
    print(f"输出 max|y| = {np.max(np.abs(total)):.6f}")


if __name__ == "__main__":
    main()
