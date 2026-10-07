# 加速比：同一块板子上，CPU 跑 vs 核跑
#
# 为什么必须在这块板子上比：
#   以前手上有两个数 —— 桌面 x86 `0.158 µs/采样`、板子上核 `4.49 µs/采样`。
#   两者**不能相除**：一个是 3 GHz 的 x86 扛 SIMD，一个是 100 MHz 的 FPGA，
#   差两个数量级是必然的,说明不了"用 FPGA 值不值"。
#   真正有意义的是**同一颗芯片里的两种做法**:Zynq 里的 ARM 核（PS）vs PL 里的核。
#   那才是"软件跑不动,所以上硬件"这句话的账。
#
# 两边算的是同一套算法：
#   相减式 4 段（3 个 193 抽头低通 + 两两相减）、边界 500/1000/2000、
#   阈值 0.1、压缩比 0.7 —— 和 `src/hls/fir_multiband.cpp` 与 `band_design.py` 一致。
#
#   ★ "同一套算法"要落实到**乘加次数**上，不是口号。核每采样跑 3 遍 193 抽头
#     （`for (int b = 0; b < N_LP; b++)`，N_LP=3），所以 CPU 这边也必须是 3 遍低通
#     再相减。**曾经这里是 4 遍**（4 段各自滤波，772 个乘加/采样 vs 核的 579），
#     那等于让 CPU 多干 1/3 的活，加速比会虚高。现在两种都跑、都打出来，
#     引用时**只用 3 遍那个数**。
#
# 比之前先看清哪些**不一样**（写进报告时必须带上这几条,否则加速比会虚高）：
#
#   1. **精度不同。** CPU 这边是 float64 双精度；核是 Q1.15 定点 16 位。
#      核做的是**更粗**的活,所以快有一部分是这个换来的 —— 这是定点设计的取舍,
#      不是"FPGA 白得的便宜"。
#   2. **CPU 那边用的是 scipy 的 C 实现**,不是 Python 循环。
#      也就是说这是"优化过的软件",不是"随便写写的软件"。
#   3. **核的耗时里含 Python 写寄存器 + cache 维护 + 等 DONE 的轮询**。
#      这一条在 v18（450 拍/采样）时几乎看不见 —— 硬件本身 4.5 µs，
#      Python 那点固定开销被摊掉了。**核改成 28 拍/采样之后它就不再可忽略**，
#      会直接决定实测值。所以下面会把它算出来、单独打一行，不再一句"很小"带过。
#
# 用法（板子上,要 root）：
#   echo xilinx | sudo -S env XILINX_XRT=/usr \
#     /usr/local/share/pynq-venv/bin/python3 compare_cpu_fpga.py

import os
import sys
import time

import numpy as np
import scipy.signal as signal
from pynq import Overlay

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from band_design import (BAND_EDGES, DRC_SLOPE, DRC_THRESHOLD, FS,
                         bands_from_lowpasses, design_lowpasses, group_delay)
from fir_core import FirMultiband

BITFILE = "fir.bit"
N_TAPS = 193
SECONDS = 3.0
REPEAT = 3
FPGA_MHZ = 100.0
# 拍/采样,见 data/results/impl_metrics.md。
# v18_axi_shell 是 450；v19_shift_chain（改了延迟线写法）之后是 28。
# 默认按现在的 bit（v19）算，要复现旧数就 CYCLES_MEASURED=450。
CYCLES_MEASURED = int(os.environ.get("CYCLES_MEASURED", "28"))
# 每块多少采样。核的 m_axi 缓冲深度是 8192，8000 是留了余量。
# **这个值以前硬编码 8000，是错的** —— 上面那个 4.5 µs 里 Python 开销只占 0.2%，
# 是因为核本身慢（450 拍），Python 那点开销被摊掉了。核快 16 倍之后
# 每块固定开销会顶上来，块长就成了要量、要调的参数。
# 所以这里改成可传参，配合 fir_chunk_sweep.py 一起看。
CHUNK = int(os.environ.get("CHUNK", "8000"))

N = int(FS * SECONDS)
budget_us = 1e6 / FS           # 48 kHz 的实时预算,µs/采样


def drc(band, thr, ratio):
    """和核里 fir_multiband.cpp 的 drc() 逐行对应。改一处必须改两处。"""
    a = np.abs(band)
    return np.where(a > thr, np.sign(band) * (thr + (a - thr) * ratio), band)


# ---------------- 造一段两边共用的输入 ----------------
# 频谱铺得宽一点（100 Hz ~ 4 kHz 一堆分量 + 底噪）,别用单个正弦 ——
# 单音在频域只占一根线,压不到什么,两边都不吃力。
rng = np.random.default_rng(20261001)
t = np.arange(N) / FS
x = np.zeros(N)
for f in (120, 300, 500, 700, 900, 1200, 1600, 2000, 2600, 3200, 4000):
    x += (0.5 / 11.0) * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi))
x += 0.01 * rng.standard_normal(N)
x = np.clip(x, -1.0, 1.0)

x16 = np.clip(np.round(x * 32767.0), -32768, 32767).astype(np.int16)
x_f64 = x16.astype(np.float64) / 32768.0        # 两边吃完全同一串数
print("=" * 72)
print("同一块板子上：ARM 核跑 vs PL 里的核跑")
print("=" * 72)
print("输入：%d 个采样（%.1f 秒 @ %d Hz），11 根谱线 100 Hz~4 kHz + 底噪"
      % (N, SECONDS, FS))
print("算法：相减式 4 段 / %d 抽头 / 边界 %s / 阈值 %g / 压缩比 %g"
      % (N_TAPS, BAND_EDGES, DRC_THRESHOLD, DRC_SLOPE))

# ---------------- CPU ----------------
print("\n---- A. CPU（Zynq 里的 ARM，float64 + scipy 的 C 实现）----")
lps = design_lowpasses(N_TAPS)
band_coefs = bands_from_lowpasses(lps, N_TAPS)
D = group_delay(N_TAPS)


def cpu_process_sub(sig):
    """和核里那个循环**同一个形状**：3 个低通 + 两两相减。

    第 4 段是 hist[D]（D 拍前的输入），核里是直接取延迟线，这里就是 delayed。
    这才是能和核对撞的版本 —— 核每采样跑 3 遍 193 抽头，这边也必须是 3 遍。

    延迟线按 `sig` 的实际长度现造，别用外面一个预分配好的定长数组 ——
    预热那次只喂 1000 个采样，定长数组会直接 broadcast 报错。
    """
    n = len(sig)
    delayed = np.empty(n)
    delayed[:D] = 0.0
    delayed[D:] = sig[:n - D]
    y = [signal.lfilter(c, 1.0, sig) for c in lps]
    bands = (y[0], y[1] - y[0], y[2] - y[1], delayed - y[2])
    out = np.zeros_like(sig)
    for b in bands:
        out += drc(b, DRC_THRESHOLD, DRC_SLOPE)
    return out


def cpu_process_4filt(sig):
    """旧形状：4 段各自一个 193 抽头滤波器。

    乘加量是上一版的 4/3 倍（772 vs 579 个/采样），**和核不是一个算法**。
    留在这里只为让那个差可见 —— 别拿这个数当加速比的分子。
    """
    bands = [signal.lfilter(c, 1.0, sig) for c in band_coefs]
    out = np.zeros_like(sig)
    for b in bands:
        out += drc(b, DRC_THRESHOLD, DRC_SLOPE)
    return out


# 预热,别把首次调用的开销算进去
cpu_process_sub(x_f64[:1000])
cpu_process_4filt(x_f64[:1000])


def timeit(fn):
    el = []
    for _ in range(REPEAT):
        t0 = time.perf_counter()
        y = fn(x_f64)
        el.append(time.perf_counter() - t0)
    b = min(el)
    return y, b, b / N * 1e6


y_cpu, cpu_best, cpu_us = timeit(cpu_process_sub)
_y4, best4, us4 = timeit(cpu_process_4filt)

print("   跑 %d 次取最快：" % REPEAT)
print("     3 低通 + 相减（和核同一个算法，**这个才是要用的**）：%.3f 秒"
      "，单采样 %.3f µs" % (cpu_best, cpu_us))
print("     4 段各自滤波（旧形状，乘加 4/3 倍，仅作对照）：%.3f 秒"
      "，单采样 %.3f µs  → 比上面慢 %.0f%%" % (best4, us4, 100.0 * (us4 / cpu_us - 1)))
print("   实时倍率 %.2fx（预算 %.1f µs/采样）" % (budget_us / cpu_us, budget_us))

# ---------------- FPGA ----------------
print("\n---- B. PL 里的核（Q1.15 定点，AXI4-Master 直接读写 DDR）----")
ol = Overlay(BITFILE)
k = FirMultiband(ol.fir_multiband_0.mmio)
print("   核地址 %s" % hex(ol.ip_dict["fir_multiband_0"]["phys_addr"]))

k.process(x16[:min(CHUNK, N)], reset=True)     # 预热
fpga_best = None
for _ in range(REPEAT):
    y16, dt, blocks = k.process(x16, reset=True, chunk=CHUNK)
    fpga_best = dt if fpga_best is None else min(fpga_best, dt)
fpga_us = fpga_best / N * 1e6
nblk = len(blocks)

print("   跑 %d 次取最快：%.4f 秒（每块 %d 个采样，切了 %d 块）"
      % (REPEAT, fpga_best, CHUNK, nblk))
print("   单采样 %.3f µs   实时倍率 %.2fx" % (fpga_us, budget_us / fpga_us))

# ---- 把"硬件"和"Python 来回"拆开 ----
# 实测 = 硬件吞吐 + 每块固定开销/块长。这两项现在**差不多大**了，
# 不能像 v18 那版一样合成一个数就完事。
theo_us = CYCLES_MEASURED / (FPGA_MHZ * 1e6) * 1e6
ovh_us = fpga_us - theo_us
print("   硬件理论上限：%d 拍 @ %g MHz = %.3f µs/采样" % (CYCLES_MEASURED, FPGA_MHZ, theo_us))
print("   实测 − 理论  = %.3f µs/采样，折成每块 **%.1f µs** 的固定开销"
      % (ovh_us, ovh_us * CHUNK))
print("      （固定开销 = 写 10 个寄存器 + 2 次 cache flush + 1 次 invalidate + 轮询 DONE）")
if theo_us > fpga_us:
    print("   ⚠️ 实测比理论还快 —— 要么量到的是噪声，要么拍数不是 %d。"
          "先确认板上这版 bit 是不是 v19，再 CYCLES_MEASURED=<实际值> 重跑一次。"
          % CYCLES_MEASURED)
elif ovh_us < 0.1 * theo_us:
    print("   → 固定开销不到硬件的 10%，这个块长下可以忽略。")
else:
    print("   → **固定开销占实测的 %.0f%%**，它已经不是小数了。" % (100.0 * ovh_us / fpga_us))
    print("     这一段不是硬件，是 Python 的来回；核变快之后它就成了主项。")
    print("     压它只有两条路：块调大（摊薄固定开销），或者改成 PL 连续搬、")
    print("     Python 不参与每一块。逐档量法见 fir_chunk_sweep.py。")

# ---------------- 对撞 ----------------
print("\n" + "=" * 72)
print("加速比")
print("=" * 72)
print("   CPU   %.3f µs/采样   (%6.2fx 实时)" % (cpu_us, budget_us / cpu_us))
print("   核    %.3f µs/采样   (%6.2fx 实时)" % (fpga_us, budget_us / fpga_us))
print("   → 核比同一颗芯片上的 ARM 快 **%.1f 倍**" % (cpu_us / fpga_us))
print()
print("   ⚠️ 引用这个数必须带上这四条，否则会虚高：")
print("      1. 两边精度不同 —— CPU 是 float64，核是 Q1.15 定点 16 位。")
print("         核做的是更粗的活，快的一部分是这一条换来的（定点设计的取舍，")
print("         不是 FPGA 白得的便宜）。")
print("      2. CPU 那边是 scipy 的 C 实现，属于「优化过的软件」，不是随便写的。")
print("         也不是极限 —— 手写 NEON float32 的 FIR 还能再快 3~5 倍（估计值，没试过）。")
print("      3. 核的耗时含 Python 写寄存器 + cache 维护 + 轮询，上面已经把这一项")
print("         单独算出来了；块长越小它越重。")
print("      4. 两边工作量对等：都是每采样 579 次有效乘法（核是 3 × 195 = 585，")
print("         尾巴上 6 个是补零）。**这一条以前写错过**，见 data/results/accel_cpu_vs_fpga.md。")
print()
print("   两个都够实时（预算 %.1f µs/采样）。**板子上的账不在吞吐上** ——" % budget_us)
print("   ARM 那个数只是勉强实时，一旦要同时干别的（录音、写文件、跑界面）就顶不住；")
print("   核是**确定性的 %.3f µs**（硬件那一段不随负载变），这才是上硬件的理由。" % theo_us)

# ---------------- 结果一致性 ----------------
# 两边精度不同，不可能逐位一样；这里只查"是不是同一件事"：
# 压缩都让整体变轻、都没削顶。
r_in = float(np.sqrt(np.mean(x_f64 ** 2)))
r_cpu = float(np.sqrt(np.mean(y_cpu ** 2)))
print("\n---- C. 结果对不对（两边精度不同，不能逐位比）----")
print("   输入有效值 %.5f ｜ CPU 输出 %.5f（%.2f 倍）" % (r_in, r_cpu, r_cpu / r_in))
print("   CPU 峰值 %.5f ｜ 核峰值 %.5f" % (np.max(np.abs(y_cpu)),
                                            np.max(np.abs(y16.astype(np.float64) / 32768.0))))
print("   两边都该是「整体变轻、不削顶」。")
