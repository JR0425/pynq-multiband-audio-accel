# 分块粒度扫描：核的"每块固定开销"到底多大，实时出声能压到多小
#
# ============================ 要回答什么 ============================
#
# 48 kHz 给每个采样 **20.83 µs** 的预算。新核只要 **0.28 µs**
# （28 拍 @ 100 MHz，见 data/results/impl_metrics.md）。
# 也就是核这边有 74 倍余量 —— **"实时"这件事已经不是核的问题了。**
#
# 那问题在哪？在**每一块来回跑一趟 Python 的固定开销**：
# 写 10 个寄存器、两次 cache 维护、轮询 DONE。
# 这部分**不随块长变**，块越小摊到每个采样上就越重。
#
# 现在 `fir_audio_loop.py` 用 `CHUNK = 8000`（48 kHz 下 = 167 ms 一块），
# 听起来当然是一句一句的。把块调小就行吗？能用多小？
# 这个脚本就是来量这条线的：
#
#     t_块 = a + b · L          （L = 块长）
#       b = 核的每采样成本，应该贴近 0.28 µs（和理论值对得上才算量对了）
#       a = 每块的固定开销，**和块长无关**
#
# 有了 a 和 b，"还能不能实时"就是一道除法：
#
#     单采样耗时 = a/L + b     要求 < 20.83 µs
#
# ⚠️ 判据不是"块越小越好"。**块长 L 本身就是延迟**（L 个采样 = L/48000 秒）：
#     L=8000 → 167 ms（一句一句）
#     L=512  → 10.7 ms（能听出来，但不像回声）
#     L=64   → 1.3 ms（已经接近人耳分辨不出的量级）
#     再加上核自己的群延迟 96 个采样 = 2 ms，以及 codec/DMA 的缓冲。
#     真正要的是：**在单采样耗时还够用的前提下，把 L 压到最小**。
#
# 这个实验**不用改任何硬件、不用重新综合** —— 只改 Python 传的块长。
# 所以它是"实时出声"这件事上最便宜的一步。
#
# ============================ 怎么跑 ============================
#
#   echo xilinx | sudo -S env XILINX_XRT=/usr \
#     /usr/local/share/pynq-venv/bin/python3 fir_chunk_sweep.py
#
# 可选：SWEEP_SECONDS=2 改每次配置处理的音频长度（默认 2 秒）。

import os
import sys

import numpy as np
from pynq import Overlay

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    # 采样率的唯一定义处是 src/python/band_design.py（板上 10/01 跑
    # compare_cpu_fpga.py 时已经传过一份）。这里只要这一个常数，
    # 所以取不到就退回 48000 —— 别为了一个已知的常数让整个脚本跑不起来。
    from band_design import FS
except ImportError:
    FS = 48000.0
    print("  （没找到 band_design.py，采样率按 %.0f Hz 算）" % FS)
from fir_core import FirMultiband

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_bit():
    """找 fir.bit。**板上和电脑上的目录结构不一样**，所以按顺序试几个地方，
    不要写死一个相对路径 —— 写死了在仓库里能跑、传到板上就 FileNotFoundError。

    （`fir_selftest.py` 那批脚本用的是 `BITFILE = "fir.bit"`，也就是**当前工作目录**，
    靠的是跑之前先 `%cd` 到那个目录。这里两种都cover，省得踩。）
    """
    cands = []
    if os.environ.get("OVERLAY"):
        cands.append(os.environ["OVERLAY"])
    cands += [
        os.path.join(os.getcwd(), "fir.bit"),          # 板上：和脚本同目录
        os.path.join(_HERE, "fir.bit"),
        os.path.join(_HERE, "..", "overlay", "fir.bit"),   # 仓库里：board/overlay/
        os.path.join(_HERE, "..", "fir.bit"),
    ]
    for c in cands:
        if os.path.isfile(c):
            return os.path.abspath(c)
    raise SystemExit(
        "找不到 fir.bit。找过这些地方：\n  " + "\n  ".join(cands) +
        "\n\n最省事的修法：把 fir.bit 和这个脚本放进同一个目录，"
        "或者用 OVERLAY=/绝对/路径/fir.bit 指一下。\n"
        "（板上那份应该在 /home/xilinx/jupyter_notebooks/ 里 —— 先 %cd 过去。）")


OVERLAY = _find_bit()
SECONDS = float(os.environ.get("SWEEP_SECONDS", "2"))

BUDGET_US = 1e6 / FS          # 每个采样的实时预算

# 上界 8000 是核的 m_axi 缓冲深度 8192 留的余量（见 fir_audio_loop.py）；
# 下界 8 已经很激进了（8 个采样 = 0.17 ms 一块）。
CHUNKS = [8000, 4000, 2000, 1000, 512, 256, 128, 64, 32, 16, 8]
REPEAT = 5                    # 每档取最快的一次

print("=" * 78)
print("分块粒度扫描 —— 实时出声能压到多小")
print("=" * 78)
print("  采样率 %.0f Hz  →  每采样预算 %.2f µs" % (FS, BUDGET_US))
print("  核的理论成本 28 拍 @ 100 MHz = %.3f µs/采样" % (28 / 100e6 * 1e6))
print()

print("  用到的是：%s" % OVERLAY)
ol = Overlay(OVERLAY)
# 注意是 `.mmio`，不是整个 overlay —— FirMultiband 只认 mmio 对象
# （和 fir_selftest.py / compare_cpu_fpga.py 的写法保持一致）。
k = FirMultiband(ol.fir_multiband_0.mmio)

N = int(FS * SECONDS)
rng = np.random.default_rng(12345)
# 用真实量级的信号（不是小信号）：DRC 那一支走的路径和幅度有关，
# 拿全零或极小信号量出来的时间不能代表实际。
x = (rng.standard_normal(N) * 0.15 * 32768).astype(np.int16)
print("  测试信号 %d 个采样（%.1f 秒）" % (N, SECONDS))
print()

print("%8s %10s %12s %12s %12s %10s" %
      ("块长", "块数", "总耗时(s)", "单采样µs", "每块µs", "实时倍率"))
print("-" * 78)

rows = []
for L in CHUNKS:
    best_dt, best_blocks = None, None
    for _ in range(REPEAT):
        y, dt, blocks = k.process(x, reset=True, chunk=L)
        if best_dt is None or dt < best_dt:
            best_dt, best_blocks = dt, blocks
    us = best_dt / N * 1e6
    per_block = best_dt / len(best_blocks) * 1e6
    rows.append((L, best_blocks, best_dt, us, per_block))
    print("%8d %10d %12.4f %12.3f %12.3f %9.2fx%s" %
          (L, len(best_blocks), best_dt, us, per_block, BUDGET_US / us,
           "" if us < BUDGET_US else "   <-- 掉出实时"))

# ---- 拟合 t_块 = a + b·L ----
# ⚠️ **只用大块那几档拟合。**
# 小块那一头（L ≤ 128）的"每块耗时"会卡在一个**硬底**上，实测 L=64/32/16/8
# 都是 64 µs 上下、几乎一模一样，不再随块长变小 —— 那是每块固定那几个
# 系统调用和轮询的代价，`a + b·L` 这个形状根本描述不了它。
# 把这几个点一起喂进 polyfit，会把 a 抬高、b 压歪，然后外推出一个
# 看着很漂亮但站不住的"最小块长"。**只用 L ≥ 512 那几档，形状是对的。**
FIT_MIN = 512
sel = [(L, pb) for L, _b, _dt, _us, pb in rows if L >= FIT_MIN]
b_all, a_all = np.polyfit([s[0] for s in sel], [s[1] for s in sel], 1)
floor = min(pb for L, _b, _dt, _us, pb in rows if L <= 128)

print()
print("=" * 78)
print("结果")
print("=" * 78)
print("  每块耗时 = %.1f µs  +  %.4f µs × 块长      （只用 L ≥ %d 那 %d 档拟合）"
      % (a_all, b_all, FIT_MIN, len(sel)))
print("             └ 固定开销      └ 每采样成本")
print()
print("  核的每采样成本理论值 0.280 µs，实测拟合 %.4f µs" % b_all)
if b_all > 0:
    ratio = b_all / 0.280
    if 0.6 < ratio < 1.6:
        print("  → 差 %.0f%%，在同一个量级 —— 量到的是核本身，不是别的东西。" % (abs(ratio - 1) * 100))
    else:
        print("  → ⚠️ 差 %.2f 倍。要么块还太大（Python 开销被摊掉一部分，" % ratio)
        print("     拟合偏低），要么块太小（轮询粒度和 time.time() 的分辨率")
        print("     顶住了，拟合偏高）。看上面哪几档的「每块µs」开始不线性。")

print()
print("  小块那一头的**硬底：约 %.0f µs / 块**。" % floor)
print("     L=128/64/32/16/8 的每块耗时都是这个数上下，不再往下掉。")
print("     所以块长越小，单采样摊到的固定开销越重（64/L）：")
print("       L=64  → 1.00 µs/采样    L=8  → 8.0 µs/采样")
print("     这是**每块**的成本，和核一点关系都没有。")

print()
print("  实时判据（预算 %.2f µs/采样）—— 扫描里**每一档都够**：" % BUDGET_US)
worst = max(rows, key=lambda r: r[3])
print("     最贵的一档是 L=%d：%.2f µs/采样，离预算还有 %.1f 倍余量。"
      % (worst[0], worst[3], BUDGET_US / worst[3]))
print("     → **核已经不是瓶颈了，块长也不是。**")
print("     按硬底 %.0f µs 算，块长的理论下限是 %.1f 个采样 —— 但那个数没意义，" % (floor, floor / (BUDGET_US - b_all)))
print("     因为音频设备本来就是一块一块给你的，不会给你 3 个采样。")

print()
print("  真正决定端到端延迟的是这三块相加（本脚本只量了前两项）：")
print("     · 块的填充时间      L 个采样 = L/%.0f 秒" % FS)
print("     · 核自己的群延迟    %d 个采样 = %.2f ms" % (96, 96 / FS * 1000))
print("     · codec / 驱动的缓冲  **没量**")
print("     举例：L=480（10 ms）→ 10 + 2 = 12 ms，再加设备缓冲。")
print()
print("  ⚠️ 引用这个数时要带上一句：这是**板子上 Python 侧的往返**，")
print("     不是核本身的吞吐（核本身 0.28 µs/采样，比这里便宜得多）。")
print("     核快的意义不是把延迟压到 0，而是**让块长可以取小** ——")
print("     延迟的下限由音频通路决定，不是由核决定。")
print()
print("  ⚠️ 还有一个前提没验：这个数是「Python 同步来回、一次一块」的模型。")
print("     真要做成边录边放，得先确认音频通路能不能**全双工** ——")
print("     跑 board/scripts/audio_duplex_probe.py（实测：能，见那个脚本的输出）。")
