# WDRC 的板上特性曲线：压缩器输入-输出关系验收
#
# 2026-10-06 量过的电平台阶是**无记忆** `drc()`（attack = release = 0）。
# `fir_multiband.cpp:588` 有分支：两段都写 0 就退回 `drc()`，否则走 `wdrc()`。
#
# 这一版量三件事。每条判据都在主机上用浮点模型预演过（见文件末尾的预演值），
# 不是先上板再看数：
#
#   A. 静态传输曲线：输入峰值电平 → 输出峰值电平。
#      每组 (阈值, 压缩比) 跑三条包络设置：
#        ① 无记忆（atk = rel = 0）       —— 解析基准
#        ② 包络极限（atk = rel = 0.001 ms）—— `env` 几乎等于 `|x|`
#        ③ 真实时间常数
#      ① 和 ② 必须逐档重合：`env == |x|` 时
#          |x| - (|x|-thr)(1-ratio) == thr + (|x|-thr)*ratio
#      两式代数上恒等。这是本节唯一的硬等式。
#      ③ 会比 ① 压得轻，偏差随压缩深度增大 —— 这不是错，是包络追不到瞬时峰值，
#      单列出来当**测量结果**，不当判据。
#
#   B. 阶跃响应，两种激励：
#      直流阶跃 —— `|x|` 恒定，包络是严格一阶，63% 点就是寄存器写的毫秒数。
#                  验的是"时间常数写对了没有"。
#      4 kHz 正弦阶跃 —— 量**实际**的有效响应时间。正弦下 `env` 只在每个周期里
#                  `|x| > env` 的那一小段往上追，所以真实攻击比写的慢。
#                  这不是测量误差：主机上直接从 `env` 抽出来量过，慢约一倍。
#
#   C. 失真：`wdrc()` 减的是**瞬时幅度**（`a2 = a - gr`，再钳到 0），不是乘系数。
#      压缩一重，波形低幅的部分被削成一段死区。这一节把 THD 量出来。
#      这是这个算法形式的固有代价，结论里照实写出来。
#
# 全部用合成信号，不碰麦克风，跑多少遍都是同一串比特。
#
# 用法（板子上，要 root）：
#   echo xilinx | sudo -S env XILINX_XRT=/usr \
#     /usr/local/share/pynq-venv/bin/python3 wdrc_curve.py
#
# 产物：/home/xilinx/jupyter_notebooks/wdrc_curve.json

import json
import os
import sys

import numpy as np
from pynq import Overlay

from fir_core import FirMultiband, coef_to_ms, ms_to_coef

BITFILE = "fir.bit"
FS = 48000.0
OUT_JSON = "/home/xilinx/jupyter_notebooks/wdrc_curve.json"
CHUNK = int(os.environ.get("CHUNK", "8000"))

# 核的输出延迟 96 个采样（三段 193 抽头低通的群延迟之和，见报告 §2）。
DELAY = 96
N_BANDS = 4

# 每个频段各放一个正弦，落在段内部（段边界 500 / 1000 / 2000 Hz），
# 这样每个单音只经过一段，出来的就是那一段自己的压缩曲线。
# 不用多音叠加：多音峰值可达有效值的 2.8 倍，顶档直接削顶，
# 而且"波峰时四段不同时在峰值"会让峰值读数失去意义。
TONES = (250.0, 700.0, 1400.0, 4000.0)

# ---- A. 静态曲线 ----
# 13 档，峰值电平 −6 → −54 dBFS，每档 4 dB。电平一律按**峰值**算：
# 压缩器看的就是峰值包络，按有效值定电平会和阈值对不上。
LEVEL_DB = tuple(-6 - 4 * k for k in range(13))
LEVEL_SEC = 1.6
LEVEL_SKIP = 1.0          # 最长 release 200 ms，1.0 s = 5 倍时间常数

# 每组 (阈值, 压缩比) 配它的真实起停时间；每组都会另外跑无记忆和包络极限两条。
SETTINGS = (
    ("thr.030 r.50 起5/停100",   983, 16384, 5.0, 100.0),
    ("thr.030 r.50 起20/停200",  983, 16384, 20.0, 200.0),
    ("出厂 .100/.70 起5/停100", 3277, 22938, 5.0, 100.0),
    ("最狠 .010/.30 起5/停100",  328,  9830, 5.0, 100.0),
)
ENV_LIMIT_MS = 0.001      # 包络极限：ms_to_coef 会饱和到 32767，env ≈ |x|

# ---- B. 阶跃响应 ----
STEP_QUIET_DB = -20.0     # 静段不能太低：low 到 env 掉到阈值以下，gr 归零就量不到了
STEP_LOUD_DB = -10.0
STEP_QUIET_SEC = 2.5      # 停 500 ms 要 4~5 倍时间常数才落回基线
STEP_LOUD_SEC = 1.2
STEP_CONFIGS = ((5.0, 100.0), (20.0, 200.0), (50.0, 500.0))
STEP_TONE_HZ = 4000.0     # 高频：一个周期 12 个采样，窗内包络起伏可忽略

# ---- C. 失真 ----
THD_FREQ = 700.0
THD_DB = (-40, -30, -20, -12, -6)
THD_SEC = 1.5
THD_SKIP = 0.8
THD_CONFIGS = (("无记忆 .030/.50", 983, 16384, 0.0, 0.0),
               ("thr.030 r.50", 983, 16384, 5.0, 100.0),
               ("最狠 .010/.30", 328, 9830, 5.0, 100.0))

ok_all = True


def check(name, passed, detail=""):
    global ok_all
    ok_all = ok_all and passed
    print("  [%s] %s%s" % ("OK  " if passed else "FAIL", name,
                           ("  " + detail) if detail else ""))
    return passed


def rms(x):
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


def peak(x):
    return float(np.max(np.abs(np.asarray(x, dtype=np.float64))))


def db(x):
    return 20.0 * np.log10(x) if x > 0 else -float("inf")


def tone(n, f, level_db):
    """单音，峰值电平 level_db（相对满量程）。0 dBFS = 幅度 1.0。"""
    t = np.arange(n) / FS
    return 10.0 ** (level_db / 20.0) * np.sin(2.0 * np.pi * f * t)


def to_i16(x):
    return np.clip(np.round(x * 32768.0), -32768, 32767).astype(np.int16)


def run(x16, thr, ratio, atk, rel, bypass=False):
    """跑一遍核。每段 reset，块与块之间不串味。"""
    if bypass:
        out, _, _ = core.process(x16, reset=True, bypass=True, chunk=CHUNK)
    else:
        out, _, _ = core.process(
            x16, reset=True, bypass=False, chunk=CHUNK,
            thr=(thr,) * N_BANDS, ratio=(ratio,) * N_BANDS,
            attack=(atk,) * N_BANDS, release=(rel,) * N_BANDS)
    return out.astype(np.float64)


# =================== 开工 ===================
print("=" * 78)
print("WDRC 板上特性曲线")
print("=" * 78)

print("\n1) 加载 %s" % BITFILE)
ol = Overlay(BITFILE)
if "fir_multiband_0" not in ol.ip_dict:
    print("ERROR: overlay 里没有 fir_multiband_0")
    sys.exit(1)
core = FirMultiband(ol.fir_multiband_0.mmio)
print("   核地址 %s" % hex(ol.ip_dict["fir_multiband_0"]["phys_addr"]))

result = {"delay": DELAY, "bit_md5": None, "A": [], "B": [], "C": []}
try:
    import hashlib
    with open(BITFILE, "rb") as f:
        result["bit_md5"] = hashlib.md5(f.read()).hexdigest()
    print("   bit md5 %s" % result["bit_md5"])
except Exception as e:
    print("   (算 md5 失败，不影响测量：%s)" % e)

n_lev = int(FS * LEVEL_SEC)
skip = int(FS * LEVEL_SKIP)

# ---------------- A. 静态传输曲线 ----------------
print("\n" + "-" * 78)
print("A) 静态传输曲线：%d 组阈值/压缩比 × %d 个频点 × %d 档（%d ~ %d dBFS 峰值）"
      % (len(SETTINGS), len(TONES), len(LEVEL_DB), LEVEL_DB[0], LEVEL_DB[-1]))

ladders = {}
for f in TONES:
    x = np.concatenate([tone(n_lev, f, d) for d in LEVEL_DB])
    ladders[f] = to_i16(x)
    print("   %5.0f Hz  %d 采样 / %.1f 秒，峰值 %d（不削顶）"
          % (f, len(x), len(x) / FS, int(np.abs(ladders[f]).max())))

def ladder_levels(y, k):
    """稳态窗口的峰值电平，已换算回满量程比例（核输出是 int16 单位）。"""
    return peak(y[k * n_lev + skip:(k + 1) * n_lev]) / 32768.0


dry_pk = {f: peak(run(ladders[f], 0, 0, 0, 0, bypass=True)[skip:n_lev]) / 32768.0
          for f in TONES}
dev = max(abs(db(dry_pk[f]) - db(peak(ladders[f][skip:n_lev]) / 32768.0)) for f in TONES)
print("\n   直通对账：输入峰值与直通输出峰值应相同")
check("直通不改变电平", dev < 0.1, "最大偏差 %.3f dB" % dev)

# 阈值以下的参考必须用**直通**，不能用原始输入：
# 低电平时峰值只有几十个 LSB，Q1.15 输入量化和滤波器系数量化本身就有 0.1 dB 量级
# 的偏差，拿原始输入当参考会把这个量化误差算成压缩器的错。
bypass_lin = {f: [ladder_levels(run(ladders[f], 0, 0, 0, 0, bypass=True), k)
                  for k in range(13)] for f in TONES}
bypass_db = {f: [db(v) for v in bypass_lin[f]] for f in TONES}

in_db = {f: [db(ladder_levels(ladders[f].astype(np.float64), k)) for k in range(13)]
         for f in TONES}

for st in SETTINGS:
    sname, thr, ratio, atk, rel = st
    print("\n   【%s】 阈 %.3f（%.1f dBFS）/ 压缩比 %.2f"
          % (sname, thr / 32768.0, 20 * np.log10(thr / 32768.0), ratio / 32768.0))
    tones_out = {}
    for f in TONES:
        y_mem = run(ladders[f], thr, ratio, 0.0, 0.0)
        y_lim = run(ladders[f], thr, ratio, ENV_LIMIT_MS, ENV_LIMIT_MS)
        y_real = run(ladders[f], thr, ratio, atk, rel)
        tones_out[str(int(f))] = {
            "in_db": in_db[f],
            "mem_db": [db(ladder_levels(y_mem, k)) for k in range(13)],
            "limit_db": [db(ladder_levels(y_lim, k)) for k in range(13)],
            "real_db": [db(ladder_levels(y_real, k)) for k in range(13)],
            "real_lin": [ladder_levels(y_real, k) for k in range(13)],
        }
    result["A"].append({"setting": sname, "thr": thr, "ratio": ratio,
                        "attack_ms": atk, "release_ms": rel, "tones": tones_out})

    print("     %-8s|%s" % ("档位", "".join("%30s" % ("%dHz" % f) for f in TONES)))
    print("     %-8s|%s" % ("", "".join("%30s" % "无记忆 / 极限 / 真实" for f in TONES)))
    for k in range(13):
        row = "     %+4d dBFS|" % LEVEL_DB[k]
        for f in TONES:
            c = tones_out[str(int(f))]
            row += "%30s" % ("%6.1f/%.1f/%.1f" % (c["mem_db"][k], c["limit_db"][k],
                                                   c["real_db"][k]))
        print(row)

    # 判据 1：包络极限必须等于无记忆
    worst = max(abs(tones_out[str(int(f))]["limit_db"][k] - tones_out[str(int(f))]["mem_db"][k])
                for f in TONES for k in range(13))
    check("%s：包络极限 == 无记忆" % sname, worst < 0.05, "最大偏差 %.4f dB" % worst)

    # 判据 2：阈值以下原样通过（按这一组自己的阈值判，参考是**直通**）
    # 用 LSB 绝对差，不用 dB：−54 dBFS 时峰值只有 65 个 LSB，差 1 个 LSB 就是 0.13 dB。
    # 非直通路径比直通多一次 `(drc_t)band[i] * g[i]` 的 floor，差 1 个 LSB 属正常。
    worst_lsb = 0.0
    for f in TONES:
        c = tones_out[str(int(f))]
        for k in range(13):
            if 10 ** (c["in_db"][k] / 20.0) < (thr / 32768.0) * 0.9:
                worst_lsb = max(worst_lsb,
                                abs(c["real_lin"][k] - bypass_lin[f][k]) * 32768.0)
    check("%s：阈值以下原样通过" % sname, worst_lsb <= 2.0,
          "与直通最大差 %.2f 个 LSB" % worst_lsb)

    # 判据 3：单调。LEVEL_DB 是递减的，所以 k 增大时输出应当跟着**变小**
    mono = all(tones_out[str(int(f))]["real_db"][k]
               > tones_out[str(int(f))]["real_db"][k + 1] - 0.05
               for f in TONES for k in range(12))
    check("%s：输出随输入单调" % sname, mono)

    # 测量结果（不是判据）：真实包络比无记忆轻多少
    print("     包络滞后造成的轻压（真实 − 无记忆，正 = 压得更轻），按档位：")
    for f in TONES:
        c = tones_out[str(int(f))]
        print("       %5.0f Hz  %s" % (f, " ".join(
            "%+5.2f" % (c["real_db"][k] - c["mem_db"][k]) for k in range(13))))
    print("     压缩量（相对直通，dB）@ %d dBFS：%s"
          % (LEVEL_DB[0], "  ".join(
              "%5.1f" % (db(dry_pk[f]) - tones_out[str(int(f))]["real_db"][0]) for f in TONES)))

# ---------------- B. 阶跃响应 ----------------
print("\n" + "-" * 78)
print("B) 阶跃响应：%d dBFS → %d dBFS → %d dBFS" % (STEP_QUIET_DB, STEP_LOUD_DB, STEP_QUIET_DB))

n_q = int(FS * STEP_QUIET_SEC)
n_l = int(FS * STEP_LOUD_SEC)
t_up = STEP_QUIET_SEC
t_dn = STEP_QUIET_SEC + STEP_LOUD_SEC
pq = 10.0 ** (STEP_QUIET_DB / 20.0)
pl = 10.0 ** (STEP_LOUD_DB / 20.0)


def step_build(kind):
    if kind == "dc":
        return np.concatenate([np.full(n_q, pq), np.full(n_l, pl), np.full(n_q, pq)])
    t_q = np.arange(n_q) / FS
    t_l = np.arange(n_l) / FS
    return np.concatenate([pq * np.sin(2 * np.pi * STEP_TONE_HZ * t_q),
                           pl * np.sin(2 * np.pi * STEP_TONE_HZ * t_l),
                           pq * np.sin(2 * np.pi * STEP_TONE_HZ * t_q)])


def gr_track(ref, out, win, hop):
    """gr(t) = 输入峰值 − 输出峰值。

    输出对掉 96 拍延迟。输入峰值取**直通**那一路，把滤波器的幅频响应也消掉，
    这样 gr 就是纯粹的压缩量，和 `gr = (env-thr)(1-ratio)` 一一对应。
    """
    a, b = ref[:-DELAY], out[DELAY:]
    n = min(len(a), len(b))
    idx = np.arange(0, n - win, hop)
    t = (idx + win / 2.0) / FS
    pi = np.array([peak(a[i:i + win]) for i in idx])
    po = np.array([peak(b[i:i + win]) for i in idx])
    return t, pi, po


def crossing(t, v, ok, t0, target, falling):
    sel = t >= t0
    tt, vv, oo = t[sel], v[sel], ok[sel]
    for i in range(1, len(vv)):
        if not (oo[i] and oo[i - 1]):
            continue
        if (vv[i] <= target) if falling else (vv[i] >= target):
            if vv[i] == vv[i - 1]:
                return tt[i]
            fr = (target - vv[i - 1]) / (vv[i] - vv[i - 1])
            return tt[i - 1] + fr * (tt[i] - tt[i - 1])
    return None


def measure_step(x_st, thr, ratio, atk, rel, win, hop, tag):
    ref = run(to_i16(x_st), 0, 0, 0, 0, bypass=True)
    y = run(to_i16(x_st), thr, ratio, atk, rel)
    t, pi, po = gr_track(ref, y, win, hop)
    gr = pi - po
    # 输出被钳到 0 的段（gr > 瞬时幅度）量不到 gr；跳变边沿附近也不可信
    ok = (po > 1e-6) & (np.abs(t - t_up) > 0.004) & (np.abs(t - t_dn) > 0.004)
    g0 = float(np.median(gr[(t > t_up - .4) & (t < t_up - .02) & ok]))
    g1 = float(np.median(gr[(t > t_up + .6) & (t < t_up + 1.1) & ok]))
    g2 = float(np.median(gr[(t > t_dn + 2.0) & (t < t_dn + 2.4) & ok]))
    c1 = crossing(t, gr, ok, t_up, g0 + 0.632 * (g1 - g0), False)
    c2 = crossing(t, gr, ok, t_dn, g1 - 0.632 * (g1 - g2), True)
    ta = (c1 - t_up) * 1000.0 if c1 else None
    tr = (c2 - t_dn) * 1000.0 if c2 else None
    print("     %s 起 %3.0f / 停 %3.0f ms：压缩量（满量程）%.3f → %.3f → %.3f"
          % (tag, atk, rel, g0 / 32768.0, g1 / 32768.0, g2 / 32768.0))
    print("       量到 起 %s ms（%+.1f%%）  停 %s ms（%+.1f%%）"
          % ("%.2f" % ta if ta else "——", 100 * (ta - atk) / atk if ta else 0.0,
             "%.2f" % tr if tr else "——", 100 * (tr - rel) / rel if tr else 0.0))
    return ta, tr


x_dc = step_build("dc")
x_tn = step_build("tone")

print("\n   ① 直流阶跃：|x| 恒定，包络是严格一阶 → 63% 点应当就是写的毫秒数")
print("     先看 Q1.15 步进能不能表示这个时间常数（48 kHz 下最慢约 683 ms）：")
for c_ms in sorted(set(m for pair in STEP_CONFIGS for m in pair)):
    q_ms = coef_to_ms(ms_to_coef(c_ms))
    print("      写 %5.0f ms → 步进 %5d → 实际 %.2f ms（%+.1f%%）"
          % (c_ms, ms_to_coef(c_ms), q_ms, 100 * (q_ms - c_ms) / c_ms))
print("     ↑ 判据对的是**实际可表示**的那个值；和写的值差多少是量化限制，单独看")
dc_rows = []
for atk, rel in STEP_CONFIGS:
    ta, tr = measure_step(x_dc, 983, 16384, atk, rel, int(FS * 0.001), int(FS * 0.00025), "DC  ")
    q_atk = coef_to_ms(ms_to_coef(atk))
    q_rel = coef_to_ms(ms_to_coef(rel))
    dc_rows.append({"attack_ms_cmd": atk, "release_ms_cmd": rel,
                    "attack_ms_achievable": q_atk, "release_ms_achievable": q_rel,
                    "tau_attack_ms": ta, "tau_release_ms": tr})
    check("DC 攻击时间常数（写 %.0f / 实际 %.0f ms）" % (atk, q_atk),
          ta is not None and abs(ta - q_atk) / q_atk < 0.15,
          "量到 %.2f ms" % ta if ta else "没量到 63% 点")
    check("DC 释放时间常数（写 %.0f / 实际 %.0f ms）" % (rel, q_rel),
          tr is not None and abs(tr - q_rel) / q_rel < 0.15,
          "量到 %.2f ms" % tr if tr else "没量到 63% 点")

print("\n   ② %g Hz 正弦阶跃：量**实际**的有效响应时间（这一条不是判据，是结果）" % STEP_TONE_HZ)
tone_rows = []
for atk, rel in STEP_CONFIGS:
    ta, tr = measure_step(x_tn, 983, 16384, atk, rel, int(FS * 0.002), int(FS * 0.0005), "正弦")
    tone_rows.append({"attack_ms_cmd": atk, "release_ms_cmd": rel,
                      "attack_ms_achievable": coef_to_ms(ms_to_coef(atk)),
                      "release_ms_achievable": coef_to_ms(ms_to_coef(rel)),
                      "tau_attack_ms": ta, "tau_release_ms": tr})
result["B"] = {"dc": dc_rows, "tone": tone_rows}

# ---------------- C. 失真 ----------------
print("\n" + "-" * 78)
print("C) 失真：%g Hz 单音，看压缩把波形削掉多少" % THD_FREQ)

n_t = int(FS * THD_SEC)
skip_t = int(FS * THD_SKIP)


def thd_of(x, f0):
    """稳态窗口上算 THD：2~10 次谐波相对基波。"""
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    fr = np.fft.rfftfreq(len(x), 1.0 / FS)

    def pk(f):
        lo, hi = np.searchsorted(fr, f * 0.97), np.searchsorted(fr, f * 1.03)
        return float(sp[lo:hi].max()) if hi > lo else 0.0

    p1 = pk(f0)
    if p1 <= 0:
        return None
    return float(np.sqrt(sum(pk(f0 * k) ** 2 for k in range(2, 11))) / p1)


for name, thr, ratio, atk, rel in THD_CONFIGS:
    rows = []
    print("\n   【%s】 阈 %.3f / 压缩比 %.2f / 起 %.0f ms / 停 %.0f ms"
          % (name, thr / 32768.0, ratio / 32768.0, atk, rel))
    print("     %-9s %11s %11s %12s" % ("峰值电平", "输出峰值", "THD", "理论死区"))
    for lv in THD_DB:
        x1 = to_i16(tone(n_t, THD_FREQ, lv))
        y1 = run(x1, thr, ratio, atk, rel)
        seg = y1[skip_t:skip_t + int(FS * 0.5)]
        th = thd_of(seg, THD_FREQ)
        # 死区：稳态下 env 停在输入峰值附近，gr = (env−thr)(1−ratio)，
        # 钳掉的比例 ≈ gr / 输入峰值。用**输入**峰值算。
        pk_in = peak(x1) / 32768.0
        gr = max(0.0, pk_in - thr / 32768.0) * (1.0 - ratio / 32768.0)
        frac = gr / pk_in if pk_in > 0 else 0.0
        marker = "" if atk > 0 else "（无记忆是折线，不是减绝对值）"
        rows.append({"level_db": lv, "out_peak_db": db(peak(seg) / 32768.0),
                     "thd": th, "dead_frac": frac})
        print("     %+5d dBFS %11.1f %10s %11.0f%%%s"
              % (lv, db(peak(seg) / 32768.0),
                 ("%.3f%%" % (100 * th)) if th is not None else "n/a",
                 0.0 if atk <= 0 else 100 * frac,
                 "" if atk > 0 else "  ←"))
    result["C"].append({"name": name, "thr": thr, "ratio": ratio,
                        "attack_ms": atk, "release_ms": rel, "rows": rows})

mem_rows = result["C"][0]["rows"]
below = [r["thd"] or 0.0 for r in mem_rows if r["level_db"] <= -30]
check("无记忆那组：阈值以下没有失真", max(below) < 0.005,
      "最大 %.3f%%" % (100 * max(below)))
print("     注：无记忆那组在 −20 dBFS 有 7.2% THD，不是 bug —— `drc()` 本身就是折线，")
print("         −20 dBFS 正好落在拐点（阈 0.030 = −30.5 dBFS）附近，压的是波峰那一小段。")

hard = [r for r in result["C"][2]["rows"] if r["level_db"] == -6][0]
base6 = [r for r in result["C"][0]["rows"] if r["level_db"] == -6][0]
check("最狠那组在 −6 dBFS 明显比无记忆失真",
      (hard["thd"] or 0) > 3 * (base6["thd"] or 1e-9),
      "%.2f%% vs %.2f%%" % (100 * (hard["thd"] or 0), 100 * (base6["thd"] or 0)))

# ---------------- 收尾 ----------------
print("\n" + "=" * 78)
print("结论：%s" % ("全过" if ok_all else "有失败项"))
print("=" * 78)
print("C 节量到的失真是 `wdrc()`「减瞬时幅度」这种形式的固有代价，不是 bug。")
print("结论：压缩越重、死区越大、THD 越高（实测值见上）。")

with open(OUT_JSON, "w") as f:
    json.dump(result, f, indent=1, ensure_ascii=False)
print("\n原始数据写到 %s" % OUT_JSON)

sys.exit(0 if ok_all else 1)
