"""软件基线 —— 和硬件**完全同一套算法**的纯 Python 实现。

它存在的唯一理由：做 CPU / FPGA 的加速比对照。
所以它和 `src/hls/fir_multiband.cpp` 必须是**同一个算法**，只差实现语言 ——
否则算出来的"加速比"里混着"两边算的根本不是一件事"，那个数就没意义了。

对齐的三个地方（以前对不上，2026-09-30 改的）：
    1. **采样率**：硬件按 48 kHz 设计系数（板载 codec 就是 48 kHz，RTL 里写死）。
       这个脚本原来直接吃 `real_voice.wav`（实测 44100 Hz），系数和硬件不是同一组。
       现在先 resample_poly 到 48 kHz。
    2. **频段结构**：硬件是**相减式**（3 个低通 + 两两相减），边界 500/1000/2000。
       原来这里是"每段各自 firwin"，边界 300/600/1000 —— 结构和边界都不一样。
    3. **压缩曲线**：阈值 0.1、压缩比 0.7，和核里 `drc()` 逐行对应。

带宽、边界、延迟这些都从 `band_design.py` 取 —— 那是全项目唯一一处定义，
不要在别的文件里再写一遍。

用法（在仓库根目录）：
    python src/python/multiband_baseline.py               # 默认 193 抽头
    python src/python/multiband_baseline.py --taps 193
输出：
    data/audio/multiband_output.wav   处理后的音频（48 kHz）
    data/figures/multiband_comparison.png  频谱对照图

屏幕上还会打出主机 CPU 处理耗时和实时倍率。它是本机软件基线，不是板上端到端性能。
"""

import argparse
import os
import time

import matplotlib
matplotlib.use("Agg")          # 无窗口环境也要能出图
import matplotlib.pyplot as plt
import numpy as np
import scipy.signal as signal

# 读写 wav 优先用 soundfile（仓库里其它脚本也都用它，保持一套）。
# 但本机 conda 环境里没装 soundfile，只有 scipy —— 于是回退到 scipy.io.wavfile，
# 免得"装了才跑得起来"。两条路的输出都是 16 位 PCM，听感上没差别。
try:
    import soundfile as _sf

    def read_wav(path):
        """返回 (float 数组 [n] 或 [n,2], 采样率)。"""
        data, fs = _sf.read(path)
        return data, fs

    def write_wav(path, x):
        _sf.write(path, x, FS)

except ImportError:
    from scipy.io import wavfile as _wavfile

    def read_wav(path):
        fs, data = _wavfile.read(path)
        return data.astype(np.float64) / 32768.0, fs

    def write_wav(path, x):
        _wavfile.write(path, FS,
                       np.round(np.clip(x, -1.0, 1.0) * 32767).astype(np.int16))

from band_design import (
    DRC_SLOPE, DRC_THRESHOLD, FS, bands_from_lowpasses, design_lowpasses,
    group_delay,
)

IN_PATH = "data/audio/real_voice.wav"
OUT_WAV = "data/audio/multiband_output.wav"
OUT_FIG = "data/figures/multiband_comparison.png"


def drc(band, thr, ratio):
    """与核里 fir_multiband.cpp 的 drc() 逐行对应。改一处必须改两处。"""
    a = np.abs(band)
    return np.where(a > thr,
                    np.sign(band) * (thr + (a - thr) * ratio),
                    band)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taps", type=int, default=193,
                    help="每段的抽头数，必须和硬件固件里的 FIR_N_TAPS 一致")
    ap.add_argument("--repeat", type=int, default=3,
                    help="计时重复几次，取最快的一次（首次调用有开销）")
    ap.add_argument("--cycles", type=int, default=None,
                    help="硬件每个采样要几拍，用来算板子侧耗时；"
                         "不给就按抽头数查下面的实测表")
    ap.add_argument("--fpga-mhz", type=float, default=100.0,
                    help="板子的时钟频率，同上")
    args = ap.parse_args()
    n = args.taps

    # 实测的拍/采样，键是抽头数。见 data/results/impl_metrics.md。
    # 换抽头数就得重新综合，也就得往这里补一行 —— 不给的话宁可不打这个对照，
    # 也不能拿 193 的数去说 65 抽头。
    CYCLES_MEASURED = {65: 173, 193: 449}
    cycles = args.cycles if args.cycles is not None else CYCLES_MEASURED.get(n)

    # ---- 1. 读音频，重采样到硬件用的采样率 ----
    data, fs_in = read_wav(IN_PATH)
    if data.ndim > 1:
        x = data[:, 0] + data[:, 1]      # 混合声道
    else:
        x = data.copy()
    x = x - np.mean(x)                   # 去直流
    print(f"1. 读入 {IN_PATH}：{len(x) / fs_in:.2f} 秒 @ {fs_in} Hz")

    if fs_in != FS:
        from math import gcd
        g = gcd(int(fs_in), FS)
        x = signal.resample_poly(x, FS // g, int(fs_in) // g)
        print(f"   重采样到 {FS} Hz（和硬件的系数同一组）→ {len(x)} 个采样")

    # ---- 2. 设计低通 + 相减得到各段（和核 §1.3 同一套） ----
    # 结构与边界都在 band_design.py 里，这里只是拿过来用 —— 那边是全项目唯一一处定义。
    lps = design_lowpasses(n)
    band_coefs = bands_from_lowpasses(lps, n)
    d = group_delay(n)

    # 自检：各段系数之和必须恰好是延迟 D 拍的冲激。这是相减式的定义性质（硬件里
    # 靠 hist[D] 白拿第 4 段，靠的就是这个恒等式），对不上说明结构搭错了 ——
    # 不是"误差大一点"。
    recon = np.zeros(n)
    recon[d] = 1.0
    err = np.max(np.abs(np.sum(band_coefs, axis=0) - recon))
    print(f"2. {len(band_coefs)} 段，每段 {n} 抽头，群延迟 D = {d} 采样"
          f"（{d / FS * 1000:.2f} ms）")
    print(f"   结构自检（各段系数之和 vs δ[D]）：最大偏差 {err:.3e}")

    # 计时只包住"处理"，不包文件读写 —— 要比的是算法本身
    def process(sig):
        bands = [signal.lfilter(c, 1.0, sig) for c in band_coefs]
        out = np.zeros_like(sig)
        for b in bands:
            out += drc(b, DRC_THRESHOLD, DRC_SLOPE)
        return bands, out

    elapsed = []
    for k in range(max(1, args.repeat)):
        t0 = time.perf_counter()
        bands, output = process(x)
        elapsed.append(time.perf_counter() - t0)
    best = min(elapsed)

    for i, b in enumerate(bands):
        print(f"   段 {i + 1}: 滤波后 max|y| = {np.max(np.abs(b)):.6f}")
    print(f"3. 压缩后求和：max|y| = {np.max(np.abs(output)):.6f}")

    # ---- 3b. Host CPU timing ----
    # This is a host-side measurement. The HLS figure is a core-level schedule,
    # so these values do not establish an end-to-end FPGA speedup.
    audio_s = len(x) / FS
    cpu_us = best / len(x) * 1e6
    budget_us = 1e6 / FS
    print(f"4. CPU 处理 {audio_s:.2f} 秒音频耗时 {best * 1000:.1f} ms"
          f"（重复 {max(1, args.repeat)} 次取最快）")
    print(f"   单采样 {cpu_us:.3f} us，实时倍率 {audio_s / best:.1f}x")
    if cycles:
        fpga_us = cycles / (args.fpga_mhz * 1e6) * 1e6
        print(f"   对照板子：{cycles} 拍 @ {args.fpga_mhz:g} MHz"
              f" = {fpga_us:.2f} us/采样，实时倍率 {budget_us / fpga_us:.1f}x")
        print(f"   （48 kHz 实时预算 {budget_us:.1f} us/采样；两项来自不同平台和测量范围，"
              "不能据此声称 FPGA 更快。自研核上板后的端到端时间仍待测。）")
    else:
        print(f"   （{n} 抽头的拍/采样还没量过，跳过板子侧对照；"
              "要用 --cycles 显式给）")

    os.makedirs(os.path.dirname(OUT_WAV), exist_ok=True)
    write_wav(OUT_WAV, output)
    print(f"   已保存 {OUT_WAV}")

    # ---- 5. 频谱对照图 ----
    m = min(FS, len(x))                  # 只取前 1 秒画图
    freqs = np.fft.fftfreq(m, 1 / FS)
    fft_orig = np.abs(np.fft.fft(x[:m])) / m * 2
    fft_multi = np.abs(np.fft.fft(output[:m])) / m * 2

    plt.figure(figsize=(10, 4))
    plt.plot(freqs[:m // 2], fft_orig[:m // 2], label="Original", color="blue", alpha=0.5)
    plt.plot(freqs[:m // 2], fft_multi[:m // 2], label="Multiband Processed",
             color="orange", linewidth=2)
    plt.title(f"Multiband Baseline ({len(bands)} bands, {n} taps, "
              f"subtractive, {FS} Hz)")
    plt.xlabel("Frequency (Hz)")
    plt.ylabel("Normalized Magnitude")
    plt.xlim(0, 2000)
    plt.ylim(0, 1.5)
    plt.legend()
    plt.grid(True)

    os.makedirs(os.path.dirname(OUT_FIG), exist_ok=True)
    plt.savefig(OUT_FIG)
    print(f"   已保存 {OUT_FIG}")


if __name__ == "__main__":
    main()
