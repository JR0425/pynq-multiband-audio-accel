"""生成最终报告用的图，输出到 data/figures/report/。

统一口径：全篇按演示线 PL 直连 fir_pl.bit 取值，不与块模式 fir.bit 并列。

数据来源：
  2-3  data/results/four_band_response_20261022.csv
  2-4  data/results/impl_metrics.md（拍数与资源，核级 v25，两条位流共用）
  3-1  board/overlay/fir_pl_util.rpt、fir_pl_timing.rpt（整机实现）
  3-2  data/results/dma_pl_direct_20261025.md（延迟预算，推算非实测）
  3-3  data/results/soak600_20261023.md、live_dropout_recheck_20261024.md（块模式出货线回归）
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
plt.rcParams["axes.axisbelow"] = True
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
    """系统整体框图（中文版，对应 fig2_1）：PL 直连 fir_pl.bit，音频全程不出 PL。

    英文版见 plot_block_diagram.py。
    """
    ORANGE = "#fbe3cc"
    GREEN = "#dcefd9"
    LBLUE = "#dbe7f3"

    def bx(x, y, w, h, text, fc=GREY, fs=10, weight="normal"):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.25",
                                    lw=1.1, edgecolor=INK, fc=fc))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
                fontsize=fs, color=INK, weight=weight, linespacing=1.45)

    def ar(p0, p1, label="", rad=0.0, fs=9, dx=0, dy=0, ls="-"):
        ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=12,
                                     lw=1.1, color=INK, linestyle=ls,
                                     connectionstyle="arc3,rad=%s" % rad))
        if label:
            ax.text((p0[0] + p1[0]) / 2 + dx, (p0[1] + p1[1]) / 2 + dy, label,
                    ha="center", va="center", fontsize=fs, color=INK,
                    bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none"))

    fig, ax = plt.subplots(figsize=(9.8, 5.6))
    ax.set_xlim(0, 124)
    ax.set_ylim(0, 64)
    ax.axis("off")

    # PL / PS 边界
    ax.add_patch(Rectangle((46, 35), 76, 25, fill=False, ls="--", lw=1.2,
                           edgecolor="#888888"))
    tag_box = dict(boxstyle="round,pad=0.15", fc="white", ec="none")
    ax.text(47.5, 58.6, "PL（可编程逻辑，100 MHz）· 音频全程不出 PL", fontsize=9.5,
            color="#555555", bbox=tag_box)
    ax.add_patch(Rectangle((46, 6), 76, 24, fill=False, ls="--", lw=1.2,
                           edgecolor="#888888"))
    ax.text(47.5, 28.2, "PS（ARM Cortex-A9，Linux，PYNQ 2.7 / Python 3.6）",
            fontsize=9.5, color="#555555", bbox=tag_box)

    # 板外器件与 codec
    bx(2, 45, 18, 9, "麦克风\n(TRRS, HP+Mic)", fc=ORANGE, fs=9.5)
    bx(2, 29, 18, 9, "耳机 / 扬声器\n(TRRS, HP+Mic)", fc=ORANGE, fs=9.5)
    bx(24, 31, 17, 22, "ADAU1761\ncodec\n\nADC / DAC\n48 kHz, int16", fc=ORANGE, fs=9.5)

    # PL 内：蛇形闭环 codec → 收 → 四段 → WDRC → 发 → codec
    bx(49, 47, 19, 9, "i2s_duplex（收）\nADC 样本入 PL", fc=GREEN, fs=8.5)
    bx(75, 47, 20, 9, "fir_mb_fe\n四段相减 FIR", fc=LBLUE, weight="bold", fs=9)
    bx(99, 47, 21, 9, "fir_drc4\n四段 WDRC", fc=LBLUE, weight="bold", fs=9)
    bx(99, 36.5, 21, 7.5, "i2s_duplex（发）\n样本出 PL → DAC", fc=GREEN, fs=8.5)

    # PS 内：最小驱动面
    bx(50, 11, 34, 12, "fir_pl_demo.py\n加载 fir_pl.bit overlay\nI2C 配一次 codec · 轮询 ctrl/stat",
       fc=LBLUE, fs=8.5)
    bx(90, 11, 28, 12, "四段增益 / WDRC 参数\n编译期写死，无寄存器\n网页验配驱动不了",
       fc="#f6f0e8", fs=8.5)

    # 连线
    ar((20, 49.5), (24, 49.5), "mic", dy=1.7, fs=8.5)
    ar((24, 33.5), (20, 33.5), "out", dy=-1.7, fs=8.5)
    ar((41, 46), (49, 51.5), "I2S", dy=-4.6, fs=8.5)
    ar((68, 51.5), (75, 51.5), "逐样点流", fs=8)
    ar((95, 51.5), (99, 51.5), fs=8.5)
    ar((109.5, 47), (109.5, 44), fs=8.5)
    ar((99, 40.25), (41, 40.25), "I2S 返回 codec", dy=1.8, dx=8, fs=8.5)
    ar((67, 23), (67, 35), ls="--")
    ax.text(69, 32.2, "AXI-Lite：ctrl / stat（PS 只读状态）", fontsize=8.5, color=INK,
            ha="left", va="center",
            bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none"))

    ax.text(62, 2.6,
            "虚线框是 PL / PS 边界。音频全程在 PL 内闭环：codec → i2s_duplex → fir_mb_fe（相减四段）→ fir_drc4（WDRC）→ i2s_duplex → codec，\n"
            "不过 PS、不过 DDR；PS 只在启动时加载 overlay、配一次 codec，之后轮询 ctrl/stat。",
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
    """fir_pl.bit 整机资源：数值取自 board/overlay/fir_pl_util.rpt，时序取自 fir_pl_timing.rpt。"""
    names = ["LUT", "FF", "DSP", "BRAM"]
    pct = [38.55, 12.74, 96.82, 7.14]
    counts = ["20,509", "13,556", "213 / 220", "10"]
    fig, ax = plt.subplots(figsize=(7.6, 3.6))
    bars = ax.barh(names, pct, color=[BLUE, BLUE, ACCENT, BLUE], height=0.55)
    ax.set_xlim(0, 100)
    ax.set_xlabel("占 xc7z020clg400-1 可用资源 / %")
    for b, p, c in zip(bars, pct, counts):
        lab = "%s（%.1f%%）" % (c, p)
        if p > 90:  # 长条标签放到柱内右端，避免超出坐标框被裁
            ax.text(b.get_width() - 2, b.get_y() + b.get_height() / 2, lab,
                    va="center", ha="right", fontsize=9, color="white")
        else:
            ax.text(b.get_width() + 1.5, b.get_y() + b.get_height() / 2, lab,
                    va="center", fontsize=9)
    ax.set_title("fir_pl.bit 整机资源占用（WNS +0.090 ns，失败端点 0）", fontsize=11)
    ax.grid(axis="x", color="#dfe3e6", lw=0.6)
    save(fig, "fig3_1_resources.png")


def fig_latency():
    """PL 直连 fir_pl.bit 只有一条通路，不存在块模式的三档延迟。

    1.97 ms 核群延迟 = FIFO 水位 + 核内 2048 拍；1.23 ms 声学往返 R。
    两者均为预算推算，未实测、未做人耳验收。
    """
    core, acou = 1.97, 1.23
    total = core + acou
    fig, ax = plt.subplots(figsize=(6.0, 4.4))
    ax.bar([0], [core], width=0.44, color=BLUE)
    ax.bar([0], [acou], width=0.44, bottom=[core], color="#b8cbd8")
    ax.text(0, core / 2, "核群 %.2f ms" % core, ha="center", va="center",
            fontsize=10, color="white", weight="bold")
    ax.text(0, core + acou / 2, "声学 R %.2f ms" % acou, ha="center", va="center",
            fontsize=10, color=INK)
    ax.text(0, total + 0.13, "%.1f ms（推算）" % total, ha="center", va="bottom",
            fontsize=11.5, color=ACCENT, weight="bold")
    ax.set_xlim(-0.6, 0.6)
    ax.set_ylim(0, 4.1)
    ax.set_xticks([0])
    ax.set_xticklabels(["fir_pl.bit\n单一通路，无三档"])
    ax.set_ylabel("端到端延迟 / ms")
    ax.set_title("PL 直连端到端延迟预算", fontsize=12)
    ax.grid(axis="y", color="#dfe3e6", lw=0.6)
    ax.text(0.5, -0.20,
            "PL 直连没有「稳定 / 中间 / 低延迟」三档：延迟由 FIFO 水位、核内拍数与 codec 固定时延决定，\n"
            "不随增益 / 门限改变。核群 1.97 ms + 声学 1.23 ms 为预算推算，未实测、未做人耳验收。",
            transform=ax.transAxes, ha="center", va="top", fontsize=8,
            color="#666666", linespacing=1.5)
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
    fig.suptitle("块模式（出货线 fir.bit）约 600 秒长跑：鲁棒性回归", fontsize=11.5)
    fig.tight_layout(rect=(0, 0.075, 1, 0.96))
    fig.text(0.5, 0.015,
             "口径说明：本图为出货线 fir.bit（块模式，音频经 PS 逐块搬运）的鲁棒性回归，与正文 PL 直连 fir_pl.bit 的性能数值不并列；\n"
             "PL 直连的对应结论为 60 秒零丢字、零断流，未做 600 秒计数。",
             ha="center", va="bottom", fontsize=7.5, color="#70787f", linespacing=1.5)
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
    """2.3.1 软件架构（PL 直连）：PS 只剩启动编排，没有音频数据路径。

    PS 不搬样、不起实时线程、不与 /dev/shm 交换参数；四段增益与 WDRC 参数在编译期写死。
    """
    fig, ax = plt.subplots(figsize=(8.8, 7.6))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 108)
    ax.axis("off")

    def bx(x, y, w, h, text, fc="#eef2f5", ec=INK, fs=8, weight="normal"):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.25,rounding_size=0.5",
                                    lw=1.1, edgecolor=ec, fc=fc, zorder=2))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
                color=INK, weight=weight, linespacing=1.45, zorder=3)

    def arr(p0, p1, label="", fs=7.5, dx=0, dy=0, ls="-"):
        ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=10,
                                     lw=1.0, color=INK, linestyle=ls,
                                     shrinkA=0, shrinkB=0, zorder=2))
        if label:
            ax.text((p0[0] + p1[0]) / 2 + dx, (p0[1] + p1[1]) / 2 + dy, label,
                    ha="center", va="center", fontsize=fs, color=INK, zorder=4,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none"))

    # 左：PS 侧启动编排（无音频数据路径）
    ax.add_patch(Rectangle((2, 20), 56, 84, fill=False, ls="--", lw=1.1, edgecolor="#9aa2a9"))
    ax.text(4, 101.5, "PS：ARM Cortex-A9 · PYNQ 2.7 · Linux", fontsize=8, color="#70787f")

    bx(6, 88, 48, 9, "上电：加载 PYNQ 2.7 镜像，启动 Python 3.6", fc="#dbe7f3", fs=8.5)
    bx(6, 74, 48, 9, "Overlay(\"fir_pl.bit\")：编程 PL", fc="#dbe7f3", fs=8.5, weight="bold")
    bx(6, 60, 48, 9, "I2C 配一次 ADAU1761：采样率 / 增益", fc="#dbe7f3", fs=8.5)
    bx(6, 46, 48, 9, "轮询 ctrl / stat：溢出 · 欠载 · 块计数", fc="#dbe7f3", fs=8.5)
    bx(6, 24, 48, 11, "PL：codec → 四段 FIR → WDRC → codec\n音频全程在 PL 内闭环（详见图 2-1）",
       fc="#dcefd9", fs=8.5)

    arr((30, 88), (30, 83), fs=7)
    arr((30, 74), (30, 69), fs=7)
    arr((30, 60), (30, 55), fs=7)
    arr((30, 46), (30, 35), "AXI-Lite ctrl / stat（不含音频）", fs=7.5, dx=3)

    # 右：编译期固定参数
    ax.add_patch(Rectangle((60, 20), 38, 84, fill=False, ls="--", lw=1.1, edgecolor="#9aa2a9"))
    ax.text(62, 101.5, "编译期（Vivado / Vitis HLS 2020.2）", fontsize=8, color="#70787f")

    bx(64, 76, 30, 9, "综合 · 实现\n→ fir_pl.bit", fc="#fdf1e8", ec=ACCENT, fs=8.5,
       weight="bold")
    bx(64, 62, 30, 9, "固化为 RTL 常量\nQ1.17 / Q7.8", fc="#f7f2ec", fs=8.5)
    bx(64, 48, 30, 9, "四段增益 · 门限 · 压缩比\n起振 / 释放", fc="#f7f2ec", fs=8.5)

    arr((79, 57), (79, 62), fs=7)
    arr((79, 71), (79, 76), fs=7)
    arr((64, 80.5), (54, 78.5), "位流", fs=7.5, dx=-1, dy=2.5)

    ax.text(64, 42,
            "fir_pl.bit 没有增益寄存器：四段增益与\n"
            "WDRC 参数编译期写死。改参数必须重新\n"
            "综合 + 重新加载 overlay，网页验配那一\n"
            "套驱动不了它。",
            fontsize=7.8, color="#6b6257", ha="left", va="top", linespacing=1.6)

    ax.text(2, 4,
            "PL 直连下 PS 侧没有音频数据路径：不搬样、不起 SCHED_FIFO 实时线程、不与 /dev/shm 交换参数，\n"
            "因此也没有 dsp / io 双线程与 queue 队列 —— 音频吞吐完全由 PL 内的 I2S 收发与核流水决定。",
            fontsize=7.8, color="#70787f", ha="left", va="center", linespacing=1.6)
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
