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
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

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


if __name__ == "__main__":
    fig_flow()
    fig_chain()
    fig_response()
    fig_versions()
    fig_resources()
    fig_latency()
    fig_soak()
    fig_wdrc_curve()
    fig_thd()
