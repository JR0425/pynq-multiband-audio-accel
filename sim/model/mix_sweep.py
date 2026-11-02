#!/usr/bin/env python3
"""干声并联的 mix 扫描 —— 全在逐位整数模型上跑，不碰板子、不改 RTL。

为什么做这个：
    板上量到深压档（+24 dB / −40 dBFS / k=0.5）THD+N 37.24%，机制是
    fir_drc4.v 的减法式压缩 `y = sign(a)·max(0, |a| − gr)` 在零交叉附近的
    死区。改乘法式增益要动 DSP 和时序（当前 slack 已经是 −0.090 ns），
    所以先在模型上验证另一条路：把干净的干声按比例并在湿声上。

定义：
    wet[i] = 模型输出（FIR 分频 → 增益 → 压缩 → 求和）
    dry[i] = x[i − 96] × dry_scale   ← 必须延迟 96 拍对齐群延迟，否则梳状滤波
    y[i]   = (1 − m)·dry[i] + m·wet[i]

    干声取哪一级要分两组跑：
      甲  dry_scale = 1       取段增益之前（fir_mb_fe.v 的 bsum），不改 RTL 就能拿到
      乙  dry_scale = g_lin   取段增益之后，和湿声同量级，要多一个乘法器
    只跑乙会高估这个方法 —— 湿声带 +24 dB 段增益（出核峰 7225），甲组的干声
    峰只有 803，差 19 dB，m=0.5 时干声几乎听不见。两组都要，才能说清代价。

跑法：
    python sim/model/mix_sweep.py            # 全表
    python sim/model/mix_sweep.py --check    # 只跑 numpy 快路和 process() 的逐点比对
"""

import argparse
import os
import sys
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "board", "probes"))
sys.path.insert(0, os.path.join(REPO, "board", "scripts"))

# thdn.py 顶层 `from pynq import MMIO`，离线跑要垫一个假的
_fake = types.ModuleType("pynq")
_fake.MMIO = object
sys.modules.setdefault("pynq", _fake)

import ref_model_wdrc as M  # noqa: E402
from thdn import thdn        # noqa: E402
import fir_pl_live as L      # noqa: E402

FS = 48828.125
N = 1024
K0 = 21                       # 21 × FS / N = 1001.36 Hz，正好落在 bin 上
DELAY = 96                    # 3 级 193 抽头 FIR 的群延迟
PEAK = 803                    # 板上入核电平（−32 dBFS 播放衰减之后）


def tone(n, peak=PEAK):
    t = np.arange(n, dtype=np.float64)
    x = np.round(peak * np.sin(2.0 * np.pi * K0 * t / N))
    return x.astype(np.int64)


def _sat(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def process_fast(x, coeffs, na, thr, ratio, gain, attack, release):
    """和 M.process() 逐点相同的快路：3 个低通走 numpy 卷积，DRC 仍逐点。

    参数是 Q1.15 / Q7.8 域的值，内部再 <<7 展开成 Q2.22，和 process() 一致。
    """
    x = np.asarray(x, dtype=np.int64)
    n = len(x)
    atk = attack << 7
    rel = release << 7
    thr = thr << 7
    ratio = ratio << 7

    ylp = [np.convolve(x, np.asarray(c, dtype=np.int64))[:n] for c in coeffs]

    xd = np.zeros(n, dtype=np.int64)
    if n > DELAY:
        xd[DELAY:] = x[:n - DELAY]
    xd <<= 17
    bands = [ylp[0], ylp[1] - ylp[0], ylp[2] - ylp[1], xd - ylp[2]]

    lo, hi = M.D24_MIN, M.D24_MAX
    acc = np.zeros(n, dtype=np.int64)
    for b in range(M.N_BANDS):
        gb = np.clip(bands[b] >> 10, lo, hi)
        gb = np.clip((gb * gain) >> 8, lo, hi)
        yv = np.empty(n, dtype=np.int64)
        env = 0
        omr = (1 << 22) - ratio
        for i in range(n):
            xx = int(gb[i])
            a = -xx if xx < 0 else xx
            diff = a - env
            env = _sat(((env << 22) + diff * (atk if diff > 0 else rel)) >> 22, lo, hi)
            if env < 0:
                env = 0
            gr = 0
            if env > thr:
                gr = _sat(((env - thr) * omr) >> 22, lo, hi)
            a2 = a - gr
            if a2 < 0:
                a2 = 0
            yv[i] = -a2 if xx < 0 else a2
        acc += yv << 10

    out = np.clip(acc >> 17, -32768, 32767)
    return out.astype(np.int64)


def mix(x, wet, dry_scale, m):
    """干声延迟 96 拍后和湿声按 m 混合。

    dry_scale 决定干声取在哪一级：
      1.0    段增益之前（fir_mb_fe.v 的 bsum），不改 RTL 就能拿到
      g_lin  段增益之后，和湿声同量级，要多一个乘法器
    """
    n = len(x)
    dry = np.zeros(n, dtype=np.float64)
    if n > DELAY:
        dry[DELAY:] = x[:n - DELAY].astype(np.float64) * dry_scale
    y = (1.0 - m) * dry + m * wet.astype(np.float64)
    return np.clip(np.round(y), -32768, 32767), dry


def measure(y):
    """最后 3 帧 1024 点，取 THD+N 中位（和板上 thdn.py 同一条判据）。

    峰和 rms 也在这 3072 点里取 —— 取整段会把开头的启动瞬态算进去：
    包络还没建立时压缩器不起作用，那一下的峰值就是线性满幅，和板上的
    稳态读数对不上（实测整段峰 12727，稳态 7225 附近）。
    """
    w = y[-3 * N:]
    frames = [y[-3 * N:-2 * N], y[-2 * N:-N], y[-N:]]
    res = [thdn(f) for f in frames]
    res = [r for r in res if r is not None]
    if not res:
        return None
    thd = sorted(r[1] for r in res)[len(res) // 2]
    return thd, int(np.max(np.abs(w))), rms(w)


def rms(a):
    return float(np.sqrt(np.mean(np.asarray(a, dtype=np.float64) ** 2)))


def db(v, ref=1.0):
    return 20.0 * np.log10(max(v, 1e-12) / ref)


def run_case(name, x, coeffs, na, gain_db, thr_db, ratio_k, atk_ms, rel_ms, mixes,
             dry_scale):
    gain = L.db_to_gain(gain_db)
    thr = L.dbfs_to_thr(thr_db) >> 7
    ratio = L.ratio_to_reg(ratio_k) >> 7
    atk = L.ms_to_coef(atk_ms) >> 7
    rel = L.ms_to_coef(rel_ms) >> 7
    g_lin = gain / 256.0

    wet = process_fast(x, coeffs, na, thr, ratio, gain, atk, rel)

    # 两条固定参考线：只放大不压缩（线性满幅），和纯湿声
    lin = np.clip(np.round(x.astype(np.float64) * g_lin), -32768, 32767)
    rms_lin = rms(lin[-3 * N:])

    rows = []
    for m in mixes:
        y, dry = mix(x, wet, dry_scale, m)
        thd, peak, r_y = measure(y)
        rows.append({
            "m": m, "thd": thd, "peak": peak, "rms": r_y,
            # 相对"只放大、不压缩"：0 dB = 一点没压
            "gr_db": db(r_y) - db(rms_lin),
            "crest": db(peak / max(r_y, 1e-9)),
        })

    # 等响度：把每条 mix 的 rms 提到纯湿声的水平，看峰值会不会顶到满量程
    ref_rms = rows[0]["rms"]
    for r in rows:
        r["lift_db"] = db(ref_rms) - db(r["rms"])
        r["peak_lifted"] = r["peak"] * (10 ** (r["lift_db"] / 20.0))

    print(f"\n### {name}  增益 {gain_db:+g} dB / 门限 {thr_db:+g} dBFS / k={ratio_k}"
          f" / atk {atk_ms} ms / rel {rel_ms} ms")
    print("| mix | THD+N | 出核峰 | 出核 rms | 相对线性 | 波峰因数 | 拉到湿声电平 +dB | 等响后峰值 |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        flag = "  ⚠️顶满量程" if r["peak_lifted"] > 32767 else ""
        print(f"| {r['m']:.1f} | {r['thd']:.2f}% | {r['peak']} | {r['rms']:.0f} | "
              f"{r['gr_db']:+.1f} dB | {r['crest']:.1f} dB | {r['lift_db']:+.1f} | "
              f"{r['peak_lifted']:.0f}{flag} |")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只跑快路和 process() 的逐点比对")
    ap.add_argument("--secs", type=float, default=1.0)
    args = ap.parse_args()

    coeffs, na = M.load_coeffs(193)

    # ---------------------------------------------------------- 快路自检
    rng = np.random.default_rng(20261102)
    for tag, xs in (("随机", rng.integers(-30000, 30000, 512)),
                    ("单音", tone(512))):
        ref = M.process(list(xs), coeffs, na, mode="wdrc", thr=3277, ratio=22938,
                        gain=256, attack=136, release=7)
        fast = process_fast(xs, coeffs, na, 3277, 22938, 256, 136, 7)
        bad = int(np.sum(fast != np.asarray(ref, dtype=np.int64)))
        print(f"快路自检 {tag}: {512 - bad}/512 相同", "PASS" if bad == 0 else "FAIL")
        if bad:
            return 1
    if args.check:
        return 0

    # ---------------------------------------------------------- 对照组
    n = int(FS * args.secs)
    x = tone(n)
    print(f"激励：{K0}×FS/1024 = {K0 * FS / N:.2f} Hz，峰值 {PEAK}，{n} 点")

    warm = run_case("对照 默认档（压缩不应启动）", x, coeffs, na,
                    22.0, -10.0, 0.7, 5.0, 100.0, [1.0], dry_scale=1.0)
    if warm[0]["thd"] > 1.0:
        print("\n⚠️ 默认档 THD 超过 1%，压缩器不该启动 —— 停，先查模型参数换算")
        return 1

    mixes = [1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]
    g_lin = L.db_to_gain(24.0) / 256.0

    groups = (
        ("甲 干声取段增益之前（fir_mb_fe.bsum，不改 RTL）", 1.0),
        ("乙 干声取段增益之后（要加一个乘法器）", g_lin),
    )
    deep_tabs = {}
    for tag, ds in groups:
        rows = run_case(f"深压档 · {tag}", x, coeffs, na,
                        24.0, -40.0, 0.5, 5.0, 100.0, mixes, dry_scale=ds)
        deep_tabs[tag] = rows
        if rows[0]["thd"] < 10.0:
            print("\n⚠️ 深压档 m=1.0 的 THD 只有 %.2f%%，和板上的 37.24%% 差太多 —— 停"
                  % rows[0]["thd"])
            return 1

    for tag, ds in groups:
        run_case(f"-20 dBFS 档 · {tag}", x, coeffs, na,
                 24.0, -20.0, 0.7, 5.0, 100.0, mixes, dry_scale=ds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
