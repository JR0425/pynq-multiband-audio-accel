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


def snr_db(ref, test):
    """信噪比：把误差当成噪声，参考信号当成信号。单位 dB，越大越好。"""
    err = test - ref
    p_sig = float(np.sum(ref.astype(np.float64) ** 2))
    p_err = float(np.sum(err.astype(np.float64) ** 2))
    if p_err == 0.0:
        return float("inf")
    return 10.0 * np.log10(p_sig / p_err)


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


if __name__ == "__main__":
    main()
