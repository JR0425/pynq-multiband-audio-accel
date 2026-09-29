#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 build/hls/reports/<版本>/ 下每一版的综合报告，拉成一张对比表。

为什么要有这个脚本：
    「性能与资源优化」这一项要的是**优化前后的对比**，不是最后那一个数。
    每跑一版 HLS 都会在 build/hls/reports/<版本>/ 下留一份 csynth 报告，
    但它是给工具看的排版，数字散在好几张表里，人工抄既慢又容易抄错。
    这个脚本负责把同一组字段从每一版里抠出来，排成一张能直接贴进报告的表。

用法：
    python src/python/parse_hls_csynth.py                # 打印到屏幕
    python src/python/parse_hls_csynth.py -o <输出文件>   # 同时写文件

每个版本取四个数：
    拍/采样   = 顶层循环（每个采样跑一遍的那个）的迭代延迟。**越小越快。**
    DSP       = 用了几个硬件乘法器。板上总共 220 个，是最稀缺的资源。
    LUT       = 用了多少可编程逻辑格子。板上总共 53200 个。
    FF        = 用了多少寄存器。板上总共 106400 个。
"""

import argparse
import os
import re
import sys

# 仓库根目录 = 本文件的上上级
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORT_ROOT = os.path.join(REPO, "build", "hls", "reports")

# 板上 xc7z020 的实际总量，用来算占用百分比
CAPACITY = {"BRAM": 280, "DSP": 220, "FF": 106400, "LUT": 53200}


def parse_report(path):
    """从一份 fir_multiband_csynth.rpt 里抠出需要的数。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    out = {}

    # --- 时序：Estimated 那一列就是综合估算出来的时钟周期 ---
    m = re.search(r"\|\s*ap_clk\s*\|\s*([\d.]+)\s*ns\s*\|\s*([\d.]+)\s*ns\s*\|", text)
    if m:
        out["period_ns"] = float(m.group(2))
        out["fmax_mhz"] = round(1000.0 / out["period_ns"], 1)

    # --- 顶层循环（每个采样跑一遍的那个）的迭代延迟 = 拍/采样 ---
    # 它的特征很好认：行程数是个问号（采样个数是运行时才知道的），而且没有流水化。
    out["cycles_per_sample"] = None
    for line in text.splitlines():
        # 表格行前面有缩进，所以先 strip 再判断
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        # 列：Loop Name | Latency min | max | Iteration Latency | II achieved | II target | Trip Count | Pipelined
        if len(cells) != 8:
            continue
        name = cells[0]
        if "VITIS_LOOP" not in name:
            continue
        if cells[6] == "?" and cells[7] == "no":
            try:
                out["cycles_per_sample"] = int(cells[3])
                out["outer_loop"] = name
            except ValueError:
                pass

    # --- 资源占用：Summary 表里 Total 那一行 ---
    m = re.search(
        r"\|\s*Total\s*\|\s*(\d+)\|\s*(\d+)\|\s*(\d+)\|\s*(\d+)\|", text)
    if m:
        out["BRAM"], out["DSP"], out["FF"], out["LUT"] = (int(g) for g in m.groups())

    return out


def collect():
    if not os.path.isdir(REPORT_ROOT):
        sys.exit("找不到报告目录：%s" % REPORT_ROOT)

    rows = []
    for name in sorted(os.listdir(REPORT_ROOT)):
        rpt = os.path.join(REPORT_ROOT, name, "fir_multiband_csynth.rpt")
        if not os.path.isfile(rpt):
            continue
        m = parse_report(rpt)
        m["label"] = name
        rows.append(m)
    return rows


def render(rows):
    head = ("| 版本 | 拍/采样 | 相对基线 | DSP | LUT | FF | BRAM | 最快能跑 |\n"
            "|---|---|---|---|---|---|---|---|\n")
    base = None
    for r in rows:
        if r["label"].startswith("v0"):
            base = r.get("cycles_per_sample")

    lines = []
    for r in rows:
        c = r.get("cycles_per_sample")
        if c is None:
            c_txt, rel = "?", "-"
        else:
            c_txt = str(c)
            rel = ("%.2fx" % (base / c)) if base else "-"
        lines.append("| `%s` | %s | %s | %s | %s (%s%%) | %s | %s | %s MHz |" % (
            r["label"], c_txt, rel,
            r.get("DSP", "?"),
            r.get("LUT", "?"), round(100.0 * r.get("LUT", 0) / CAPACITY["LUT"], 1),
            r.get("FF", "?"),
            r.get("BRAM", "?"),
            r.get("fmax_mhz", "?"),
        ))
    return head + "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", help="同时写一份 markdown 到该路径")
    args = ap.parse_args()

    rows = collect()
    table = render(rows)
    print(table)

    if args.out:
        header = (
            "# HLS 综合结果对比\n\n"
            "由 `src/python/parse_hls_csynth.py` 从 `build/hls/reports/<版本>/` 自动生成，"
            "不要手改 —— 改了下次重跑就被覆盖。\n\n"
            "每一版对应 `build/hls/directives/` 下的同名指令文件，"
            "外加一组编译开关（见各指令文件开头）。完整的复现命令见 "
            "`build/hls/run_synth.tcl` 的文件头。\n\n"
            "⚠️ `v0_baseline` / `v1_partition` 以及 `t1_*` / `e1_*` / `e2_*` 这几版"
            "是在内核源码重构**之前**测的，对应当时那份源码。"
            "它们的用处是记录「哪条路走不通」，不是可直接重跑的当前版本。\n\n"
        )
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(header + table)
        print("已写入 %s" % args.out)


if __name__ == "__main__":
    main()
