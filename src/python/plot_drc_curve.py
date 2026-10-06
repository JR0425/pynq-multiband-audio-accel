"""把板上第 9 节量出来的「电平台阶」画成压缩器的输入-输出曲线。

为什么要有这个：
    报告里说「压缩器把电平跨度压窄了 8.7 dB」，光靠一行字没人信，也没法看。
    台阶测试是压缩器最标准的表征方法 —— 横轴给进去多响、纵轴出来多响，
    曲线偏离 45° 斜线的程度就是压缩量。评委认这张图。

数据从哪来：
    data/results/fir_audio_loop_run_*.txt 里第 9 节那张表。**板上实测的数**，
    这个脚本不重算 —— 重算出来的是电脑上的仿真，不是板子上的成绩，两回事。

用法（仓库根目录）：
    python src/python/plot_drc_curve.py                    # 自动找最新的一份 run 记录
    python src/python/plot_drc_curve.py --run data/results/fir_audio_loop_run_20261006.txt
输出：
    data/figures/drc_transfer_curve.png
"""

import argparse
import glob
import os
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

# int16 的有效值满量程。dBFS 用它当 0 dB —— 和 plot_spectrum_waterfall.py 一致。
FULL_SCALE = 32768.0

# 台阶表的每一行：档位 输入 直通 压缩后 相对直通。
# 4c 那张扫描表是 "(0.100, 0.70)  4383 ..." 开头，这里匹配不上，不会混进来。
ROW = re.compile(r"^\s*([+-]\d+)\s+dB\s+(\d+)\s+(\d+)\s+(\d+)\s+([+-][\d.]+)\s+dB\s*$")
SPAN = re.compile(r"输入跨\s*([\d.]+)\s*dB，输出跨\s*([\d.]+)\s*dB")


def pick_font():
    """挑一个装了的中文字体。一个都没有就退回英文标签。

    matplotlib 自带的 DejaVu Sans 没有汉字，不设这个的话图里全是方框，
    而且它不报错 —— 只会静默画出一堆豆腐块。
    """
    want = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Source Han Sans SC",
            "WenQuanYi Zen Hei", "PingFang SC", "Heiti SC", "Arial Unicode MS"]
    have = {f.name for f in font_manager.fontManager.ttflist}
    for name in want:
        if name in have:
            return name
    return None


def parse_run(path):
    """从 run 记录里抠出台阶表和跨度那一行。"""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    steps = []
    for ln in lines:
        m = ROW.match(ln)
        if m:
            steps.append((int(m.group(1)), float(m.group(2)), float(m.group(3)),
                          float(m.group(4)), float(m.group(5))))
    if not steps:
        raise SystemExit("%s 里没找到第 9 节的台阶表 —— 这份记录是旧版（没跑第 9 节）？"
                         % path)

    span = None
    for ln in lines:
        m = SPAN.search(ln)
        if m:
            span = (float(m.group(1)), float(m.group(2)))
            break
    return steps, span


def db(x):
    return 20.0 * np.log10(x / FULL_SCALE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None,
                    help="run 记录文件；不给就取 data/results 里最新的 fir_audio_loop_run_*.txt")
    ap.add_argument("--out", default="data/figures/drc_transfer_curve.png")
    args = ap.parse_args()

    if args.run:
        run_path = args.run
    else:
        cands = sorted(glob.glob("data/results/fir_audio_loop_run_*.txt"))
        if not cands:
            raise SystemExit("data/results/ 下没有 fir_audio_loop_run_*.txt")
        run_path = cands[-1]
    print("读：%s" % run_path)

    steps, span = parse_run(run_path)
    lv = np.array([s[0] for s in steps], dtype=float)
    # 前三列是 int16 有效值，要转 dBFS；第四列本来就是 dB，不能再转一次。
    x_in = db(np.array([s[1] for s in steps], dtype=float))
    x_dry = db(np.array([s[2] for s in steps], dtype=float))
    x_wet = db(np.array([s[3] for s in steps], dtype=float))
    lift_col = np.array([s[4] for s in steps], dtype=float)

    # 两个自检：直通列必须等于输入列（核在 bypass 下只延迟不改幅度），
    # 升量列必须等于「压缩后 − 直通」。对不上说明表抠错了，别往下画。
    d_dry = float(np.abs(x_dry - x_in).max())
    if d_dry > 0.05:
        raise SystemExit("直通列和输入列差 %.2f dB —— 台阶表拼错了？" % d_dry)
    d_lift = float(np.abs((x_wet - x_dry) - lift_col).max())
    if d_lift > 0.15:
        raise SystemExit("升量列和「压缩后−直通」差 %.2f dB —— 台阶表拼错了？" % d_lift)
    print("自检：直通与输入最大差 %.3f dB，升量列最大差 %.3f dB" % (d_dry, d_lift))

    if span:
        print("跨度：输入 %.1f dB，输出 %.1f dB，压窄 %.1f dB"
              % (span[0], span[1], span[0] - span[1]))

    font = pick_font()
    if font:
        plt.rcParams["font.sans-serif"] = [font, "DejaVu Sans"]
        print("中文字体：%s" % font)
    else:
        print("没找到中文字体 —— 图里的标签改用英文")
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 130

    zh = font is not None

    def L(cn, en):
        return cn if zh else en

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.6, 4.5))

    # ---- 左：输入-输出曲线 ----
    lo = min(x_in.min(), x_wet.min()) - 3
    hi = max(x_in.max(), x_wet.max()) + 3
    ax1.plot([lo, hi], [lo, hi], "--", color="0.55", lw=1.2,
             label=L("不压缩（45° 参考线）", "No compression (1:1 reference)"))
    ax1.plot(x_in, x_wet, "-o", color="#1f77b4", lw=2.0, ms=6,
             label=L("压缩后（阈值 0.010、压缩比 0.30）",
                     "Compressed (thr 0.010, ratio 0.30)"))
    ax1.set_xlim(lo, hi)
    ax1.set_ylim(lo, hi)
    ax1.set_aspect("equal")
    ax1.set_xlabel(L("输入电平 (dBFS)", "Input level (dBFS)"))
    ax1.set_ylabel(L("输出电平 (dBFS)", "Output level (dBFS)"))
    ax1.set_title(L("压缩器的输入-输出曲线", "Compressor transfer curve"))
    ax1.grid(alpha=0.3)
    ax1.legend(loc="upper left", fontsize=8.5)
    # 最响那一档压在 45° 线上，标「+0.0」只是噪声，跳过。其余标到一位小数，
    # 和右图柱子上的一致（写 %.0f 会把 8.7 进成 9，两张图对不上）。
    for xi, yi in list(zip(x_in, x_wet))[1:]:
        ax1.annotate("%+.1f dB" % (yi - xi), (xi, yi), textcoords="offset points",
                     xytext=(7, -13), fontsize=7.5, color="#1f77b4")

    if span:
        ax1.text(0.97, 0.06,
                 L("输入跨 %.1f dB →  输出只跨 %.1f dB\n压窄 %.1f dB"
                   % (span[0], span[1], span[0] - span[1]),
                   "Input span %.1f dB ->  output span %.1f dB\n"
                   "compressed by %.1f dB" % (span[0], span[1], span[0] - span[1])),
                 transform=ax1.transAxes, ha="right", va="bottom", fontsize=9,
                 bbox=dict(boxstyle="round,pad=0.4", fc="#fffbe6", ec="0.7"))

    # ---- 右：相对直通被抬了多少 ----
    # 用柱状，因为横轴是六个离散档位，不是连续量；连线会暗示中间有值。
    ax2.bar(range(len(lv)), x_wet - x_dry, color="#1f77b4", width=0.6)
    ax2.axhline(0.0, color="0.4", lw=1.0)
    ax2.set_xticks(range(len(lv)))
    ax2.set_xticklabels(["%+d" % v for v in lv])
    ax2.set_xlabel(L("输入档位 (dB，相对最响档)", "Step (dB rel. loudest)"))
    ax2.set_ylabel(L("相对不压缩抬升 (dB)", "Lift over bypass (dB)"))
    ax2.set_title(L("小声的档位被抬起来多少", "Lift applied to quieter steps"))
    ax2.grid(axis="y", alpha=0.3)
    for i, v in enumerate(x_wet - x_dry):
        ax2.annotate("%+.1f" % v, (i, v), textcoords="offset points",
                     xytext=(0, 3), ha="center", fontsize=8)
    ax2.set_ylim(0, max(x_wet - x_dry) * 1.25 + 0.5)

    fig.tight_layout()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    fig.savefig(args.out, bbox_inches="tight")
    print("已写出：%s" % args.out)
    print("最响档不抬（它是响度基准），越轻抬得越多，到 −24 dB 及以下封顶 %.1f dB。"
          % (x_wet - x_dry)[-1])
    print("下面几档封顶是硬拐点压缩器的正常形状：落到门限以下就一点不动了。")


if __name__ == "__main__":
    main()
