"""
频段划分方案扫描 —— 比较几种"分几段、边界在哪、用哪种设计法"的组合。

背景:现行方案(65 抽头 @ 48 kHz,边界 300/600/1000/8000)实测低频前三段几乎没分开。
本脚本量化两件事:
  ① 串音(leakage):一段的能量漏进了别的段多少 dB
  ② 求和平坦度(sum flatness):各段加起来能不能还原原信号

两种设计法:
  naive —— 每段各自 firwin(现行做法)
  diff  —— 每段 = 两个低通之差,band_i = LP(f_i+1) - LP(f_i)

  diff 的好处(可算,不用试):
    · 各段之和 = LP(Nyquist) - LP(0) = 1 - 0 = 1,**恒定全通**,和抽出什么边界无关
    · 每段在直流和 Nyquist 处**恒为零**(两个低通在两端都等于 1)→
      彻底消掉"想要带通、出来却是通到 DC"这个毛病
    · 硬件上是 4 个**低通** + 3 个减法,和现在的 4 个滤波器**一样的规模**

用法:python src/python/band_plan_sweep.py
"""

import numpy as np
from scipy import signal

from band_design import FS, NYQ

# ⚠️ 本脚本是**调研工具**，不是生产路径。它同时保留"现行方案"（300/600/1000，
#    4 段各自 firwin）和几个候选方案，用来把"为什么换"量出来；
#    真正给硬件用的那套在 band_design.py 里，只有一处定义。


# ---------------------------------------------------------------- 滤波器设计

def design_naive(plan, n):
    """每段独立设计(现行做法):低通 / 带通 / 高通各管各的。"""
    filts = []
    for lo, hi in plan:
        if lo == 0:
            h = signal.firwin(n, hi, fs=FS, pass_zero='lowpass')
        elif hi >= NYQ:
            h = signal.firwin(n, lo, fs=FS, pass_zero='highpass')
        else:
            h = signal.firwin(n, [lo, hi], fs=FS, pass_zero='bandpass')
        filts.append(h)
    return filts


def design_diff(plan, n):
    """每段 = 两个低通之差。各段之和恒为 1,且每段在 DC / Nyquist 处恒为 0。"""
    edges = [b[1] for b in plan[:-1]]                 # 内部边界
    lps = [signal.firwin(n, e, fs=FS, pass_zero='lowpass') for e in edges]
    filts = []
    for i in range(len(plan)):
        lower = lps[i - 1] if i > 0 else np.zeros(n)   # 第一段的下界是 DC → 低通为 0
        upper = lps[i] if i < len(edges) else None     # 最后一段的上界是 Nyquist(恒通)
        if upper is None:
            band = np.zeros(n)
            band[(n - 1) // 2] = 1.0                   # δ,即"全通"
            band = band - lower
        else:
            band = upper - lower
        filts.append(band)
    return filts


# ---------------------------------------------------------------- 频响

def response(filts, freqs):
    """各滤波器在给定频率上的复频响,返回 shape=(nbands, nfreq)。"""
    w = np.pi * np.asarray(freqs, dtype=float) / NYQ
    out = []
    for h in filts:
        k = np.arange(len(h))
        out.append(np.sum(h[None, :] * np.exp(-1j * np.outer(w, k)), axis=1))
    return np.array(out)


def mag_db(filts, freqs):
    return 20 * np.log10(np.abs(response(filts, freqs)) + 1e-12)


def band_centers(plan):
    """每段的代表频率:低频段用算术中心,其余用几何中心。"""
    out = []
    for lo, hi in plan:
        hi = min(hi, 8000)
        out.append(hi / 3 if lo == 0 else np.sqrt(lo * hi))
    return out


def worst_crosstalk(filts, plan):
    """对每条边界 f_e,量低段在 1.5*f_e、高段在 0.5*f_e 的残留。"""
    rows = []
    for i in range(len(plan) - 1):
        fe = plan[i][1]
        low_at = mag_db(filts, [1.5 * fe])[i][0]
        high_at = mag_db(filts, [0.5 * fe])[i + 1][0]
        rows.append((fe, low_at, high_at, max(low_at, high_at)))
    return rows


def flatness(filts):
    """各段之和在 100~8000 Hz 的峰谷差(dB)。理想是 0。"""
    freqs = np.linspace(100, 8000, 2000)
    tot = np.abs(response(filts, freqs).sum(axis=0))
    db = 20 * np.log10(tot + 1e-12)
    return db.max() - db.min(), db.min(), db.max()


# ---------------------------------------------------------------- 扫描

PLANS = [
    # 第一行是"原来那套"，留着当对照 —— 这个脚本就是靠对比把它换掉的。
    # 它**不是**现在用的方案：现行边界定义在 src/python/band_design.py。
    ("原方案 300/600/1000",  [(0, 300), (300, 600), (600, 1000), (1000, NYQ)]),
    ("倍频程 500/1000/2000", [(0, 500), (500, 1000), (1000, 2000), (2000, NYQ)]),
    ("倍频程 250/500/1000",  [(0, 250), (250, 500), (500, 1000), (1000, NYQ)]),
    ("三段 500/2000",        [(0, 500), (500, 2000), (2000, NYQ)]),
    ("两段 1000",            [(0, 1000), (1000, NYQ)]),
]


def report(plan, n=65):
    name, bands = plan
    print(f"\n{'=' * 80}")
    print(f"方案:{name}   抽头数 N={n}   段数={len(bands)}")
    print(f"{'=' * 80}")

    centers = band_centers(bands)
    for mode, fn in (("naive", design_naive), ("diff", design_diff)):
        filts = fn(bands, n)
        print(f"\n  [{mode}] 串音矩阵(dB)——行=哪一段输出,列=输入频率;对角线应≈0")
        print("        " + "".join(f"{c:>10.0f}Hz" for c in centers))
        for i in range(len(filts)):
            line = f"  段{i + 1}  "
            for c in centers:
                line += f"{mag_db(filts, [c])[i][0]:>12.1f}"
            print(line)

        dc = mag_db(filts, [0.001])[:, 0]
        print(f"  直流增益:" + "".join(f"  段{i + 1} {v:>6.1f} dB" for i, v in enumerate(dc)))

        rng, lo, hi = flatness(filts)
        print(f"  求和平坦度(100~8000Hz): 峰谷差 {rng:.2f} dB (min {lo:.2f} / max {hi:.2f})")

        print("  边界残留(判据:两栏都 <= -20 dB 才算分开):")
        for fe, low_at, high_at, worst in worst_crosstalk(filts, bands):
            mark = "OK " if worst <= -20 else "NG "
            print(f"    边界 {fe:>5.0f} Hz: 低段在 {1.5 * fe:>6.0f}Hz = {low_at:>7.1f} dB | "
                  f"高段在 {0.5 * fe:>6.0f}Hz = {high_at:>7.1f} dB  [{mark}]")


def tap_sweep(label, bands, mode="diff"):
    fn = {"diff": design_diff, "naive": design_naive}[mode]
    print(f"\n{'=' * 80}")
    print(f"抽头数扫描:「{label}」,设计法 {mode}")
    print(f"{'=' * 80}")
    print("  抽头数   过渡带(3.3*fs/N)   最差串音    求和峰谷差")
    for n in (65, 129, 257, 385, 513):
        filts = fn(bands, n)
        worst = max(r[3] for r in worst_crosstalk(filts, bands))
        rng = flatness(filts)[0]
        print(f"  {n:>5d}   {3.3 * FS / n:>13.0f} Hz  {worst:>8.1f} dB  {rng:>9.2f} dB")


if __name__ == "__main__":
    for p in PLANS:
        report(p)

    tap_sweep("倍频程 500/1000/2000", [(0, 500), (500, 1000), (1000, 2000), (2000, NYQ)])
    tap_sweep("原方案 300/600/1000", [(0, 300), (300, 600), (600, 1000), (1000, NYQ)])
