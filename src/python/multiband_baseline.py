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
    python src/python/multiband_baseline.py --taps 65
输出：
    data/audio/multiband_output.wav   处理后的音频（48 kHz）
    data/figures/multiband_comparison.png  频谱对照图

屏幕上还会打出 CPU 处理耗时和实时倍率 —— 那才是"软件基线"这个词的用处。
记：那是**桌面 x86** 的数，不是板子上那个 ARM 核的数，报告里引用要说清是哪一个。
"""

import argparse
import os
import sys
import time

# Windows 控制台默认 GBK，打不出 µ（第 4 步那两行会 UnicodeEncodeError）。
# 只在 Windows 上会撞，但加这一句不影响 Linux —— 板子上跑同一个文件。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
    # 193 那个数取的是**上板的那一版** `v18_axi_shell`(=450)，不是它的前一版
    # `v13_sub_n193`(=449) —— 挂 AXI 壳时多了一拍。差 1 拍不影响结论，
    # 但两个文件报的字面数不一致会让人以为看错了。
    CYCLES_MEASURED = {65: 173, 193: 450}
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

    # ---- 逐段计时 ----
    # 只包住"处理"，不包文件读写 —— 要比的是算法本身。
    # ★ 形状必须和核一样：核每采样跑 **3 遍** 193 抽头低通
    #   （`for (int b = 0; b < N_LP; b++)`，N_LP=3），第 4 段靠 hist[D] 白拿。
    #   如果这边改成"4 段各自滤波"，乘加量成了 4/3 倍，加速比会虚高。
    #   下面 `process_legacy` 那条路留着，只为让那个差看得见。
    d = group_delay(n)
    delayed = np.zeros_like(x)
    delayed[d:] = x[:len(x) - d]

    def stage_lowpass(sig):
        return [signal.lfilter(c, 1.0, sig) for c in lps]

    def stage_drc(y):
        bands = (y[0], y[1] - y[0], y[2] - y[1], delayed - y[2])
        out = np.zeros_like(x)
        for b in bands:
            out += drc(b, DRC_THRESHOLD, DRC_SLOPE)
        return bands, out

    def process_legacy(sig):
        """旧形状：4 段各自一个滤波器。乘加 4/3 倍，别当基线用。"""
        bs = [signal.lfilter(c, 1.0, sig) for c in band_coefs]
        o = np.zeros_like(sig)
        for b in bs:
            o += drc(b, DRC_THRESHOLD, DRC_SLOPE)
        return bs, o

    rep = max(1, args.repeat)

    def bench(fn):
        el = []
        r = None
        for _ in range(rep):
            t0 = time.perf_counter()
            r = fn()
            el.append(time.perf_counter() - t0)
        return r, min(el)

    _, t_lp = bench(lambda: stage_lowpass(x))
    _, t_all = bench(lambda: stage_drc(stage_lowpass(x)))
    _, t_legacy = bench(lambda: process_legacy(x))
    bands, output = stage_drc(stage_lowpass(x))          # 真正要输出的那一份
    # 压缩+求和那一段是"全流程减去滤波"推出来的，不是单独量的 ——
    # 单独量会把两次调用之间的 cache 效应算进去。这是个估计值，当量级看。
    t_drc_only = t_all - t_lp

    for i, b in enumerate(bands):
        print(f"   段 {i + 1}: 滤波后 max|y| = {np.max(np.abs(b)):.6f}")
    print(f"3. 压缩后求和：max|y| = {np.max(np.abs(output)):.6f}")

    # 读入那一段单独量（含磁盘 I/O，和上面几个不是一回事，所以分开列）
    t0 = time.perf_counter()
    _d, fs_in2 = read_wav(IN_PATH)
    _x = _d[:, 0] + _d[:, 1] if _d.ndim > 1 else _d.copy()
    if fs_in2 != FS:
        from math import gcd as _gcd
        _g = _gcd(int(fs_in2), FS)
        _x = signal.resample_poly(_x, FS // _g, int(fs_in2) // _g)
    t_io = time.perf_counter() - t0

    best = t_all

    # ---- 3b. CPU 耗时 —— 这才是"软件基线"这个词的用处 ----
    # 记：这是**桌面 x86 上的数**，不是板子上那个 ARM 核的数。两个数是两个量级，
    # 报告里引用时必须说清是哪一个。加速比要等上板之后拿同一段音频、同一条时钟去比。
    audio_s = len(x) / FS
    cpu_us = best / len(x) * 1e6
    budget_us = 1e6 / FS
    print(f"4. CPU 处理 {audio_s:.2f} 秒音频耗时 {best * 1000:.1f} ms"
          f"（重复 {rep} 次取最快）")
    print(f"   单采样 {cpu_us:.3f} µs，实时倍率 {audio_s / best:.1f}x")
    print(f"   逐段（同一台机器、同一段音频，取最快那次）：")
    print(f"     a. 读入 + 混音 + 去直流 + 重采样到 48k（含磁盘） "
          f"{t_io * 1000:7.1f} ms")
    print(f"     b. 3 个 193 抽头低通（和核同一个循环）           "
          f"{t_lp * 1000:7.1f} ms   ← 全流程里的大头")
    print(f"     c. 4 段压缩 + 求和（= 全流程 − b，估计值）        "
          f"{t_drc_only * 1000:7.1f} ms")
    print(f"     ---- 处理合计（b + c，不含 a 的磁盘）            "
          f"{t_all * 1000:7.1f} ms")
    print(f"     （对照）4 段各自滤波的旧形状，乘加 4/3 倍          "
          f"{t_legacy * 1000:7.1f} ms  比上面慢 "
          f"{100.0 * (t_legacy / t_all - 1):.0f}%")
    if cycles:
        fpga_us = cycles / (args.fpga_mhz * 1e6) * 1e6
        print(f"   对照板子：{cycles} 拍 @ {args.fpga_mhz:g} MHz"
              f" = {fpga_us:.2f} µs/采样，实时倍率 {budget_us / fpga_us:.1f}x")
        print(f"   （48 kHz 实时预算 {budget_us:.1f} µs/采样；两个都够实时，差的是别的 ——"
              "桌面 3 GHz 扛 SIMD，比 100 MHz 的 DSP 链快得多 ——"
              "这种规模的负载上这很正常。板子的账不在吞吐上，在确定性延迟、功耗和体积。）")
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
