#!/usr/bin/env python3
"""本代演示通路的加速比：ARM vs PL 计算链，同一次运行、同一串输入。

为什么不是 compare_cpu_fpga.py：
  `fir_pl.bit`（演示位流）里没有 PS<->PL 的批量接口 —— 音频从 I2S 进、从 I2S 出，
  PL 每 20.48 µs 消费一个采样，PS 插不进去。在演示位流上计时量到的是采样率，
  不是计算时间。所以换成 `fir_rtl_dma.bit`：它的计算链与演示位流是**同一组
  RTL 源文件**（fir_mb_fe + fir_drc4，见 board/overlay/build_audio_pl.tcl 与
  build_rtl_dma.tcl），只是外壳从 I2S 换成 AXI DMA。量的那一侧没变。

两边干的是同一件事，乘加次数对得上：
  3 遍 193 抽头低通 + 两两相减 + 四段增益/WDRC = 每采样 579 次乘加。
  CPU 侧 float64 + scipy 的 C 实现；PL 侧 Q1.15 定点、全并行。

用法（板上 root，先停 fir-pl-webui）：

    systemctl stop fir-pl-webui
    XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u accel_demo_chain.py

  --passes 10   PL 侧连发多少次 65535 点（默认 10）
  --secs 3.0    CPU 侧的输入长度
"""
import argparse
import os
import sys
import time

# 跑在 bit/hwh 所在目录里：rtl_dma_probe 的 BITFILE 是相对路径
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
os.chdir(_HERE)

import numpy as np  # noqa: E402
import scipy.signal as signal  # noqa: E402
from pynq import allocate  # noqa: E402

from band_design import (BAND_EDGES, DRC_SLOPE, DRC_THRESHOLD, FS,  # noqa: E402
                         bands_from_lowpasses, design_lowpasses, group_delay)
import rtl_dma_probe as R  # noqa: E402

N_TAPS = 193
MAC_PER_SAMPLE = 3 * N_TAPS        # 核每采样跑 3 遍低通


def build_input(n):
    """和 compare_cpu_fpga.py 完全同一份造法（同一个种子）。"""
    rng = np.random.default_rng(20261001)
    t = np.arange(n) / FS
    x = np.zeros(n)
    for f in (120, 300, 500, 700, 900, 1200, 1600, 2000, 2600, 3200, 4000):
        x += (0.5 / 11.0) * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi))
    x += 0.01 * rng.standard_normal(n)
    x = np.clip(x, -1.0, 1.0)
    return np.clip(np.round(x * 32767.0), -32768, 32767).astype(np.int16)


def drc(band, thr, ratio):
    a = np.abs(band)
    return np.where(a > thr, np.sign(band) * (thr + (a - thr) * ratio), band)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--passes", type=int, default=10)
    ap.add_argument("--secs", type=float, default=3.0)
    a = ap.parse_args()

    n_cpu = int(FS * a.secs)
    x16 = build_input(n_cpu)
    x_f64 = x16.astype(np.float64) / 32768.0
    print("输入 %d 采样（%.1f s @ %g Hz），11 根谱线 + 底噪，峰值 %.1f%% FS"
          % (n_cpu, a.secs, FS, 100.0 * np.max(np.abs(x_f64))))

    # ---------------- CPU ----------------
    lps = design_lowpasses(N_TAPS)
    D = group_delay(N_TAPS)

    def cpu_process(sig):
        n = len(sig)
        delayed = np.empty(n)
        delayed[:D] = 0.0
        delayed[D:] = sig[:n - D]
        y = [signal.lfilter(c, 1.0, sig) for c in lps]
        out = np.zeros_like(sig)
        for b in (y[0], y[1] - y[0], y[2] - y[1], delayed - y[2]):
            out += drc(b, DRC_THRESHOLD, DRC_SLOPE)
        return out

    cpu_process(x_f64[:1000])
    best = None
    for k in range(3):
        t0 = time.perf_counter()
        cpu_process(x_f64)
        el = time.perf_counter() - t0
        best = el if best is None else min(best, el)
        print("  CPU 第 %d 趟 %.3f s" % (k + 1, el))
    cpu_us = best / n_cpu * 1e6
    print("  ARM：%.3f µs/采样（%.1f ms 整段）" % (cpu_us, best * 1e3))

    # ---------------- PL ----------------
    print("\n加载 fir_rtl_dma.bit")
    dma, ctrl, stat = R.load()
    n_pl = R.MAXN
    xs = x16[:n_pl].astype(np.int32)
    inb = allocate(shape=(n_pl,), dtype=np.int32)
    outb = allocate(shape=(n_pl,), dtype=np.int32)
    # 冷启动那一块不算
    R.run_dma(dma, ctrl, stat, xs, drc=True, inbuf=inb, outbuf=outb, verify=False)

    wall = 0.0
    cyc = 0
    print("  连发 %d × %d 点，走 DRC 通路（ctrl[0]=1）" % (a.passes, n_pl))
    for i in range(a.passes):
        _, c, tw = R.run_dma(dma, ctrl, stat, xs, drc=True,
                             inbuf=inb, outbuf=outb, verify=False)
        wall += tw
        cyc += c
    total = a.passes * n_pl
    pl_wall_us = wall / total * 1e6
    pl_core_us = (cyc / R.FCLK_HZ) / total * 1e6
    core_cps = cyc / total
    print("  墙钟 %.4f s，总 %d 采样" % (wall, total))
    print("  PL 墙钟（含 DMA/驱动）：%.4f µs/采样 = %.2f Msamp/s"
          % (pl_wall_us, 1.0 / pl_wall_us))
    print("  PL 核内：%.4f µs/采样 = %.2f Msamp/s（%.3f 拍/采样 @100 MHz）"
          % (pl_core_us, 1.0 / pl_core_us, core_cps))
    print("  每块 DMA+驱动开销 %.2f ms（65535 点）"
          % ((wall - cyc / R.FCLK_HZ) / a.passes * 1e3))

    print("\n== 结果 ==")
    print("  乘加次数：两边都是 %d 次/采样（3 × %d 抽头）" % (MAC_PER_SAMPLE, N_TAPS))
    print("  ARM %.3f µs vs PL 墙钟 %.4f µs  => %.1f 倍"
          % (cpu_us, pl_wall_us, cpu_us / pl_wall_us))
    print("  ARM %.3f µs vs PL 核内 %.4f µs  => %.1f 倍（只算核，不含搬运）"
          % (cpu_us, pl_core_us, cpu_us / pl_core_us))
    print("  实时预算 %.3f µs/采样：ARM 占 %.1f%%，PL 核内占 %.1f%%"
          % (1e6 / FS, 100.0 * cpu_us * FS / 1e6, 100.0 * pl_core_us * FS / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
