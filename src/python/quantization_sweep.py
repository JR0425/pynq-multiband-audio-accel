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
"""

import os
import re
import sys

import numpy as np

GOLDEN = "data/results/python_golden.txt"
FIXED_GLOB = re.compile(r"fixed_dw(\d+)_cw(\d+)\.txt$")

# 四个频段的分界（Hz）—— 与滤波器设计一致
BANDS = [(0, 300, "1: 0-300"), (300, 600, "2: 300-600"),
         (600, 1000, "3: 600-1k"), (1000, 8000, "4: 1k-8k"),
         (8000, 24000, "带外 8k+")]
FS = 48000


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

    实测（采样 16 位 / 系数 18 位）：总 SNR 74.5 dB 看着还行，
    拆开一看 0-300 Hz 那一段只有 70.3 dB，而 600-1000 Hz 有 105.6 dB。

    ⚠️ **但这个 35 dB 的差距不是"第 1 频段量化得差"。** 验过：
        · 换成白噪声输入 → 四段是平的（85.4 / 87.6 / 85.3 / 85.6）
        · 换成纯 288 Hz 正弦 → 顺序反过来（105.1 / 98.5 / 86.6 / **65.8**）
      所以这个分布是**测试信号的频谱**决定的，不是量化器决定的。
      原因：四个滤波器重叠很厉害（65 抽头在 48 kHz 下频率分辨率约 740 Hz，
      而第 1、2 频段只隔 300 Hz），所以"某个频段的 SNR"量的其实是
      "信号和量化误差各自落在哪里"，不是"这一段被量化坏了"。
      **不要拿这张表去下"哪一段精度不够"的结论。**

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
    return [os.path.join(d, n) for n in names]


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

    # 顺带查一下"每多 1 位约 +6 dB"这条经验规律对不对得上。
    # ⚠️ 不能拿最窄和最宽两行硬除 —— 宽到一定程度后瓶颈就换了（可能是系数位宽、
    #    也可能是累加器的截断），SNR 会停下来不再涨，硬除出来的斜率就没有意义。
    #    所以这里只看**同一个系数位宽下、相邻两个采样位宽**之间的斜率，
    #    并且把已经平掉的那几档标出来。
    by_cw = {}
    for r in rows:
        if r["dw"] is not None and r["cw"] is not None:
            by_cw.setdefault(r["cw"], []).append(r)

    print("每多 1 位采样，SNR 涨多少（经验值约 6 dB）：")
    for cw, group in sorted(by_cw.items()):
        group.sort(key=lambda r: r["dw"])
        for a, b in zip(group, group[1:]):
            slope = (b["snr"] - a["snr"]) / (b["dw"] - a["dw"])
            note = "还在按 6 dB/位走" if slope >= 5.0 else "已经平了 —— 瓶颈换到别处"
            print("  系数 %2d 位：采样 %2d → %2d 位，SNR %5.1f → %5.1f dB"
                  "（%.1f dB/位，%s）" % (cw, a["dw"], b["dw"], a["snr"], b["snr"], slope, note))

    print("\n参考：float32 那版与 float64 黄金参考的 SNR 约 130 dB，"
          "所以定点这几十 dB 的差距完全由位宽决定，不是别的问题。")

    # ---- 分频段再看一遍 ----
    print("\n分频段 SNR（dB）：")
    print("  %-14s %9s %9s %9s %9s | %8s" % ("配置", "0-300", "300-600", "600-1k", "1k-8k", "总SNR"))
    for p in files:
        try:
            t = np.loadtxt(p)
        except Exception:                           # noqa: BLE001
            continue
        row = per_band_snr(ref, t)
        label = os.path.basename(p).replace("fixed_", "").replace(".txt", "")
        cells = {n: s for n, _, _, s in row}
        print("  %-14s %9.1f %9.1f %9.1f %9.1f | %8.1f" % (
            label,
            cells.get("1: 0-300", float("nan")), cells.get("2: 300-600", float("nan")),
            cells.get("3: 600-1k", float("nan")), cells.get("4: 1k-8k", float("nan")),
            snr_db(ref, t)))

    print("\n⚠️ 这张表里各段能差 35 dB，但**不是「哪一段量化得差」** —— 换成白噪声输入"
          "\n   四段就是平的，换成纯正弦顺序还会反过来。四个滤波器重叠得太厉害，"
          "\n   这张表量的是「信号和误差各自落在哪」，不是「哪一段精度不够」。"
          "\n   详见 per_band_snr() 的注释。")


if __name__ == "__main__":
    main()
