#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把"定点核的输出"和 float64 黄金参考比，量化成信噪比，排成一张表。

为什么用信噪比（SNR）而不是"误差小于多少"：
    音频这一行的行话是 SNR（dB）。而且它和位宽是线性关系 ——
    每多 1 位约 +6 dB。所以一张"位宽 vs SNR"的表，一眼就能看出
    "要 60 dB 得给多少位"，比一串绝对误差好判断得多。

    参考基准：data/results/python_golden.txt（float64 的同一套算法）。
    核本身是 float32，与它差 1.5e-07 —— 远小于 16 位定点的量化台阶（3e-05），
    所以拿 float64 当基准来量定点误差是够用的。

用法（仓库根目录）：
    python src/python/quantization_sweep.py                    # 扫 data/results/fixed_*.txt
    python src/python/quantization_sweep.py <文件> [<文件>...]  # 指定文件

文件名里要能看出位宽，格式 fixed_dw<采样位>_cw<系数位>.txt ——
这个命名是 build/hls/run_csim.tcl 那条扫描命令定的。

⚠️ 2026-09-30：**现在只有「系数位」这一维是真能扫的，采样位被接口钉死在 16。**
    核的接口换成 int16 / Q1.15 之后，`samp_in()`（src/hls/fir_types.h）是
    **按比特重解释**，不再做数值换算 —— 它把那 16 个比特直接当成 data_t 的比特域。
    这在 FIR_DW=16 时是对的（Q1.15 ↔ Q1.15），dw 一改就是把小数点位置挪了位置。
    更关键的是 `src/hls/fir_multiband.cpp` 里 DRC 那两个参数是
    `thr[i] = (drc_t)samp_in(drc_thr[i]);` —— 阈值和压缩比走的也是这条比特重解释，
    同样只在 dw=16 时成立。

    实测症状很有迷惑性：dw=20/24 时**直通检验仍然完全通过**（错配 0 个，
    因为旁路不走 DRC），输出幅度也对（与黄金参考的中位比值 1.000），
    但 SNR 掉到 14 dB、相关系数 0.99 —— 看着像"位宽不够"，
    其实是 DRC 的阈值被按错了刻度。**dw≠16 量到的是刻度错，不是量化误差。**

    所以扫描只用 dw=16 那一列，变 cw。真要让采样位可扫，
    得先把那两处换算改成按 dw 定标（除以 2^(DW-1) 而不是重解释比特）。
    现在不做 —— 板载音频链路本来就是 16 位，没有别的 dw 需要支持。
"""

import os
import re
import sys

import numpy as np

from band_design import BAND_EDGES, FS, NYQ

GOLDEN = "data/results/python_golden.txt"
FIXED_GLOB = re.compile(r"fixed_dw(\d+)_cw(\d+)\.txt$")

# 分频段看 SNR 用的频段 —— 边界从 band_design 取，别再抄一份。
# 带外那一段（8k 以上）是留着的：它能看出"误差有没有被搬到听不见的地方"。
BANDS = ([(lo, hi, f"{i + 1}: {lo}-{hi}") for i, (lo, hi) in
          enumerate(zip([0] + BAND_EDGES, BAND_EDGES + [8000]))]
         + [(8000, NYQ, "带外 8k+")])


def snr_db(ref, test):
    """信噪比：把误差当成噪声，参考信号当成信号。单位 dB，越大越好。"""
    err = test - ref
    p_sig = float(np.sum(ref.astype(np.float64) ** 2))
    p_err = float(np.sum(err.astype(np.float64) ** 2))
    if p_err == 0.0:
        return float("inf")
    return 10.0 * np.log10(p_sig / p_err)


def per_band_snr(ref, test):
    """分频段看信噪比 —— 用来回答"误差是均匀的，还是某一频段特别差"。

    下面这几个数是在**旧频段划分**（300/600/1000、65 抽头）下测的。
    实测（采样 16 位 / 系数 18 位）：总 SNR 74.5 dB 看着还行，
    拆开一看 0-300 Hz 那一段只有 70.3 dB，而 600-1000 Hz 有 105.6 dB。

    ⚠️ **但这个 35 dB 的差距不是"第 1 频段量化得差"。** 验过：
        · 换成白噪声输入 → 四段是平的（85.4 / 87.6 / 85.3 / 85.6）
        · 换成纯 288 Hz 正弦 → 顺序反过来（105.1 / 98.5 / 86.6 / **65.8**）
      所以这个分布是**测试信号的频谱**决定的，不是量化器决定的。
      原因：当时四个滤波器重叠得很厉害（65 抽头在 48 kHz 下频率分辨率约 740 Hz，
      而第 1、2 频段只隔 300 Hz），所以"某个频段的 SNR"量的其实是
      "信号和量化误差各自落在哪里"，不是"这一段被量化坏了"。
      **不要拿这张表去下"哪一段精度不够"的结论。**

    这一条现在**基本失效了，但故意留着**：频段划分后来换成 500/1000/2000、
    193 抽头，边界残留从 −6.0 dB 改善到 −21.4 dB（见 report/hardware_interface_spec.md §1.5），
    滤波器已经不重叠了。留着是因为——**万一哪天为了省资源把抽头数砍回 65，
    这条警告立刻重新成立**，而这个坑不太容易自己想到。

    真正站得住的结论只有一个：总 SNR 由**输入量化到 16 位**这一项主导 ——
    在 Python 里只量化输入、其余全用 float64，复现出 80.7 dB，
    与 HLS 实测的 74.5 dB 对得上（余下的差来自累加器截断和 DRC 的定点常数）。
    而 16 位本来就是音频的源格式，所以这一项不算设计的缺陷。

    ⚠️ 加窗（汉宁窗）再算谱，否则截断泄漏会把误差抹平到整个频轴上。
    """
    err = test - ref
    w = np.hanning(len(ref))
    s = np.abs(np.fft.rfft(ref * w)) ** 2
    e = np.abs(np.fft.rfft(err * w)) ** 2
    f = np.fft.rfftfreq(len(ref), 1.0 / FS)
    out = []
    for lo, hi, name in BANDS:
        m = (f >= lo) & (f < hi)
        sig, er = float(s[m].sum()), float(e[m].sum())
        if sig <= 0.0:
            continue
        out.append((name, 100.0 * sig / float(s.sum()),
                    100.0 * er / float(e.sum()),
                    (10.0 * np.log10(sig / er)) if er > 0.0 else float("inf")))
    return out


def evaluate(ref, path):
    test = np.loadtxt(path)
    if len(test) != len(ref):
        raise SystemExit("%s 是 %d 个采样，黄金参考是 %d 个 —— 不是同一批数据"
                         % (path, len(test), len(ref)))
    err = test - ref
    m = FIXED_GLOB.search(os.path.basename(path))
    return {
        "file": os.path.basename(path),
        "dw": int(m.group(1)) if m else None,
        "cw": int(m.group(2)) if m else None,
        "snr": snr_db(ref, test),
        "max_err": float(np.max(np.abs(err))),
        "rms_err": float(np.sqrt(np.mean(err.astype(np.float64) ** 2))),
        "corr": float(np.corrcoef(ref, test)[0, 1]),
    }


def collect(argv):
    if argv:
        return argv
    d = os.path.dirname(GOLDEN)
    names = sorted(n for n in os.listdir(d) if n.startswith("fixed_") and n.endswith(".txt"))
    # 只收文件名里真能读出位宽的那批。过去是"能列出来就收"，于是
    # fixed_dw16_cw18_transparent.txt、fixed_dw16_cw18_nosat.txt 这类
    # （位宽读不出来）也会被收进来，在表里印成两行 "? | ?" —— 看着像数据，
    # 其实连它是什么配置都不知道。宁可在这里点名跳过。
    keep, skipped = [], []
    for n in names:
        (keep if FIXED_GLOB.search(n) else skipped).append(n)
    if skipped:
        print("跳过 %d 个文件名里读不出位宽的 fixed_*.txt：%s"
              % (len(skipped), "、".join(skipped)), file=sys.stderr)
    return [os.path.join(d, n) for n in keep]


def render(rows):
    head = ("| 采样位 | 系数位 | SNR (dB) | 最大误差 | 均方根误差 | 相关系数 |\n"
            "|---|---|---|---|---|---|\n")
    lines = []
    for r in rows:
        dw = r["dw"] if r["dw"] is not None else "?"
        cw = r["cw"] if r["cw"] is not None else "?"
        lines.append("| %s | %s | %.1f | %.3e | %.3e | %.9f |"
                     % (dw, cw, r["snr"], r["max_err"], r["rms_err"], r["corr"]))
    return head + "\n".join(lines) + "\n"


def main():
    # Windows 的控制台默认是 GBK 编码，打印 ⚠️ 这类字符会直接抛
    # UnicodeEncodeError 把脚本打断（中文没事，因为 GBK 里有中文）。
    # 把错误处理改成 replace：编不出来的字符变成 ?，至少不中断。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not os.path.exists(GOLDEN):
        raise SystemExit("找不到黄金参考 %s —— 先跑 src/python/export_golden.py" % GOLDEN)

    ref = np.loadtxt(GOLDEN)
    files = collect(argv)
    if not files:
        raise SystemExit("一个 fixed_*.txt 都没找到")

    rows = []
    for p in files:
        try:
            rows.append(evaluate(ref, p))
        except Exception as e:                      # noqa: BLE001 —— 扫一批文件，别一个坏的全停
            print("跳过 %s：%s" % (p, e), file=sys.stderr)

    # 采样位优先、系数位其次，从窄到宽排 —— 宽的一眼落在后面
    rows.sort(key=lambda r: (r["dw"] if r["dw"] is not None else 0,
                             r["cw"] if r["cw"] is not None else 0))
    print(render(rows))

    # 烟测：不同位宽不可能给出同一个 SNR —— 每多 1 位采样该涨约 6 dB，
    # 从 12 位到 24 位该差几十 dB。整批极差不到 1 dB，那一定是数据的问题，
    # 不是"测出来就这样"。撞过一次：相减式改造之后没重跑 csim，
    # 旧输出对新黄金参考，八行 SNR 全在 −8.38（极差 0.0014 dB）。
    snrs = [r["snr"] for r in rows if r["snr"] is not None]
    if len(snrs) >= 3 and max(snrs) - min(snrs) < 1.0:
        print("\n[!] 这几行的 SNR 几乎一模一样（极差 %.4f dB），不同位宽不该这样 ——"
              "\n    这批 fixed_*.txt 多半和 python_golden.txt 不是同一版核跑出来的。"
              "\n    重跑 csim 生成新的再比，每档记得用 HLS_OUT 存成不同文件名。"
              % (max(snrs) - min(snrs)))

    # 顺带查一下"每多 1 位约 +6 dB"这条经验规律对不对得上。
    # ⚠️ 不能拿最窄和最宽两行硬除 —— 宽到一定程度后瓶颈就换了（可能是系数位宽、
    #    也可能是累加器的截断），SNR 会停下来不再涨，硬除出来的斜率就没有意义。
    #    所以这里只看**同一个系数位宽下、相邻两个采样位宽**之间的斜率，
    #    并且把已经平掉的那几档标出来。
    #
    # ⚠️ 2026-09-30：采样位被接口钉死在 16 之后（原因见文件头那条），
    #    这一维就只剩一行、构不成"相邻两档"了 —— 打印会变成"只有标题没有内容"。
    #    所以改成：有采样位宽对才打这一段，否则换打系数位宽的斜率。
    by_cw = {}
    for r in rows:
        if r["dw"] is not None and r["cw"] is not None:
            by_cw.setdefault(r["cw"], []).append(r)

    dw_pairs = [(cw, a, b)
                for cw, group in sorted(by_cw.items())
                for a, b in zip(sorted(group, key=lambda r: r["dw"]),
                                sorted(group, key=lambda r: r["dw"])[1:])]

    if dw_pairs:
        print("每多 1 位采样，SNR 涨多少（经验值约 6 dB）：")
        for cw, a, b in dw_pairs:
            slope = (b["snr"] - a["snr"]) / (b["dw"] - a["dw"])
            note = "还在按 6 dB/位走" if slope >= 5.0 else "已经平了 —— 瓶颈换到别处"
            print("  系数 %2d 位：采样 %2d → %2d 位，SNR %5.1f → %5.1f dB"
                  "（%.1f dB/位，%s）" % (cw, a["dw"], b["dw"], a["snr"], b["snr"], slope, note))
    else:
        # 采样位只剩一档时，有意义的是"系数位加到多少才够"。
        # 判据不是 6 dB/位，而是"有没有过 70 dB 那条线" + "再加还有没有用"。
        by_dw = {}
        for r in rows:
            if r["dw"] is not None and r["cw"] is not None:
                by_dw.setdefault(r["dw"], []).append(r)
        print("采样位已被接口钉死在 16（见文件头），现在能扫的是系数位：")
        for dw, group in sorted(by_dw.items()):
            group.sort(key=lambda r: r["cw"])
            prev = None
            for r in group:
                if r["cw"] < 16:
                    note = "低于 70 dB 判据"
                elif prev is not None and r["snr"] - prev < 0.5:
                    note = "再加已经没用了（瓶颈换到采样位了）"
                else:
                    note = "够用"
                print("  采样 %2d 位：系数 %2d 位，SNR %5.1f dB  ← %s"
                      % (dw, r["cw"], r["snr"], note))
                prev = r["snr"]

    print("\n参考：float32 那版与 float64 黄金参考的 SNR 约 130 dB，"
          "所以定点这几十 dB 的差距完全由位宽决定，不是别的问题。")

    # ---- 分频段再看一遍 ----
    # 列名从 BANDS 现算，不写死 —— 频段边界改过一次，写死的列名当时就没跟上，
    # 取不到 key，整张表印成 nan，还没人发现。
    names = [n for _, _, n in BANDS]
    print("\n分频段 SNR（dB）：")
    print("  %-14s %s | %8s" % ("配置", " ".join("%9s" % n for n in names), "总SNR"))
    for p in files:
        try:
            t = np.loadtxt(p)
        except Exception:                           # noqa: BLE001
            continue
        row = per_band_snr(ref, t)
        label = os.path.basename(p).replace("fixed_", "").replace(".txt", "")
        cells = {n: s for n, _, _, s in row}
        print("  %-14s %s | %8.1f" % (
            label,
            " ".join("%9.1f" % cells.get(n, float("nan")) for n in names),
            snr_db(ref, t)))

    print("\n⚠️ 各段之间能差几十 dB，但**不是「哪一段量化得差」** —— 换成白噪声输入"
          "\n   四段就是平的，换成纯正弦顺序还会反过来。"
          "\n   详见 per_band_snr() 的注释（那一条是旧频段划分下的教训，"
          "现在分离度上去了，但抽头数砍回 65 就会重新成立）。")


if __name__ == "__main__":
    main()
