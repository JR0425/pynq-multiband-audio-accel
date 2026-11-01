#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
---
name: csynth-report-table
description: >
  Turn a directory of Vitis HLS synthesis reports into one comparison table
  (cycles per sample, II, DSP, LUT, FF, BRAM) so an optimisation history can be
  quoted without hand-copying numbers out of tool output. Use when a project has
  many HLS builds under one folder and you need the before/after table for a
  report, or when a resource number is disputed and you want to re-derive it from
  the report files instead of from memory.
license: MIT
compatibility: Vitis HLS 2020.2, both the wide csynth.rpt and the per-top-function *_csynth.rpt layout; column counts are matched explicitly and unknown layouts degrade to n/a rather than crashing
metadata:
  version: "1.0.0"
  updated: "2026-11-01"
---

# 把一堆 csynth 报告拉成一张表

## 什么时候用

- 报告里要一张"优化前后的性能与资源对比表"，而每版只有一份 csynth 报告。
- 某个资源数字被质疑，需要当场从报告文件重新抠一遍，而不是引用记忆里的数。
- 一版改完想快速看拍数 / DSP 有没有动。

## 什么时候别用

- **要的是实测。** csynth 是**估算**：LUT 一律被高估约 2.5 倍，DSP 两个方向都不准
  （实测过 6→35 和 200→196）。**资源和时序必须用布局布线后的报告。**
  本脚本只适合比同一流程内的相对趋势，不适合对外报绝对值。
- 想用 csynth 的 Fmax 估算去区分限流方案 —— 实测在这里没有区分度
  （v13~v17 一律报 143.58 MHz，一模一样）。
- 报告被移动过、和源码对不上。先确认目录名与编译开关的对应关系。

## 用法

    python csynth_report_table.py <reports_root>             # 打印 markdown
    python csynth_report_table.py <reports_root> -o out.md   # 同时写文件

`reports_root` 下应是"一版一个子目录"，每个子目录里有 `csynth.rpt`
和/或 `<顶层函数名>_csynth.rpt`。**优先读后者** —— 它是同一份数据的精简版，
表格列数固定，比宽表好解析。

## 它抠哪几个数，以及为什么

| 列 | 从哪来 | 为什么重要 |
|---|---|---|
| 拍/采样 | **顶层**循环的 iteration latency | 越小越快。顶层 = 每采样跑一遍的那个 |
| II | 同一行的 achieved II | 没到目标 II 时最该看的就是它 |
| DSP | Utilization Estimates 的 Total 行 | 板上最稀缺的资源，通常最先撞上限 |
| LUT / FF / BRAM | 同上 | 决定还能不能再塞功能 |

⚠️ **顶层循环的识别是最容易错的一步。** 报告里 `- VITIS_LOOP_x_y`（减号/宽表里前缀
空格更少）是外层，`+ VITIS_LOOP_x_y` 是它里面那一圈。混用会把"合段循环 69 拍"
当成"整核 69 拍"，两个数差一倍 —— 本项目真出过这个错。
本脚本的判据：**未流水化、且行程数是 `?` 或 `-`（运行时才知道）的那一行**；
命中多条时取 iteration latency 最大的那条。
"""

import argparse
import os
import re
import sys

RE_NUM_PCT = re.compile(r"^(\d+)\s*(?:\(\s*\d+%\s*\))?$")


def cells_of(line):
    s = line.strip()
    if not s.startswith("|"):
        return None
    return [c.strip() for c in s.strip("|").split("|")]


def pick_report(vdir):
    """优先精简版 <fn>_csynth.rpt，退回宽表 csynth.rpt。"""
    cand = None
    for fn in sorted(os.listdir(vdir)):
        if fn.lower().endswith("_csynth.rpt"):
            return os.path.join(vdir, fn)
        if fn.lower() == "csynth.rpt":
            cand = os.path.join(vdir, fn)
    return cand


def parse_loops(text):
    """返回 (cycles_per_sample, ii_achieved, outer_loop_name)。

    两种排版：
      精简 8 列  | name | min | max | itlat | achieved | target | trip | pipelined |
      宽表 14 列 | name | type | slack | lat_cyc | lat_ns | itlat | ii | trip | pipelined | ...
    """
    best = None
    for line in text.splitlines():
        c = cells_of(line)
        if not c or "VITIS_LOOP" not in c[0]:
            continue
        try:
            if len(c) == 8:
                name, itlat, ii = c[0], c[3], c[4]
                trip, pipe = c[6], c[7]
            elif len(c) >= 14:
                name, itlat, ii = c[0], c[5], c[6]
                trip, pipe = c[7], c[8]
            else:
                continue
        except IndexError:
            continue
        if pipe.lower() != "no":
            continue
        if trip not in ("?", "-"):
            continue
        try:
            val = int(itlat)
        except ValueError:
            continue
        if best is None or val > best[0]:
            best = (val, ii, name.strip())

    if best is None:
        return None, None, None
    ii = best[1]
    try:
        ii = int(ii)
    except (TypeError, ValueError):
        ii = None
    return best[0], ii, best[2]


def parse_util(text):
    """返回 {BRAM, DSP, FF, LUT}。两种排版各一条路。"""
    out = {}

    # 精简版：|Total | BRAM_18K | DSP | FF | LUT | URAM |
    for line in text.splitlines():
        c = cells_of(line)
        if not c or len(c) < 5:
            continue
        if c[0].replace(" ", "") != "Total":
            continue
        vals = []
        for x in c[1:5]:
            m = RE_NUM_PCT.match(x)
            if not m:
                vals = []
                break
            vals.append(int(m.group(1)))
        if len(vals) == 4:
            out["BRAM"], out["DSP"], out["FF"], out["LUT"] = vals
            return out

    # 宽表：模块行 |+ fir_xxx | Timing | slack | ... | BRAM(n%) | DSP(n%) | FF(n%) | LUT(n%) | URAM |
    for line in text.splitlines():
        c = cells_of(line)
        if not c or len(c) < 14:
            continue
        if not c[0].startswith("+"):
            continue
        try:
            out["BRAM"] = int(RE_NUM_PCT.match(c[9]).group(1))
            out["DSP"] = int(RE_NUM_PCT.match(c[10]).group(1))
            out["FF"] = int(RE_NUM_PCT.match(c[11]).group(1))
            out["LUT"] = int(RE_NUM_PCT.match(c[12]).group(1))
        except (AttributeError, ValueError):
            out = {}
            continue
        return out

    return out


def scan(root):
    rows = []
    for entry in sorted(os.listdir(root)):
        vdir = os.path.join(root, entry)
        if not os.path.isdir(vdir):
            continue
        rpt = pick_report(vdir)
        if not rpt:
            continue
        with open(rpt, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        cyc, ii, loop = parse_loops(text)
        u = parse_util(text)
        rows.append(
            {
                "version": entry,
                "cycles": cyc,
                "ii": ii,
                "loop": loop,
                "dsp": u.get("DSP"),
                "lut": u.get("LUT"),
                "ff": u.get("FF"),
                "bram": u.get("BRAM"),
                "report": os.path.relpath(rpt, root).replace("\\", "/"),
            }
        )
    return rows


def render(rows, dsp_total, lut_total):
    out = ["| 版本 | 拍/采样 | II | DSP | LUT | FF | BRAM | 报告 |",
           "|---|---|---|---|---|---|---|---|"]
    base = None
    for r in rows:
        if r["version"].startswith("v0") and r["cycles"]:
            base = r["cycles"]
    for r in rows:
        if r["cycles"] is None:
            cyc = "n/a"
        elif base and base != r["cycles"]:
            cyc = "%d（基线 %.1f×）" % (r["cycles"], base / r["cycles"])
        else:
            cyc = str(r["cycles"])

        dsp = r["dsp"]
        if dsp is not None and dsp_total:
            dsp = "%d（%.1f%%）" % (dsp, 100.0 * dsp / dsp_total)
        lut = r["lut"]
        if lut is not None and lut_total:
            lut = "%d（%.1f%%）" % (lut, 100.0 * lut / lut_total)

        cells = [
            "`%s`" % r["version"], cyc,
            r["ii"] if r["ii"] is not None else "-",
            dsp if dsp is not None else "n/a",
            lut if lut is not None else "n/a",
            r["ff"] if r["ff"] is not None else "n/a",
            r["bram"] if r["bram"] is not None else "n/a",
            r["report"],
        ]
        out.append("| " + " | ".join(str(c) for c in cells) + " |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="csynth 报告 -> 对比表")
    ap.add_argument("root", help="各版报告所在的根目录（一版一个子目录）")
    ap.add_argument("-o", "--out", help="同时写入这个文件")
    ap.add_argument("--dsp-total", type=int, default=220, help="器件 DSP 总数，用于算占比")
    ap.add_argument("--lut-total", type=int, default=53200, help="器件 LUT 总数")
    args = ap.parse_args()

    if not os.path.isdir(args.root):
        print("不是目录: %s" % args.root)
        return 2

    rows = scan(args.root)
    if not rows:
        print("没找到任何 csynth 报告。检查目录层级：root/<版本>/csynth.rpt")
        return 1

    md = render(rows, args.dsp_total, args.lut_total)
    print(md)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md + "\n")
        print("\n已写入 %s" % args.out)

    bad = [r["version"] for r in rows if r["cycles"] is None or r["dsp"] is None]
    if bad:
        print(
            "\n⚠️ %d 个版本有字段没抠到，需人工核：%s" % (len(bad), ", ".join(bad)),
            file=sys.stderr,
        )
    print(
        "\n提醒：这是 csynth 估算。对外报资源与时序必须用布局布线后的报告。",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
