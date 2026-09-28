"""生成硬件比对的"黄金参考"（golden reference）。

它是什么：拿与 HLS 核**完全相同的那组系数**，在 Python 里把同一批输入算一遍。
硬件输出与它比对，才算"算法正确"。

为什么要单独写这一步，而不是直接用 multiband_baseline.py 的结果：
    multiband_baseline.py 处理的是 real_voice.wav，而那个文件是 44100 Hz，
    它的滤波器是按 44100 设计的；而给 HLS 用的系数文件
    （fir_coeffs_{1..4}.txt，由 export_coefficients.py 生成）是按 48000 设计的
    —— 板载 codec 实际就是 48 kHz（RTL 里写死）。
    两套系数不是同一组滤波器，直接对撞没有意义。
    所以这里统一用**给硬件的那组系数**来算参考值。

用法（在仓库根目录）：
    python src/python/export_golden.py
输出：
    data/results/python_golden.txt  —— 每行一个浮点数，顺序与输入一致
"""

import os

import numpy as np
import scipy.signal as signal

IN_PATH = "data/audio/test_input.txt"
OUT_PATH = "data/results/python_golden.txt"
COEFF_FMT = "sim/hls_csim/fir_coeffs_{}.txt"

N_TAPS = 65
N_BANDS = 4
DRC_THRESHOLD = 0.1
DRC_SLOPE = 0.7


def drc(x):
    """动态范围压缩：阈值以下原样过，超出部分按 0.7 压。
    与核里 fir_multiband.cpp 的 drc() 必须完全一致。"""
    a = np.abs(x)
    out = np.where(
        a > DRC_THRESHOLD,
        np.sign(x) * (DRC_THRESHOLD + (a - DRC_THRESHOLD) * DRC_SLOPE),
        x,
    )
    return out


def main():
    x = np.loadtxt(IN_PATH, dtype=np.float64)
    print(f"输入：{IN_PATH}  共 {len(x)} 个采样")

    total = np.zeros_like(x)
    for band in range(1, N_BANDS + 1):
        taps = np.loadtxt(COEFF_FMT.format(band), dtype=np.float64)
        assert len(taps) == N_TAPS, f"频段 {band} 的系数个数不是 {N_TAPS}"
        filtered = signal.lfilter(taps, 1.0, x)   # 因果 FIR，零初始状态
        total += drc(filtered)
        print(f"  频段 {band}: 滤波后 max|y| = {np.max(np.abs(filtered)):.6f}"
              f"  DRC 后 max|y| = {np.max(np.abs(drc(filtered))):.6f}")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    np.savetxt(OUT_PATH, total, fmt="%.9e")
    print(f"\n黄金参考已保存：{OUT_PATH}")
    print(f"输出 max|y| = {np.max(np.abs(total)):.6f}")


if __name__ == "__main__":
    main()
