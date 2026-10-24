#!/usr/bin/env python3
"""系统功能框图 —— 报告 §2 用图。

画的是出货配置（v25_env_indep，36 拍/采样）的实际连接：
模拟端 → codec → PL 的音频 IP / FIR 核 → DDR 块缓冲 → PS 侧的 Python 搬样线程。

用法（仓库根目录）：
    python src/python/plot_block_diagram.py
输出 data/figures/system_block_diagram.png
"""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

OUT = os.path.join(os.path.dirname(__file__), "..", "..",
                   "data", "figures", "system_block_diagram.png")

plt.rcParams["font.family"] = "DejaVu Sans"

BLUE = "#dbe7f3"
GREEN = "#dcefd9"
ORANGE = "#fbe3cc"
GREY = "#eeeeee"
EDGE = "#444444"


def box(ax, x, y, w, h, text, fc=GREY, fs=9, weight="normal", style="round,pad=0.25"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=style,
                                linewidth=1.1, edgecolor=EDGE, facecolor=fc))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fs, weight=weight, linespacing=1.45)


def arrow(ax, p0, p1, label="", ls="-", color=EDGE, rad=0.0, fs=8, dx=0, dy=0):
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="<|-|>" if ls == "<>" else "-|>",
                                 mutation_scale=12, linewidth=1.1, color=color,
                                 linestyle="-", connectionstyle=f"arc3,rad={rad}"))
    if label:
        mx = (p0[0] + p1[0]) / 2 + dx
        my = (p0[1] + p1[1]) / 2 + dy
        ax.text(mx, my, label, ha="center", va="center", fontsize=fs,
                bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none", alpha=0.92))


def main():
    fig, ax = plt.subplots(figsize=(12.4, 6.6))
    ax.set_xlim(0, 124)
    ax.set_ylim(0, 66)
    ax.axis("off")

    # ---- PL / PS 分区 ----
    ax.add_patch(Rectangle((46, 36), 76, 27, fill=False, linestyle="--",
                           linewidth=1.2, edgecolor="#888888"))
    ax.text(47.5, 60.5, "PL  (FPGA fabric, 100 MHz)", fontsize=9, color="#555555")
    ax.add_patch(Rectangle((46, 3), 76, 25, fill=False, linestyle="--",
                           linewidth=1.2, edgecolor="#888888"))
    ax.text(47.5, 25.2, "PS  (ARM Cortex-A9, Linux, PYNQ 2.7 / Python 3.6)",
            fontsize=9, color="#555555")

    # ---- 数据通路 ----
    box(ax, 2, 46, 20, 10, "Microphone\n(TRRS, HP+Mic)", fc=ORANGE)
    box(ax, 2, 30, 20, 10, "Headphone / speaker\n(TRRS, HP+Mic)", fc=ORANGE)
    box(ax, 25, 33, 18, 24, "ADAU1761\ncodec\n\nADC / DAC\n48 kHz, int16", fc=ORANGE)
    box(ax, 50, 48, 26, 11,
        "audio_codec_ctrl\n(Pynq audio IP)\nrecord() / play(), MMIO, no DMA", fc=GREEN)
    box(ax, 84, 48, 34, 11,
        "fir_multiband core  v25\n4-band subtractive FIR + WDRC + band gain\n"
        "36 cycles/sample @ 100 MHz", fc=BLUE, weight="bold")
    box(ax, 88, 22, 30, 9, "DDR3\nblock buffers in[] / out[]", fc=GREY)

    # ---- 控制 / 搬运 ----
    box(ax, 48, 6, 22, 12,
        "fir_live.py\nblock mover thread\n(SCHED_FIFO)", fc=BLUE)
    box(ax, 76, 6, 20, 9,
        "web tuning UI\n/dev/shm/fir_tune.json", fc=BLUE, fs=8)

    # ---- 连线 ----
    arrow(ax, (22, 51), (25, 51), "mic")
    arrow(ax, (25, 35), (22, 35), "out")
    arrow(ax, (43, 45), (50, 51), "I2S", dy=-6.5)
    arrow(ax, (101, 48), (101, 31), "AXI4 master → S_AXI_HP0")
    arrow(ax, (88, 24.5), (70, 15), "cffi: copy in[] / out[]", dx=9, dy=-2)
    arrow(ax, (59, 18), (59, 48), "FIFO over MMIO\n(duplex_block)", dx=13)
    arrow(ax, (70, 18), (86, 48), "AXI-Lite", rad=-0.28, dx=9, dy=10)

    ax.text(62, 0.8,
            "Dashed boxes mark the PL / PS boundary. The audio IP has no DMA: the PS reads and "
            "writes its FIFO over MMIO, and the core is fed from DDR through its own AXI4 master port.",
            fontsize=7.5, color="#666666", ha="center")

    fig.tight_layout()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fig.savefig(OUT, dpi=200, bbox_inches="tight")
    print("wrote", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
