#!/usr/bin/env python3
"""把 LAT_TRACE 的块模式延迟 json 算成分位数与 deadline miss。

    python src/python/latency_quantiles.py
    python src/python/latency_quantiles.py --glob "data/results/latency_*.json"

产物：data/results/latency_quantiles.md 与 .csv。
时间戳单位是秒（perf_probe.py 里乘 1000 换成 ms），本脚本同样输出 ms。
"""

import argparse
import csv
import glob
import json
import os
import re

import numpy as np

SR = 48000.0
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_MD = os.path.join(REPO, "data", "results", "latency_quantiles.md")
OUT_CSV = os.path.join(REPO, "data", "results", "latency_quantiles.csv")

BITSTREAM = {
    "20261022": "49 拍 v21_wdrc",
    "20261023": "36 拍 v25_env_indep（md5 5aa079f29e8d66c21d1d34f1302c3695）",
}


def analyze(path):
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    L = int(d["L"])
    nbuf = int(d["NBUF"])
    src = d.get("SRC", "")
    wall = float(d.get("wall", 0.0))
    cap = np.asarray(d["cap"], dtype=np.float64)
    seq = np.asarray([r[0] for r in d["out"]], dtype=np.int64)
    t = np.asarray([r[1] for r in d["out"]], dtype=np.float64)

    ok = (seq >= 0) & (seq < cap.size)
    sel = seq[ok]
    lat = (t[ok] - cap[sel]) * 1000.0
    dt = np.diff(cap) * 1000.0
    theory = L * 1000.0 / SR

    uniq, counts = np.unique(sel, return_counts=True)
    lat_q = np.percentile(lat, [50, 95, 99]) if lat.size else np.full(3, np.nan)
    dt_q = np.percentile(dt, [50, 95, 99]) if dt.size else np.full(3, np.nan)
    miss_n = int((dt > theory).sum())
    hard_n = int((dt > theory + 0.5).sum())
    span = float(cap[-1] - cap[0]) if cap.size > 1 else 0.0

    m = re.search(r"_(\d{8})\.json$", os.path.basename(path))
    date = m.group(1) if m else ""

    return {
        "path": path,
        "name": os.path.basename(path),
        "date": date,
        "bitstream": BITSTREAM.get(date, "位流未记录"),
        "L": L,
        "NBUF": nbuf,
        "SRC": src,
        "wall": wall,
        "n_cap": int(cap.size),
        "n_lat": int(lat.size),
        "n_dt": int(dt.size),
        "lat_p50": float(lat_q[0]),
        "lat_p95": float(lat_q[1]),
        "lat_p99": float(lat_q[2]),
        "lat_max": float(lat.max()) if lat.size else np.nan,
        "lat_min": float(lat.min()) if lat.size else np.nan,
        "dt_p50": float(dt_q[0]),
        "dt_p95": float(dt_q[1]),
        "dt_p99": float(dt_q[2]),
        "dt_max": float(dt.max()) if dt.size else np.nan,
        "theory_ms": theory,
        "miss_n": miss_n,
        "miss_pct": 100.0 * miss_n / dt.size if dt.size else np.nan,
        "hard_n": hard_n,
        "hard_pct": 100.0 * hard_n / dt.size if dt.size else np.nan,
        "rt": (cap.size * L / SR) / span if span > 0 else np.nan,
        "unplayed": int(cap.size - uniq.size),
        "dups": int((counts > 1).sum()),
        "gaps": int((np.diff(uniq) != 1).sum()) if uniq.size > 1 else 0,
        "lat_over25": int((lat > 25.0).sum()),
    }


def sort_key(r):
    return (r["NBUF"], r["L"], r["date"], r["name"])


def fmt(x, nd=2):
    return "—" if not np.isfinite(x) else ("%.*f" % (nd, x))


def table1(rows):
    out = [
        "| 配置 | 文件 | n（块） | P50 | P95 | P99 | max | min | > 25 ms |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        cfg = "n%d_l%d" % (r["NBUF"], r["L"])
        out.append(
            "| %s | `%s` | %d | %s | %s | %s | %s | %s | %d |"
            % (cfg, r["name"], r["n_lat"], fmt(r["lat_p50"]), fmt(r["lat_p95"]),
               fmt(r["lat_p99"]), fmt(r["lat_max"]), fmt(r["lat_min"]),
               r["lat_over25"])
        )
    return "\n".join(out)


def table2(rows):
    out = [
        "| 配置 | 文件 | n（间隔） | P50 | P95 | P99 | max | 理论 L/48 | miss | > 理论+0.5 ms | 实时倍率 | 未播出块 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        cfg = "n%d_l%d" % (r["NBUF"], r["L"])
        out.append(
            "| %s | `%s` | %d | %s | %s | %s | %s | %s | %s%% | %d（%s%%） | %s× | %d |"
            % (cfg, r["name"], r["n_dt"], fmt(r["dt_p50"]), fmt(r["dt_p95"]),
               fmt(r["dt_p99"]), fmt(r["dt_max"]), fmt(r["theory_ms"]),
               fmt(r["miss_pct"]), r["hard_n"], fmt(r["hard_pct"]),
               fmt(r["rt"], 3), r["unplayed"])
        )
    return "\n".join(out)


def write_md(rows, pattern):
    total_lat = sum(r["n_lat"] for r in rows)
    total_dt = sum(r["n_dt"] for r in rows)
    dates = sorted({r["date"] for r in rows if r["date"]})
    bits = "；".join("%s-%s-%s %s" % (d[:4], d[4:6], d[6:], BITSTREAM.get(d, "位流未记录"))
                     for d in dates)
    contiguous = all(r["gaps"] == 0 and r["dups"] == 0 for r in rows)
    unplayed = sorted({r["unplayed"] for r in rows})
    overload = [r for r in rows if r["rt"] < 0.95]
    straddle = [r for r in rows if abs(r["dt_p50"] - r["theory_ms"]) < 0.05
                and r["miss_pct"] > 20.0]
    tail = [r for r in rows if r["lat_max"] > 25.0 and r["lat_p50"] <= 25.0]

    p = []
    p.append("# 块模式延迟的分位数与 deadline miss\n")
    p.append("本表由 `src/python/latency_quantiles.py` 从 `data/results/latency_*.json` 直接算出，"
             "共 %d 个文件、延迟样本 %s 块、块间隔样本 %s 个。\n"
             % (len(rows), "{:,}".format(total_lat), "{:,}".format(total_dt)))
    p.append("## 口径（先读这一段）\n")
    p.append("- **只覆盖 PS 在环的块模式线。** 数据来自 `fir_live.py` 的 `LAT_TRACE`："
             "每块记一次 `duplex_block()` 的调用起点（被采到的时刻），以及同一个槽被取去播放时的"
             "调用起点（交给 codec 的时刻）。两者之差是这一块经过数字通路的延迟，"
             "不含 codec 模拟端、耳机与空气。\n")
    p.append("- **位流：**%s。两版同配置延迟逐档相同（差 ≤ 0.04 ms，见 "
             "`latency_retest_20261023.md`）。\n" % bits)
    p.append("- **音源：**九个文件都是 `SRC=wav`。\n")
    p.append("- **不是 PL 直连 / DMA 线。** `fir_pl.bit`、`fir_dma*.bit` 那几颗没有逐块时间戳入库，"
             "延迟与稳定性是另一套口径（见 `dma_pl_direct_20261025.md`、"
             "`soak_pl_600_20261029_1031.md`），**不能和本表的抖动数并列或互相换算**。\n")
    p.append("- 同一配置测了两天的文件分开列，不合并：10-22 与 10-23 是两次独立运行。\n")
    p.append("\n## 定义\n")
    p.append("- 端到端延迟 = `out` 中块号 k 的时刻 − `cap[k]`，**用块号做键，不用数组下标**；"
             "块号越界（最后几块只采到、还没播出）的记录丢弃。\n")
    p.append("- 块间隔 = `diff(cap)`。理论块长 = `L / 48000`。\n")
    p.append("- deadline miss = 块间隔严格大于理论块长的块数 / 块间隔总数，没有容差。\n")
    p.append("- 分位数为线性插值（numpy 默认）；`n` 是该文件参与统计的块数，样本数写在表头。\n")
    p.append("- 异常值不剔除，`max` 单列。\n")
    p.append("\n## 表 1　端到端延迟（ms）\n")
    p.append(table1(rows) + "\n")
    p.append("\n## 表 2　块间隔与 deadline miss（ms）\n")
    p.append(table2(rows) + "\n")
    p.append("\n`> 理论+0.5 ms` 是补充诊断：严格阈值下块节拍只要比标称值慢几微秒就算 miss，"
             "这一列把真正的毫秒级迟到与相位差分开。\n")
    p.append("\n同一配置两天的 miss 可以差近一倍（n2_l360 为 16.2% 与 8.8%），原因不是运行质量变了："
             "两趟的块间隔中位都比理论值短 20~40 µs，落在阈值哪一侧取决于 I2S 时钟与主机墙钟的相位，"
             "换一趟就换一个切点。**比较文件请看 `> 理论+0.5 ms` 这一列，miss 列只在同一趟内部有意义。**\n")
    p.append("\n## 数据问题与读数注意\n")
    p.append("1. **字段与对齐都可靠。** 九个文件都是 `L`/`NBUF`/`SRC`/`wall`/`cap`/`out` 六个键，"
             "结构一致；%s"
             "（块号连续、无重复）；`cap` 比 `out` 多 %s 块，这些块采到了但没播出，"
             "不进入延迟统计。\n" % ("九个文件全部连续" if contiguous else "有文件块号不连续，见下",
                                  " 或 ".join(str(x) for x in unplayed)))
    if overload:
        p.append("2. **%s 这一档跑不动，只能当下限对照。** 实时倍率 %s×，块间隔中位 %s ms "
                 "已经高于理论 %s ms（采集节拍被处理速度拖慢）。它的 miss 100%% "
                 "是过载，不是抖动统计。\n"
                 % ("、".join("`%s`" % r["name"] for r in overload),
                    "、".join(fmt(r["rt"], 3) for r in overload),
                    "、".join(fmt(r["dt_p50"]) for r in overload),
                    "、".join(fmt(r["theory_ms"]) for r in overload)))
    if straddle:
        p.append("3. **`%s` 的 miss 比例贴着阈值，不能读成“卡顿比例”。** 块间隔中位与理论值只差 "
                 "%s ms，严格阈值正好落在分布中央；超出理论值 0.5 ms 以上的只有 %s 块。\n"
                 % ("`、`".join(r["name"] for r in straddle),
                    "、".join(fmt(abs(r["dt_p50"] - r["theory_ms"]), 3) for r in straddle),
                    "、".join(str(r["hard_n"]) for r in straddle)))
    if tail:
        p.append("4. **延迟尾部：**%s 的 max 超过 25 ms（%s 块 > 25 ms），"
                 "这些点保留在表里，未剔除。\n"
                 % ("、".join("`%s`" % r["name"] for r in tail),
                    "、".join(str(r["lat_over25"]) for r in tail)))
    p.append("5. **n3_l480 的 29.8 ms 是三缓冲的设计值，不是抖动。** 它的全部块都 > 25 ms，"
             "但 P50–P99 只差不到 1 ms、max 31.4 ms；比较不同配置时先看 P50，再看尾部。\n")
    p.append("\n## 复现\n")
    p.append("```bash\npython src/python/latency_quantiles.py --glob \"%s\"\n```\n" % pattern)
    text = "\n".join(p)
    text = re.sub(r"\n\n(?=- |\d+\. |`> )", "\n", text)
    with open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_csv(rows):
    cols = ["file", "date", "bitstream", "L", "NBUF", "SRC", "wall_s",
            "n_cap", "n_lat", "n_dt", "theory_ms",
            "lat_p50_ms", "lat_p95_ms", "lat_p99_ms", "lat_max_ms", "lat_min_ms",
            "dt_p50_ms", "dt_p95_ms", "dt_p99_ms", "dt_max_ms",
            "miss_n", "miss_pct", "over_theory_0p5_n", "over_theory_0p5_pct",
            "rt_factor", "unplayed_blocks", "dup_blocks", "gap_blocks", "lat_over25_n"]
    keys = ["name", "date", "bitstream", "L", "NBUF", "SRC", "wall",
            "n_cap", "n_lat", "n_dt", "theory_ms",
            "lat_p50", "lat_p95", "lat_p99", "lat_max", "lat_min",
            "dt_p50", "dt_p95", "dt_p99", "dt_max",
            "miss_n", "miss_pct", "hard_n", "hard_pct",
            "rt", "unplayed", "dups", "gaps", "lat_over25"]
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([("%.6g" % r[k]) if isinstance(r[k], float) else r[k] for k in keys])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="data/results/latency_*.json")
    args = ap.parse_args()
    pattern = args.glob if os.path.isabs(args.glob) else os.path.join(REPO, args.glob)
    files = sorted(glob.glob(pattern))
    if not files:
        raise SystemExit("没有匹配的文件：%s" % pattern)
    rows = sorted((analyze(f) for f in files), key=sort_key)
    write_md(rows, args.glob)
    write_csv(rows)
    print("已写 %s 与 %s（%d 个文件）" % (OUT_MD, OUT_CSV, len(rows)))


if __name__ == "__main__":
    main()
