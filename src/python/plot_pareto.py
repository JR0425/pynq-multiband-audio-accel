#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「拍数 / DSP / LUT / 时序」这四个量的取舍画成一张图，供设计报告 §4 使用。

为什么要有这张图：
    `hls_synth_metrics.md` 和 `impl_metrics.md` 里已经有全部数字，但它们是**过程表** ——
    一行一版，读的人要自己在脑子里做加法才能看出「我们为什么停在这一格」。
    指南 §3.3.4 要的是「不以显著的资源代价换取有限的性能收益」这句话的证据，
    而这句话本质上是一句**取舍陈述**，用散点 + 非支配前沿比用表格说得清楚。

数据来源（不复制数字，全部现场解析）：
    * 估算值：build/hls/reports/<版本>/fir_multiband_csynth.rpt（与 parse_hls_csynth 同一套字段）
    * 实测值：data/results/impl_metrics.md 里的 markdown 表（按表头名取列，不按列序号）

用法：
    python src/python/plot_pareto.py                       # 写 data/figures/report/fig4_1_pareto.png
    python src/python/plot_pareto.py -o /tmp/x.png         # 另存

读图要点：
    左图 x 是 DSP、右图 x 是 LUT，两图 y 都是「每采样拍数」。**两个方向都是越小越好**，
    所以左下角是最优区。虚线是非支配前沿：它上面的点没有任何一个能被别的点同时打赢
    （更少的拍 + 更少的资源）。落在前沿上的都是「合理选择」，选哪一个取决于你要什么。
"""

import argparse
import os
import re
import sys

import matplotlib
from matplotlib.lines import Line2D
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_hls_csynth import collect as collect_csynth, CAPACITY  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMPL_MD = os.path.join(REPO, "data", "results", "impl_metrics.md")
DEFAULT_OUT = os.path.join(REPO, "data", "figures", "report", "fig4_1_pareto.png")

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["axes.axisbelow"] = True
plt.rcParams["figure.dpi"] = 160

INK = "#1f2328"
GREY = "#8b949e"
EST = "#c9d1d9"
PASS = "#2f6f4e"      # 时序通过
FAIL = "#a63d40"      # 时序没过
SHIP = "#b7791f"      # 出货点


def _num(cell):
    """从 '3962 (7.5%)' / '+1.624' / '**51.5 (36.8%)**' / '−0.620' 里取第一个数。

    U+2212 那个减号必须先换成 ASCII '-'，否则 '-0.620' 会被读成 0.620 —— 一次
    时序没过被判成过了，图上就是绿的。这条是这次写脚本时踩到的。
    """
    s = cell.replace("*", "").replace("\u2212", "-").replace("\u2013", "-")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def _clean_label(cell):
    """'`v21_wdrc`（49 拍）' -> 'v21_wdrc'；'`v15_...` @ **80 MHz**' -> '...@80MHz'。"""
    raw = cell.replace("*", "")
    lab = re.sub(r"`", "", raw).split("（")[0].split("(")[0].strip()
    lab = re.sub(r"\s*@.*$", "", lab).strip()       # 先剥掉已有的 '@ ...'，免得叠两层
    if "@" in raw:
        m = re.search(r"(\d+)\s*MHz", raw)
        if m:
            lab = "%s@%dMHz" % (lab, int(m.group(1)))
    return lab


def _cells(line):
    return [c.strip().replace("*", "") for c in line.strip().strip("|").split("|")]


def parse_impl(path):
    """解析 impl_metrics.md 里的表，两种排版都认。

    1) 常规表：首行同时含「版本」「DSP」列名，一行一版；
    2) 转置表（v21 vs v25 那张）：首格是「指标」，一列一版。
       出货版 v25 只出现在这张表里，不解析它，图上就没有出货点。
    """
    rows = {}
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    header = None
    for line in lines:
        s = line.strip()
        if not s.startswith("|"):
            header = None
            continue
        cells = _cells(line)
        if set("".join(cells)) <= set("-: "):
            continue

        # --- 转置表：指标 | 版本A | 版本B | 差 ---
        if cells[0] in ("指标", "项") and len(cells) >= 3:
            header = cells
            rows.setdefault("_transposed", []).append({"header": cells, "rows": []})
            continue
        if header and header[0] in ("指标", "项"):
            rows["_transposed"][-1]["rows"].append(cells)
            continue

        # --- 常规表 ---
        if "版本" in cells and "DSP" in cells:
            header = cells
            continue
        if not header or "SEL" in cells:
            continue
        rec = dict(zip(header, cells))
        label = _clean_label(rec.get("版本", ""))
        if not label:
            continue
        row = {"label": label}
        for key, name in (("cycles", "拍/采样"), ("DSP", "DSP"), ("LUT", "LUT"),
                          ("FF", "FF"), ("BRAM", "BRAM"), ("WNS", "WNS")):
            v = rec.get(name)
            row[key] = _num(v) if v else None
        if row.get("cycles") and row.get("DSP") and row.get("LUT"):
            rows.setdefault(label, row)

    # 转置表 → 每版一条。版本名在**表头**里，不在「拍/采样」那一行。
    for block in rows.get("_transposed", []):
        head, body = block["header"], block["rows"]
        by_metric = {c[0].strip("* "): c for c in body if len(c) >= 2}
        cyc = by_metric.get("拍/采样")
        res = None
        for k in by_metric:
            if "BRAM" in k and "LUT" in k:      # 'OOC LUT / FF / DSP / BRAM'
                res = by_metric[k]
                break
        wns = None
        for k in by_metric:
            if "WNS" in k:
                wns = by_metric[k]
                break
        if not (cyc and res and wns):
            continue
        for col in range(1, len(cyc) - 1):       # 最后一列是「差」，跳过
            label = _clean_label(head[col])
            if not label or label == "差":
                continue
            nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", res[col].replace("*", ""))]
            if len(nums) < 4:
                continue
            rec = {"label": label, "cycles": _num(cyc[col]),
                   "LUT": nums[0], "FF": nums[1], "DSP": nums[2], "BRAM": nums[3],
                   "WNS": _num(wns[col])}
            if rec["cycles"] and rec["DSP"] and rec["LUT"]:
                rows.setdefault(label, rec)

    return [v for k, v in rows.items() if k != "_transposed"]


def pareto(points, better=("x", "y")):
    """非支配集：没有任何一个点能在两个维度上同时不差且至少一维更好。"""
    front = []
    for p in points:
        dominated = False
        for q in points:
            if q is p:
                continue
            if q["x"] <= p["x"] and q["y"] <= p["y"] and (q["x"] < p["x"] or q["y"] < p["y"]):
                dominated = True
                break
        if not dominated:
            front.append(p)
    return sorted(front, key=lambda d: d["x"])


def panel(ax, est, impl, xkey, xlabel, cap, title, first=True):
    ax.set_facecolor("#fbfcfd")

    # 装不下/极端的版本会把 x 轴拉到没有信息量的区间，单独排除并注明
    over = [r for r in est if r.get(xkey) and r[xkey] > cap * 1.5]
    est_pts = [(r[xkey], r["cycles_per_sample"]) for r in est
               if r.get(xkey) and r.get("cycles_per_sample") and r[xkey] <= cap * 1.5]
    ax.scatter([p[0] for p in est_pts], [p[1] for p in est_pts],
               s=16, c=EST, edgecolors="#b9c2cb", linewidths=0.4, zorder=2,
               label="csynth 估算" if first else None)
    if over:
        ax.text(0.02, 0.975, "另有 %d 版超出坐标范围，未画出" % len(over),
                transform=ax.transAxes, ha="left", va="top", fontsize=7,
                color=FAIL)

    # '@NNMHz' 的行是换了时钟约束量出来的，拍数含义不同，不能混在 100 MHz 这张图上
    pts = [{"x": r[xkey], "y": r["cycles"], "r": r} for r in impl
           if r.get(xkey) and "@" not in r["label"]]
    for p in pts:
        ok = (p["r"].get("WNS") if p["r"].get("WNS") is not None else 0) >= 0
        ship = "v25" in p["r"]["label"]
        ax.scatter([p["x"]], [p["y"]],
                   s=260 if ship else 66,
                   marker="*" if ship else "o",
                   c=SHIP if ship else (PASS if ok else FAIL),
                   edgecolors="white", linewidths=1.0, zorder=5)

    front = pareto(pts)
    if len(front) >= 2:
        ax.plot([p["x"] for p in front], [p["y"] for p in front],
                ls="--", lw=1.1, color="#6b7a86", zorder=3,
                label="非支配前沿（实测）" if first else None)

    # 只标前沿、出货点、时序没过的点，免得 60 多个标签糊成一团。
    # 每个标签的手工偏移是为了避开同一簇里的邻居 —— 自动摆放会互相压字。
    offs = {
        "v25_env_indep": (11, 7, "left"),
        "v21_wdrc": (9, 8, "left"),
        "v19_shift_chain": (9, -13, "left"),
        "v13_sub_n193": (8, 7, "left"),
        "v18_axi_shell": (-8, 8, "right"),
        "v12_sub_n65": (8, -13, "left"),
        "v14_sub_n193_mul96": (-9, 6, "right"),
        "v15_sub_n193_mul64": (-9, -14, "right"),
        "v15_sub_n193_mul64@80MHz": (8, -13, "left"),
    }
    show = {id(p) for p in front} | {id(p) for p in pts
                                     if "v25" in p["r"]["label"]
                                     or (p["r"].get("WNS") or 0) < 0}
    for p in pts:
        if id(p) in show:
            lab = p["r"]["label"]
            dx, dy, ha = offs.get(lab, (8, -13, "left"))
            # 靠左边缘的点，右对齐的标签会顶出坐标区，翻到右边去
            if ha == "right" and p["x"] < cap * 0.22:
                dx, ha = -dx, "left"
            ax.annotate(lab, (p["x"], p["y"]), textcoords="offset points",
                        xytext=(dx, dy), fontsize=7.4,
                        color=SHIP if "v25" in lab else INK, ha=ha)

    ax.set_xlabel(xlabel, fontsize=9.5, color=INK)
    ax.set_ylabel("每采样拍数（100 MHz 下，越小越快）", fontsize=9.5, color=INK)
    ax.set_yscale("log")
    ax.set_title(title, fontsize=10.5, color=INK, pad=9)
    ax.grid(True, ls=":", lw=0.6, color="#dde3e8")
    ax.tick_params(labelsize=8.5, colors="#4a5258")
    ax.set_xlim(left=0, right=cap * 1.08)
    ax.axvline(cap, ls="-.", lw=0.9, color=FAIL)
    ax.text(cap * 0.995, ax.get_ylim()[1] * 0.55, "板上总量 %d " % cap,
            fontsize=7.2, color=FAIL, rotation=90, va="top", ha="right")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    est = collect_csynth()
    impl = parse_impl(IMPL_MD)
    if not est:
        sys.exit("没解析到 csynth 报告，检查 build/hls/reports/")
    if not impl:
        sys.exit("没解析到 impl_metrics.md 里的实测表")

    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.8))
    panel(axes[0], est, impl, "DSP", "DSP48E1 用量（板上共 220）", CAPACITY["DSP"],
          "左：拍数 — DSP 的取舍")
    panel(axes[1], est, impl, "LUT", "LUT 用量（板上共 53200）", CAPACITY["LUT"],
          "右：拍数 — LUT 的取舍", first=False)

    h, lab = axes[0].get_legend_handles_labels()
    h += [Line2D([], [], marker="o", ls="", ms=7, color=PASS, label="实测 · 时序通过"),
          Line2D([], [], marker="o", ls="", ms=7, color=FAIL, label="实测 · 时序没过"),
          Line2D([], [], marker="*", ls="", ms=13, color=SHIP, label="出货 · v25_env_indep")]
    lab = [x.get_label() for x in h]
    fig.legend(h, lab, loc="lower center", ncol=5, frameon=False, fontsize=8.4,
               bbox_to_anchor=(0.5, 0.005))
    fig.suptitle("HLS 优化轨迹：拍数、DSP、LUT 的取舍全貌", fontsize=12.5, color=INK)
    fig.text(0.5, 0.055,
             "报告目录共 68 版，其中 53 版有完整拍数/资源数据（灰点，csynth 估算，数值一律被高估）；彩色点是布局布线实测。"
             "两个方向都是越小越好，左下角最优；虚线是实测的非支配前沿。",
             ha="center", fontsize=8.2, color=GREY)
    fig.subplots_adjust(top=0.86, bottom=0.16, wspace=0.24)

    out = args.out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    fig.savefig(out[:-4] + ".svg", bbox_inches="tight", facecolor="white")
    print("已写入 %s（+ .svg）" % out)
    print("估算点 %d 个，实测点 %d 个，前沿 %d 点" % (
        len([r for r in est if r.get("cycles_per_sample")]), len(impl),
        len(pareto([{"x": r["DSP"], "y": r["cycles"], "r": r} for r in impl if r.get("DSP")]))))


if __name__ == "__main__":
    main()
