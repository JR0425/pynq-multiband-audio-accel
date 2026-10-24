"""生成最终报告用的图，输出到 data/figures/report/。

数据来源：
  2-3  data/results/four_band_response_20261022.csv
  2-4  data/results/impl_metrics.md（拍数与资源）
  3-1  data/results/impl_metrics.md（整机实现）
  3-2  data/results/latency_n*_20261023.json（数字段）；声学段 R 见 latency_retest_20261023.md
  3-3  data/results/soak600_20261023.md、live_dropout_recheck_20261024.md
  3-4  3-5  data/results/wdrc_curve_20261023.json
"""

import csv
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "data", "figures", "report")
os.makedirs(OUT, exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 160

INK = "#1f2933"
ACCENT = "#c9622b"
BLUE = "#2f6f9f"
GREEN = "#3f7d4f"
GREY = "#8a939b"


def save(fig, name):
    path = os.path.join(OUT, name)
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote", os.path.relpath(path, ROOT))


def box(ax, x, y, w, h, text, fc="#eef2f5", ec=INK, fs=10):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.02",
                                fc=fc, ec=ec, lw=1.1))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, color=INK)


def arrow(ax, x1, y1, x2, y2, style="-|>", color=INK, ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                                 mutation_scale=12, lw=1.1, color=color, linestyle=ls))


def fig_flow():
    fig, ax = plt.subplots(figsize=(9.2, 5.4))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6)
    ax.axis("off")

    rows = [
        (4.7, ["Python 浮点模型", "整数参考 / 定点核", "C 仿真", "综合 / 实现"]),
        (2.7, ["板端集成 Overlay", "频响 / WDRC / 延迟", "鲁棒性 A–E", "600 秒长跑"]),
    ]
    xs = [0.4, 2.9, 5.4, 7.9]
    for y, labels in rows:
        for x, lab in zip(xs, labels):
            box(ax, x, y, 1.9, 0.95, lab, fs=9)
    for i in range(3):
        arrow(ax, xs[i] + 1.9, 4.7 + 0.48, xs[i + 1], 4.7 + 0.48)
        arrow(ax, xs[i + 1], 2.7 + 0.48, xs[i] + 1.9, 2.7 + 0.48)

    arrow(ax, 8.85, 4.72, 8.85, 3.68)

    box(ax, 2.9, 0.7, 5.0, 0.95,
        "每次优化：数值 · 拍数 · 时序 · 板端 四类回归", fc="#fdf1e8", ec=ACCENT, fs=10)
    for x in (4.1, 6.2):
        arrow(ax, x, 2.7, x, 1.7, ls="--", color=ACCENT)

    ax.text(0.4, 5.9, "设计验证流程：上一级通过才进入下一级", fontsize=12, color=INK)
    save(fig, "fig1_1_flow.png")


def fig_block():
    """系统整体框图（中文版，对应 fig2_1）。英文版见 plot_block_diagram.py。"""
    ORANGE = "#fbe3cc"
    GREEN = "#dcefd9"
    LBLUE = "#dbe7f3"

    def bx(x, y, w, h, text, fc=GREY, fs=10, weight="normal"):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.25",
                                    lw=1.1, edgecolor=INK, fc=fc))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
                fontsize=fs, color=INK, weight=weight, linespacing=1.45)

    def ar(p0, p1, label="", rad=0.0, fs=9, dx=0, dy=0):
        ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=12,
                                     lw=1.1, color=INK,
                                     connectionstyle="arc3,rad=%s" % rad))
        if label:
            ax.text((p0[0] + p1[0]) / 2 + dx, (p0[1] + p1[1]) / 2 + dy, label,
                    ha="center", va="center", fontsize=fs, color=INK,
                    bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none"))

    fig, ax = plt.subplots(figsize=(9.8, 5.4))
    ax.set_xlim(0, 124)
    ax.set_ylim(0, 66)
    ax.axis("off")

    ax.add_patch(Rectangle((46, 36), 76, 27, fill=False, ls="--", lw=1.2,
                           edgecolor="#888888"))
    tag_box = dict(boxstyle="round,pad=0.15", fc="white", ec="none")
    ax.text(47.5, 61.0, "PL（可编程逻辑，100 MHz）", fontsize=9.5, color="#555555",
            bbox=tag_box)
    ax.add_patch(Rectangle((46, 3), 76, 25, fill=False, ls="--", lw=1.2,
                           edgecolor="#888888"))
    ax.text(47.5, 26.4, "PS（ARM Cortex-A9，Linux，PYNQ 2.7 / Python 3.6）",
            fontsize=9.5, color="#555555", bbox=tag_box)

    bx(2, 46, 20, 10, "麦克风\n(TRRS, HP+Mic)", fc=ORANGE)
    bx(2, 30, 20, 10, "耳机 / 扬声器\n(TRRS, HP+Mic)", fc=ORANGE)
    bx(25, 33, 18, 24, "ADAU1761\ncodec\n\nADC / DAC\n48 kHz, int16", fc=ORANGE, fs=9.5)
    bx(50, 48, 26, 11, "audio_codec_ctrl\n(PYNQ 音频 IP)\nMMIO，无 DMA", fc=GREEN)
    bx(84, 48, 34, 11,
       "fir_multiband 核 v25\n四段相减 FIR + WDRC\n36 拍/采样 @ 100 MHz",
       fc=LBLUE, weight="bold", fs=9.5)
    bx(88, 22, 30, 9, "DDR3\n块缓冲 in[] / out[]", fc="#eeeeee")
    bx(48, 6, 22, 12, "fir_live.py\n搬样线程\n(SCHED_FIFO)", fc=LBLUE, fs=9.5)
    bx(76, 6, 20, 9, "网页验配界面\n/dev/shm/fir_tune.json", fc=LBLUE, fs=8.5)

    ar((22, 51), (25, 51), "mic", dy=1.9, fs=8.5)
    ar((25, 35), (22, 35), "out", dy=-1.9, fs=8.5)
    ar((43, 45), (50, 51), "I2S", dy=-6.5)
    ar((101, 48), (101, 31), "AXI4 主机 → S_AXI_HP0", fs=8.5)
    ar((88, 24.5), (70, 15), "cffi：拷 in[] / out[]", dx=9, dy=-2, fs=8.5)
    ar((59, 18), (59, 48), "MMIO 读写 FIFO\n(duplex_block)", dx=13, fs=8.5)
    ar((70, 18), (86, 48), "AXI-Lite", rad=-0.28, dx=9, dy=10, fs=8.5)

    ax.text(62, 0.6,
            "虚线框是 PL / PS 边界。音频 IP 没有 DMA：PS 用 MMIO 读写它的 FIFO，\n"
            "核通过自己的 AXI4 主机口从 DDR 取块。",
            fontsize=8.5, color="#666666", ha="center", va="center", linespacing=1.4)

    fig.tight_layout()
    save(fig, "fig2_1_block.png")


def fig_chain():
    fig, ax = plt.subplots(figsize=(10.4, 4.6))
    ax.set_xlim(0, 12.9)
    ax.set_ylim(0, 5)
    ax.axis("off")

    box(ax, 0.2, 2.0, 1.3, 0.9, "输入\nint16/Q1.15", fc="#eef2f5", fs=9)
    box(ax, 2.0, 3.1, 1.6, 0.8, "LP500\n193 抽头", fs=9)
    box(ax, 2.0, 2.05, 1.6, 0.8, "LP1000\n193 抽头", fs=9)
    box(ax, 2.0, 1.0, 1.6, 0.8, "LP2000\n193 抽头", fs=9)
    box(ax, 4.3, 1.0, 1.5, 3.0, "相减\n组合\n4 段", fc="#fdf1e8", ec=ACCENT, fs=9)
    box(ax, 6.4, 2.0, 1.5, 0.9, "每段增益\nQ7.8", fs=9)
    box(ax, 8.6, 1.95, 2.0, 1.0, "每段 WDRC\n包络平滑\nattack / release", fs=8)
    box(ax, 11.1, 2.0, 1.5, 0.9, "求和 +\n饱和输出", fs=9)

    for y in (3.5, 2.45, 1.4):
        arrow(ax, 1.5, 2.45, 2.0, y)
    for y in (3.5, 2.45, 1.4):
        arrow(ax, 3.6, y, 4.3, 2.5)
    arrow(ax, 5.8, 2.5, 6.4, 2.45)
    arrow(ax, 7.9, 2.45, 8.6, 2.45)
    arrow(ax, 10.6, 2.45, 11.1, 2.45)

    ax.plot([1.5, 1.8, 1.8], [2.45, 2.45, 0.55], color=GREY, lw=1.0)
    ax.plot([1.8, 4.3], [0.55, 0.55], color=GREY, lw=1.0)
    ax.text(2.4, 0.35, "延迟 96 采样（2.00 ms），band[3] = 延迟输入 − LP2000",
            fontsize=9, color=GREY)

    ax.text(0.2, 4.6, "相减式四段分频与 WDRC 信号链", fontsize=12, color=INK)
    save(fig, "fig2_2_chain.png")


def fig_response():
    path = os.path.join(ROOT, "data", "results", "four_band_response_20261022.csv")
    freq, bands = [], [[] for _ in range(4)]
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            freq.append(float(row["freq"]))
            for i in range(4):
                bands[i].append(float(row["band%d" % i]))
    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    colors = [BLUE, GREEN, "#b07a2a", "#7a5aa0"]
    for i, c in enumerate(colors):
        ax.semilogx(freq, bands[i], lw=1.5, color=c, label="段%d" % i)
    ax.semilogx(freq, [0.0] * len(freq), lw=1.4, color=INK, ls="--", label="合成（0 dB）")
    for f in (500, 1000, 2000):
        ax.axvline(f, color=GREY, lw=0.8, ls=":")
    ax.set_xlim(20, 20000)
    ax.set_ylim(-100, 10)
    ax.set_xlabel("频率 / Hz")
    ax.set_ylabel("幅度 / dB")
    ax.set_title("四段分频频响（按核内 Q1.17 系数计算）", fontsize=12)
    ax.grid(True, which="both", color="#dfe3e6", lw=0.6)
    ax.legend(ncol=5, fontsize=9, loc="lower center")
    save(fig, "fig2_3_response.png")


def fig_versions():
    versions = ["v13\n449 拍", "v19\n31 拍", "v20\n35 拍", "v21\n49 拍", "v25\n36 拍"]
    cycles = [449, 31, 35, 49, 36]
    dsp = [202, 200, 200, 205, 205]
    fig, ax = plt.subplots(figsize=(8.2, 4.4))
    x = range(len(versions))
    bars = ax.bar(x, cycles, color=[GREY, GREY, GREY, GREY, ACCENT], width=0.55)
    ax.set_yscale("log")
    ax.set_ylabel("拍 / 采样（对数轴）")
    ax.set_xticks(list(x))
    ax.set_xticklabels(versions)
    ax.set_ylim(20, 700)
    for b, v in zip(bars, cycles):
        ax.text(b.get_x() + b.get_width() / 2, v * 1.08, str(v), ha="center", fontsize=9)
    ax2 = ax.twinx()
    ax2.plot(list(x), dsp, color=BLUE, marker="o", lw=1.4)
    ax2.set_ylabel("DSP 数量", color=BLUE)
    ax2.set_ylim(150, 230)
    ax2.tick_params(axis="y", colors=BLUE)
    ax.set_title("HLS 版本演进与资源对照", fontsize=12)
    ax.grid(axis="y", color="#dfe3e6", lw=0.6)
    save(fig, "fig2_4_versions.png")


def fig_resources():
    names = ["LUT", "FF", "DSP", "BRAM"]
    pct = [12.54, 10.22, 93.18, 39.29]
    counts = ["6,669", "10,876", "205 / 220", "55"]
    fig, ax = plt.subplots(figsize=(7.6, 3.6))
    bars = ax.barh(names, pct, color=[BLUE, BLUE, ACCENT, BLUE], height=0.55)
    ax.set_xlim(0, 100)
    ax.set_xlabel("占 xc7z020clg400-1 可用资源 / %")
    for b, p, c in zip(bars, pct, counts):
        ax.text(b.get_width() + 1.5, b.get_y() + b.get_height() / 2,
                "%s（%.1f%%）" % (c, p), va="center", fontsize=9)
    ax.set_title("v25 整机资源占用（WNS +0.533 ns，失败端点 0）", fontsize=11)
    ax.grid(axis="x", color="#dfe3e6", lw=0.6)
    save(fig, "fig3_1_resources.png")


def fig_latency():
    tiers = ["稳定档\n480 / 3", "中间档\n480 / 2", "低延迟档\n360 / 2"]
    digital = [29.80, 19.86, 14.92]
    acoustic = [1.23, 1.23, 1.23]
    end = [31.0, 21.1, 16.2]
    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    x = range(len(tiers))
    ax.bar(x, digital, width=0.5, color=BLUE, label="数字段（块流水）")
    ax.bar(x, acoustic, width=0.5, bottom=digital, color="#b8cbd8",
           label="声学段 R（DAC→耳机→空气→话筒→ADC）")
    for i in range(len(tiers)):
        ax.text(i, end[i] + 0.8, "%.1f ms" % end[i], ha="center", fontsize=10, color=INK)
    ax.set_xticks(list(x))
    ax.set_xticklabels(tiers)
    ax.set_ylim(0, 36)
    ax.set_ylabel("端到端延迟 / ms")
    ax.set_title("三档数字段与端到端延迟", fontsize=12)
    ax.legend(fontsize=8.5, loc="upper right")
    ax.grid(axis="y", color="#dfe3e6", lw=0.6)
    save(fig, "fig3_2_latency.png")


def fig_soak():
    runs = ["10-23 稳定档\n480 / 3", "10-23 低延迟\n360 / 2", "10-24 复测\n关插桩"]
    blocks = [60192, 80103, 80188]
    secs = [600.02, 599.92, 600.01]
    gaps = [3.683, 3.846, None]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.8, 4.2))
    x = range(len(runs))
    a1.bar(x, blocks, width=0.5, color=BLUE)
    for i, (b, s) in enumerate(zip(blocks, secs)):
        a1.text(i, b * 1.01, "%s 块 / %.2f s\n丢块 0" % (format(b, ","), s),
                ha="center", fontsize=8)
    a1.set_xticks(list(x))
    a1.set_xticklabels(runs, fontsize=8.5)
    a1.set_xlim(-0.6, len(runs) - 0.4)
    a1.set_ylim(0, max(blocks) * 1.22)
    a1.set_ylabel("处理块数")
    a1.set_title("三次约 600 秒连续运行：丢块均为 0", fontsize=10)
    a1.grid(axis="y", color="#dfe3e6", lw=0.6)

    vals = [g if g is not None else 0 for g in gaps]
    bars = a2.bar(x, vals, width=0.5, color=[BLUE, BLUE, "#ffffff"], edgecolor=INK)
    bars[2].set_hatch("//")
    for i, g in enumerate(gaps):
        a2.text(i, (g or 0) + 0.08, "%.2f ms" % g if g else "未作判据", ha="center", fontsize=8)
    a2.set_xticks(list(x))
    a2.set_xticklabels(runs, fontsize=8.5)
    a2.set_xlim(-0.6, len(runs) - 0.4)
    a2.set_ylim(0, 4.8)
    a2.set_ylabel("最大块间空档 / ms")
    a2.set_title("0 丢块不等于 0 空档", fontsize=10)
    a2.grid(axis="y", color="#dfe3e6", lw=0.6)
    fig.tight_layout()
    save(fig, "fig3_3_soak.png")


def fig_wdrc_curve():
    path = os.path.join(ROOT, "data", "results", "wdrc_curve_20261023.json")
    data = json.load(open(path, encoding="utf-8"))["A"]
    fig, ax = plt.subplots(figsize=(7.4, 4.6))
    colors = [GREY, BLUE, GREEN, ACCENT]
    for i, setting in enumerate(data):
        tone = setting["tones"]["250"]
        ax.plot(tone["in_db"], tone["real_db"], marker="o", ms=3.5, lw=1.3,
                color=colors[i], label=setting["setting"])
    tone = data[0]["tones"]["250"]
    ax.plot(tone["in_db"], tone["in_db"], color=INK, lw=1.0, ls="--", label="直通")
    ax.set_xlabel("输入峰值 / dBFS")
    ax.set_ylabel("输出峰值 / dBFS")
    ax.set_title("WDRC 静态传输曲线（250 Hz，13 档阶梯）", fontsize=12)
    ax.grid(True, color="#dfe3e6", lw=0.6)
    ax.legend(fontsize=8.5, loc="upper left")
    save(fig, "fig3_4_wdrc_curve.png")


def fig_thd():
    path = os.path.join(ROOT, "data", "results", "wdrc_curve_20261023.json")
    data = json.load(open(path, encoding="utf-8"))["C"]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    colors = [GREY, BLUE, ACCENT]
    for i, conf in enumerate(data):
        levels = [r["level_db"] for r in conf["rows"]]
        thd = [r["thd"] * 100 for r in conf["rows"]]
        ax.plot(levels, thd, marker="o", ms=4, lw=1.4, color=colors[i], label=conf["name"])
    ax.set_xlabel("输入电平 / dBFS")
    ax.set_ylabel("THD / %")
    ax.set_ylim(0, 50)
    ax.set_title("WDRC 失真随输入电平与压缩强度变化（700 Hz）", fontsize=12)
    ax.grid(True, color="#dfe3e6", lw=0.6)
    ax.legend(fontsize=8.5)
    save(fig, "fig3_5_thd.png")


def fig_ab_loudness():
    """§3.4.1：原始 / 过核未补偿 / 过核补回等响度。数据来自板上实录那一对 wav。"""
    import wave

    def rd(name):
        with wave.open(os.path.join(ROOT, "data", "audio", name), "rb") as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64)

    x = rd("ab_20261024_01_original.wav")
    y = rd("ab_20261024_02_core.wav")
    z = rd("ab_20261024_03_matched.wav")
    fs = 48000
    t = np.arange(len(x)) / fs

    win = int(0.1 * fs)
    nb = len(x) // win
    def env(v):
        return 20 * np.log10(np.sqrt(np.mean(v[:nb * win].reshape(nb, win) ** 2, axis=1)) / 32768.0 + 1e-12)
    te = (np.arange(nb) + 0.5) * 0.1
    e1, e2, e3 = env(x), env(y), env(z)

    # 0.12 秒窗口：取压缩量（原始与过核包络差）最大的那一段
    fig, a1 = plt.subplots(figsize=(9.2, 4.2))
    a1.plot(te, e1, color="#7d868f", lw=1.3, label="原始输入")
    a1.plot(te, e2, color=BLUE, lw=1.2, label="过核（未补偿）")
    a1.plot(te, e3, color=ACCENT, lw=1.3, ls="--", label="过核 + 等响度补偿")
    a1.set_xlim(0, t[-1])
    a1.set_ylim(-60, -5)
    a1.set_xlabel("时间 / s")
    a1.set_ylabel("100 ms 包络 / dBFS")
    a1.set_title("原始与等响度补偿后：整段 RMS 都是 −17.85 dBFS", fontsize=11)
    a1.grid(True, color="#dfe3e6", lw=0.6)
    a1.legend(fontsize=8.5, ncol=3, loc="lower right")

    fig.tight_layout()
    save(fig, "fig3_6_ab_loudness.png")


def fig_software():
    """2.3.1 软件架构：控制面与数据面只在一对 /dev/shm json 上相交。"""
    fig, ax = plt.subplots(figsize=(9.4, 8.2))
    ax.set_xlim(0, 100)
    ax.set_ylim(-8, 108)
    ax.axis("off")

    def bx(x, y, w, h, text, fc="#eef2f5", ec=INK, fs=8, weight="normal"):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.25,rounding_size=0.5",
                                    lw=1.1, edgecolor=ec, fc=fc, zorder=2))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
                color=INK, weight=weight, linespacing=1.45, zorder=3)

    def poly(pts, label="", lx=None, ly=None, ha="left", ls="-", fs=7.5):
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=INK, lw=1.0, ls=ls,
                solid_capstyle="butt", solid_joinstyle="miter", zorder=1)
        ax.add_patch(FancyArrowPatch(pts[-2], pts[-1], arrowstyle="-|>",
                                     mutation_scale=10, lw=1.0, color=INK, linestyle=ls,
                                     shrinkA=0, shrinkB=0, zorder=2))
        if label:
            ax.text(lx, ly, label, ha=ha, va="center", fontsize=fs, color=INK, zorder=4,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none"))

    # 区域：板端 PS / 板端 PL，标签竖排在框内左边，不压任何走线
    ax.add_patch(Rectangle((2, 44), 96, 48, fill=False, ls="--", lw=1.1, edgecolor="#9aa2a9"))
    ax.text(4.6, 68, "板端 PS：ARM Cortex-A9 · PYNQ 2.7 · Linux", fontsize=7.5,
            color="#70787f", rotation=90, ha="center", va="center")
    ax.add_patch(Rectangle((2, 2), 96, 38, fill=False, ls="--", lw=1.1, edgecolor="#9aa2a9"))
    ax.text(4.6, 21, "板端 PL：100 MHz", fontsize=7.5, color="#70787f",
            rotation=90, ha="center", va="center")

    # 浏览器（PC 上）
    bx(12, 96, 46, 10, "浏览器（PC）：验配页面 page.html\n四段增益 / 门限 / 压缩比 / 起振 / 释放",
       fc="#fdf1e8", ec=ACCENT)
    poly([(32, 96), (32, 90)], "HTTP：/api/tune · /api/start · /api/status", 33.5, 93)

    # 控制面
    bx(8, 80, 38, 10, "fir_webui.py　HTTP 服务 :8080\n起停 fir_live 子进程、写参数文件", fc="#dbe7f3")
    poly([(32, 80), (32, 72)], "写参数", 33.5, 76.5)
    bx(20, 62, 26, 10, "/dev/shm/fir_tune.json\n旋钮参数", fc="#eeeeee")
    bx(8, 50, 38, 10, "/dev/shm/fir_status.json\n状态 + 电平", fc="#eeeeee")
    poly([(12, 60), (12, 80)], "读状态（20 Hz）", 13.5, 75)

    # 数据面：两条线程 + 一个 C 库
    bx(54, 78, 20, 12, "dsp 线程\nSCHED_FIFO 10\n过核 + 写状态", fc="#dbe7f3")
    bx(82, 78, 16, 12, "io 线程\nSCHED_FIFO 20\n只调 duplex_block", fc="#dbe7f3")
    poly([(74, 86), (82, 86)], "q1", 78, 88.3, ha="center", fs=7)
    poly([(82, 82), (74, 82)], "q2", 78, 79.7, ha="center", fs=7)
    bx(82, 46, 16, 10, "libaudio_stream.so\n（C++，cffi）", fc="#e8eef3")
    poly([(90, 78), (90, 56)], "cffi", 91.5, 67)

    # 调参通道：dsp 每 0.05 s 读一次，写完状态再写回文件
    poly([(46, 67), (50, 67), (50, 76), (56, 76), (56, 78)], "每 0.05 s 读一次", 51, 76.9, fs=7)
    poly([(62, 78), (62, 72), (53, 72), (53, 54), (46, 54)], "写状态 / 电平", 52, 70.4, fs=7)

    # PL：核与 DDR、音频 IP 与 codec
    bx(52, 22, 22, 12, "fir_multiband 核 v25\n36 拍 / 采样", fc="#dbe7f3", weight="bold")
    bx(76, 22, 22, 12, "音频 IP（codec ctrl）\nMMIO FIFO，无 DMA", fc="#dcefd9")
    bx(52, 6, 22, 10, "DDR3 块缓冲\ncin[] / cout[]", fc="#eeeeee")
    bx(76, 6, 22, 10, "ADAU1761 codec\n麦克风 / 耳机（TRRS）", fc="#fbe3cc")
    poly([(63, 78), (63, 34)], "AXI-Lite 写寄存器", 64.5, 60)
    poly([(90, 46), (90, 34)], "MMIO 读写 FIFO", 88.5, 42, ha="right")
    poly([(63, 22), (63, 16)], "AXI4 主机 S_AXI_HP0", 64.5, 19)
    poly([(87, 22), (87, 16)], "I2S", 85.5, 19, ha="right")

    ax.text(2, -4.5,
            "左为控制面、右为数据面：两条线程不共享内存里的任何旋钮，只通过 /dev/shm 下两个 json 文件交换参数与状态。\n"
            "q1 / q2 是两条 queue.SimpleQueue —— io 把收到的整块交给 dsp，dsp 把算好的一块交回 io；界面不碰 MMIO。",
            fontsize=7.5, color="#70787f", linespacing=1.6)
    save(fig, "fig2_5_software.png")


if __name__ == "__main__":
    fig_flow()
    fig_block()
    fig_chain()
    fig_response()
    fig_versions()
    fig_resources()
    fig_latency()
    fig_soak()
    fig_wdrc_curve()
    fig_thd()
    fig_ab_loudness()
    fig_software()
