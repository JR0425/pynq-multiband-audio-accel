"""硬件 FIR 输出 vs 软件参考 —— 用互相关验证通路对不对。

为什么用互相关而不是逐点比对：
    线性相位 FIR 有 (N-1)/2 个采样点的固有延迟。逐点直接比会得到一个很低的相关系数，
    看起来像算错了。正确做法是先扫一遍位移，找相关系数最大的那个，
    再看最佳位移是不是正好等于理论群延迟。

用法（在 PYNQ 板子上跑）：

    env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 verify_fir_hw_e2e.py

实测（2026-09-26，27 抽头参考 overlay，20 万点）：
    DMA 搬运约 3.1 ms；最佳位移 +13 = (27-1)/2；该位移下相关系数 1.000000。

换用自己的 overlay 时要改三处：BITFILE / DMA_PATH / COEFFS。
"""
import time

import numpy as np
from pynq import Overlay, allocate

BITFILE = "fir_accel2.bit"
DMA_PATH = "filter.fir_dma"      # overlay.ip_dict 里的 DMA 名字，先跑 test_overlay_load.py 看
COEFFS = [
    -255, -260, -312, -288, -144, 153, 616, 1233, 1963,
    2739, 3474, 4081, 4481, 4620, 4481, 4081, 3474, 2739,
    1963, 1233, 616, 153, -144, -288, -312, -260, -255,
]

FS = 100e6          # 采样率
DURATION = 0.002    # 时长（秒），200 kHz 信号取 2 ms 够用
MARGIN = 3000       # 头尾各掐掉多少点，避开边界效应
SCAN = 30           # 位移扫描范围 ±SCAN

n = int(DURATION * FS)
t = np.linspace(0, DURATION, n, endpoint=False)
samples = (
    10000 * np.sin(0.2e6 * 2 * np.pi * t)       # 主信号 200 kHz
    + 1500 * np.cos(46e6 * 2 * np.pi * t)       # 噪声 46 MHz
    + 2000 * np.sin(12e6 * 2 * np.pi * t)       # 噪声 12 MHz
).astype(np.int32)

# 软件参考
ref = np.convolve(samples.astype(np.float64), COEFFS, mode="same")

# 硬件
overlay = Overlay(BITFILE)
dma = overlay.filter.fir_dma
in_buf = allocate(shape=(n,), dtype=np.int32)
out_buf = allocate(shape=(n,), dtype=np.int32)
np.copyto(in_buf, samples)

t0 = time.time()
dma.sendchannel.transfer(in_buf)
dma.recvchannel.transfer(out_buf)
dma.sendchannel.wait()
dma.recvchannel.wait()
t1 = time.time()

hw = out_buf.copy().astype(np.float64)
in_buf.close()
out_buf.close()
print("DMA 耗时 %.4f 秒" % (t1 - t0))

# 硬件输出与参考之间可能差一个整体缩放系数，先估出来
core = slice(MARGIN, -MARGIN)
scale = float(np.dot(hw[core], ref[core]) / np.dot(ref[core], ref[core]))
print("缩放系数 1/%.1f" % (1.0 / scale))

# 扫位移，找相关系数最高的那个
best = (None, -2.0)
for s in range(-SCAN, SCAN + 1):
    c = float(np.corrcoef(hw[MARGIN + s : n - MARGIN + s], ref[MARGIN : n - MARGIN])[0, 1])
    if c > best[1]:
        best = (s, c)

s, c = best
a = hw[MARGIN + s : n - MARGIN + s]
b = ref[MARGIN : n - MARGIN]
resid = a - scale * b

print()
print("最佳位移 = %+d 个采样点, 相关系数 = %.6f" % (s, c))
print("理论群延迟 = (N-1)/2 = %.1f" % ((len(COEFFS) - 1) / 2.0))
print("残差/信号 = %.6f" % (np.sqrt(np.mean(resid**2)) / np.sqrt(np.mean((scale * b) ** 2))))

if c > 0.999:
    print("结论: 抠掉固定延迟后与软件参考一致, 通路正确")
else:
    print("结论: 仍有偏差, 需要继续查")
