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
            "`v4_ref_limit5` / `v4_fold_*` 这一组量的是「折叠结构」（利用系数对称性"
            "把乘法次数减半，编译开关 `-DFIR_FOLD=1`）。结论是**不划算**：同为乘法器"
            "上限 5，直算 327 拍，折叠 1247 拍；把 hist 完全拆开能回到 663 拍，"
            "面积却翻到 79 DSP。原因是这条线的瓶颈在浮点加法的延迟链，不在乘法 —— "
            "折叠减掉的是不紧张的乘法，加的却是紧张的加法。详见 "
            "`src/hls/fir_multiband.cpp` 里 `FIR_FOLD` 那一段注释。\n\n"
            "`v5*` ~ `v11*` 这一组量的是「定点化」（`-DFIR_FIXED=1`，"
            "采样 16 位 × 系数 18 位）。**结论要分设计点说**，不能一句话说完：\n\n"
            "    限流 5 个乘法器：浮点 327 拍 / 27 DSP /  9936 LUT，"
            "定点 253 拍 / 8 DSP / 19130 LUT\n"
            "    不限流（全并行）：浮点 274 拍 / 345 DSP / 52953 LUT，"
            "定点 185 拍 / 67 DSP / 17552 LUT\n\n"
            "第一次只量了「限流 5」那一格就下结论说「定点更费 LUT」，"
            "**那个归因是错的**。把表达式层那 16730 LUT 按运算类型拆开，"
            "43%（7024 LUT）是 `select` —— 也就是饱和逻辑；"
            "而累加器 `ap_fixed<40,8>`（Q8.32）有 256 倍余量，那些选择器一次都不会触发。\n\n"
            "加上 `FIR_ACC_SAT=0`（只把累加器的 `AP_SAT` 换成 `AP_WRAP`）之后：\n\n"
            "    限流 5： 252 拍 /  6 DSP / 8714 LUT\n"
            "    不限流： 173 拍 / 67 DSP / 6502 LUT\n\n"
            "**两个设计点上都变成定点胜**，且 csim 输出与饱和版逐位相同。"
            "详见 `skill/pitfalls/hls_fixed_point_types.md`。\n\n"
            "`v8_float_nolimit` / `v9_fixed_nolimit` 是为对照补的「不限流」两格："
            "GitHub 上 `Nunigan/FIR-FIlter_HLS` 那组 float/fixed 报告也是全并行不限流，"
            "结论是定点全面更省，补这两格是为了确认和它可比。\n\n"
            "`v5_fixed_naive` 是**故意留着的反例**：系数表写成 `const float`、"
            "在循环里现转定点，HLS 不折这个转换，生成 65 个 float→double 扩位器，"
            "LUT 炸到 123550（232.2%，装不下）。修正成「表本身就是定点类型」之后"
            "降到 28309。\n\n"
            "`v12*` ~ `v17*` 这一组量的是**相减式频段划分**：源码从「4 段各自设计"
            "一个带通」改成「3 个低通 + 两两相减」，接口从 float 改成 int16/Q1.15，"
            "抽头数变成编译开关 `-DFIR_N_TAPS`。\n\n"
            "先看 65 抽头那一格：**换结构是零成本、纯赚**。`v12_sub_n65` 与 "
            "`v10_fixed_nosat` 同为 173 拍、同为 67 DSP，而 LUT 从 6502 降到 4818、"
            "FF 从 6071 降到 5687 —— 滤波器少了一个，速度一点没掉。\n\n"
            "把抽头数加到 193（`v13_sub_n193`）段边缘残留从 −6 dB 改善到 −21 dB，"
            "代价是 197 个 DSP，占芯片 220 个的 **89%**，太多。但从这里扫出一条"
            "反直觉的曲线 —— 限流（`set_directive_allocation -limit N -type operation "
            "fir_multiband mul`）：\n\n"
            "    不限（v13）   197 DSP (89%)   9246 LUT  194 BRAM  449 拍\n"
            "    限  96（v14） 97 DSP (44%)  19186 LUT   64 BRAM  452 拍\n"
            "    限  64（v15） 65 DSP (29%)  19041 LUT   48 BRAM  455 拍\n"
            "    限  32（v16） 33 DSP (15%)  18095 LUT   27 BRAM  464 拍\n"
            "    限  16（v17） 17 DSP  (7%)  17112 LUT   14 BRAM  482 拍\n\n"
            "**乘法器从 195 个收到 16 个，拍数只从 449 涨到 482（+7%），DSP 从 197 掉到 17。**"
            "也就是说在 193 抽头定点版上，乘法器数量**根本不是瓶颈** —— 抽头循环卡在 "
            "II=2（`hist` 被实现成 RAM，读口不够），复用乘法器几乎不要钱。\n"
            "这正好说明 `v4_fold_*` 那组「折叠不划算」的结论**有前提**：前提是瓶颈在"
            "浮点加法的延迟链。换成定点、规模换到 193 之后前提变了，就得重测 ——"
            "这一组就是重测的结果。\n\n"
            "**上表里限流那几格的拍数和 DSP 都不能直接采信，现在有实测了。**"
            "把 v13/v14/v15 送进 Vivado 走完综合 + 布局布线（`-mode out_of_context`）：\n\n"
            "                                csynth 估计          实测\n"
            "    v13 不限流                197 DSP / 9246 LUT   202 DSP /  3962 LUT / WNS +1.624 ✅\n"
            "    v14 限 96                  97 DSP / 19186 LUT  132 DSP /  9702 LUT / WNS −0.138 ❌\n"
            "    v15 限 64                  65 DSP / 19041 LUT   86 DSP / 10443 LUT / WNS −0.620 ❌\n\n"
            "结论是**限流在 193 抽头定点版上不划算**：DSP 确实省了（202 → 86），"
            "但 LUT 反而涨了 2.6 倍，时序还从过得去变成过不去。原因和 `v11_fixed_nosat_mul5` 那次"
            "一样 —— 复用把两个 DSP48 串成一条中间没有寄存器的链（最差路径 10.491 ns，"
            "2 个 DSP48E1 直连）。而不限流那一版的最差路径根本不在算术上，落在 `hist` 的地址加法上。\n\n"
            "**csynth 的 Fmax 估算在这里没有任何区分度**（v13~v17 一律报 143.58 MHz，一模一样），"
            "所以限流版只能靠布局布线回答。实测过程见 `data/results/impl_metrics.md`。\n\n"
            "**以上全部是 `csynth_design` 的估算值，不是布局布线后的实测值。**"
            "实测值见 `data/results/impl_metrics.md`。两者差得不小：LUT 一律被高估约 2.5 倍，"
            "DSP 则两个方向都不准（`v11_fixed_nosat_mul5` 估 6 实测 35，"
            "`v13_sub_n193` 估 197 实测 202，`v15_sub_n193_mul64` 估 65 实测 86）。"
            "所以**估算值不能当结果**。\n\n"
        )
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(header + table)
        print("已写入 %s" % args.out)


if __name__ == "__main__":
    main()
