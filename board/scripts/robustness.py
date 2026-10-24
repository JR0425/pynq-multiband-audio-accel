# 鲁棒性测试：异常输入下的行为验收
#
# 目标不是"证明没有 bug"，而是把边界条件挨个撞一遍，
# 让结果文档里能写一句"这些输入下不崩、不卡、不绕回"，而且有输出为证。
#
# 分五组：
#   A 输入边界 —— 全零 / 满幅 / 直流 / Nyquist / 单点脉冲 / 白噪声
#   B 满幅扫描 —— 直流电平和每段增益从 0 扫到 16 位最大，查有没有绕回（正数变负数）
#   C 块长     —— 1 / 2 / 17 / 157 / 1024 / 8192，同一个输入结果必须逐位相同
#   D 参数边界 —— 阈值 / 压缩比 / 增益 / 起停时间取到 16 位两端（含负数）
#   E 状态     —— reset 的确定性、跨块状态保持、反复 reset
#
# 判据分两种：
#   硬判据 —— 直通逐位、块长一致、reset 一致、单调。这些必须完全相等，差 1 个 LSB 也算失败。
#   软判据 —— A 组只要求"跑完 + 有界 + 符号正确"。极端输入下没有独立参考可以逐位对，
#             强行定一个逐位判据只会变成"拿实现证明实现"。
#
# 每一组都单独 try/except：某一组炸了不影响后面的组，
# 而且"炸了"本身就是这张表要记录的结果。
#
# 用法（板上，要 root）：
#   echo xilinx | sudo -S env XILINX_XRT=/usr \
#     /usr/local/share/pynq-venv/bin/python3 robustness.py
#
# 产物：/home/xilinx/jupyter_notebooks/robustness.json

import json
import os
import sys
import time

import numpy as np
from pynq import Overlay

from fir_core import FirMultiband, GAIN_UNITY, ms_to_coef

BITFILE = "fir.bit"
FS = 48000.0
OUT_JSON = "/home/xilinx/jupyter_notebooks/robustness.json"
CHUNK = int(os.environ.get("CHUNK", "8000"))
DELAY = 96
N_BANDS = 4

N_1S = int(FS)          # 1 秒
N_SHORT = 4800          # 0.1 秒，扫描用

ok_all = True
result = {"bit_md5": None, "groups": []}


def check(name, passed, detail=""):
    global ok_all
    ok_all = ok_all and passed
    print("  [%s] %s%s" % ("OK  " if passed else "FAIL", name,
                           ("  " + detail) if detail else ""))
    return passed


def to_i16(x):
    return np.clip(np.round(np.asarray(x) * 32768.0), -32768, 32767).astype(np.int16)


def proc(x, **kw):
    """跑一遍。默认 reset=True（每个用例互不影响）。"""
    x = np.asarray(x, dtype=np.int16)
    chunk = kw.pop("chunk", CHUNK)
    y, dt, blocks = core.process(x, reset=kw.pop("reset", True),
                                 chunk=chunk, **kw)
    return y, dt, blocks


def peak(x):
    return float(np.max(np.abs(np.asarray(x, dtype=np.float64)))) if len(x) else 0.0


def db(v):
    return 20.0 * np.log10(v) if v > 0 else -float("inf")


def shifted(x):
    """输入延后 DELAY 拍的参考（直通路径的唯一正确答案）。"""
    x = np.asarray(x, dtype=np.int16)
    return np.concatenate([np.zeros(DELAY, np.int16), x[:-DELAY]])


# =================== 开工 ===================
print("=" * 78)
print("鲁棒性测试")
print("=" * 78)

print("\n0) 加载 %s" % BITFILE)
ol = Overlay(BITFILE)
if "fir_multiband_0" not in ol.ip_dict:
    print("ERROR: overlay 里没有 fir_multiband_0")
    sys.exit(1)
core = FirMultiband(ol.fir_multiband_0.mmio)
try:
    import hashlib
    with open(BITFILE, "rb") as f:
        result["bit_md5"] = hashlib.md5(f.read()).hexdigest()
    print("   bit md5 %s" % result["bit_md5"])
except Exception as e:
    print("   (算 md5 失败：%s)" % e)


# ---------------- A. 输入边界 ----------------
print("\n" + "-" * 78)
print("A) 输入边界：最极端的几种波形，直通必须逐位正确，压缩必须不崩")


def dc(n, v):
    return np.full(n, v, dtype=np.int16)


def sine(n, f, amp):
    t = np.arange(n) / FS
    return np.clip(np.round(amp * np.sin(2 * np.pi * f * t)), -32768, 32767).astype(np.int16)


def square(n, f, amp):
    t = np.arange(n) / FS
    return np.where(np.sin(2 * np.pi * f * t) >= 0, amp, -amp).astype(np.int16)


N = N_1S
rng = np.random.default_rng(20261023)
cases = [
    ("全零", np.zeros(N, np.int16), None),
    ("单点脉冲 +32767", np.concatenate([np.zeros(100, np.int16),
                                        np.array([32767], np.int16),
                                        np.zeros(N - 101, np.int16)]), None),
    ("满幅方波 ±32767 @1kHz", square(N, 1000.0, 32767), None),
    ("0 dBFS 正弦 @1kHz", sine(N, 1000.0, 32767.0), None),
    ("+32767 直流", dc(N, 32767), +1),
    ("-32768 直流", dc(N, -32768), -1),
    ("正弦 +0.25 直流偏置", np.clip(sine(N, 1000.0, 9000.0).astype(np.int32) + 8192,
                                  -32768, 32767).astype(np.int16), None),
    ("Nyquist +20000/-20000", np.where(np.arange(N) % 2 == 0, 20000, -20000).astype(np.int16), None),
    ("1 Hz 正弦（最慢摆动）", sine(N, 1.0, 20000.0), None),
    ("满幅白噪声", rng.integers(-32768, 32768, N, dtype=np.int64).astype(np.int16), None),
]

a_rows = []
for name, x, sign in cases:
    try:
        t0 = time.time()
        yby, _, _ = proc(x, bypass=True)
        ref = shifted(x)
        exact_bad = int(np.count_nonzero(yby != ref))

        ycp, _, blocks = proc(x)
        pk = peak(ycp)
        nz = int(np.count_nonzero(ycp))
        sign_ok = True
        if sign is not None:
            tail = ycp[200:].astype(np.int32)
            sign_ok = bool(np.all(tail * sign >= 0))
        # 非零输入必须真的吐出东西：整段静音是"没跑"的一种表现
        alive = (nz > 0) if name != "全零" else (nz == 0)

        ok = check("A %-24s 直通逐位" % name, exact_bad == 0,
                   "错 %d / %d 个采样" % (exact_bad, len(x)))
        ok &= check("A %-24s 有输出/不崩/符号对" % name,
                    alive and sign_ok,
                    "峰值 %.0f，非零 %d%s" % (pk, nz, "" if sign_ok else "，符号翻了"))
        a_rows.append({"case": name, "bypass_bad": exact_bad,
                       "out_peak": pk, "sign_ok": sign_ok,
                       "blocks": len(blocks), "sec": round(time.time() - t0, 2)})
        ok_all = ok_all and ok
    except Exception as e:
        check("A %-24s" % name, False, "抛异常：%r" % (e,))
        a_rows.append({"case": name, "error": repr(e)})
result["groups"].append({"group": "A_input_edge", "rows": a_rows})


# ---------------- B. 满幅扫描：查绕回 ----------------
print("\n" + "-" * 78)
print("B) 满幅扫描：输出绝不能绕回（正输入出负数）")

# B1 直流电平：单调不减
print("\n   ① 直流电平 0 → 32767（压缩开，默认参数）")
dc_levels = [0, 1000, 2000, 4000, 8000, 12000, 16000, 20000, 24000, 28000, 32000, 32767]
prev = None
mono = True
b1 = []
for lv in dc_levels:
    y, _, _ = proc(dc(N_SHORT, lv))
    v = float(np.median(y[-1000:]))
    if prev is not None and v < prev - 0.5:
        mono = False
    prev = v
    b1.append({"in": lv, "out": v})
    print("      %6d -> %9.1f" % (lv, v))
vals = [r["out"] for r in b1]
check("B1 直流扫描单调不减", mono, "最大 %.0f，最小 %.0f" % (max(vals), min(vals)))
check("B1 满幅直流没有绕回", vals[-1] > 0, "32767 输入 → %.1f" % vals[-1])
result["groups"].append({"group": "B1_dc_sweep", "rows": b1})

# B2 增益：峰值单调不减，且不超量程
print("\n   ② 每段增益 0 → 32767（0.5 正弦，查是否饱和/绕回）")
gains = [0, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32767]
xq = sine(N_SHORT, 1000.0, 16384.0)
pg = None
gmono = True
b2 = []
for g in gains:
    y, _, _ = proc(xq, gain=(g,) * N_BANDS)
    v = peak(y)
    if pg is not None and v < pg - 0.5:
        gmono = False
    pg = v
    b2.append({"gain_raw": g, "out_peak": v})
    print("      增益 %6d (Q7.8 %+7.2f dB) -> 峰值 %7.1f%s"
          % (g, db(g / float(GAIN_UNITY)), v, "  饱和" if v >= 32767 else ""))
gv = [r["out_peak"] for r in b2]
check("B2 增益扫描单调不减", gmono, "最大 %.0f" % max(gv))
check("B2 最大增益时输出饱和在满量程，没有绕回", gv[-1] >= 32767 and gv[-1] <= 32768,
      "峰值 %.0f" % gv[-1])
result["groups"].append({"group": "B2_gain_sweep", "rows": b2})

# B3 负增益
print("\n   ③ 负增益（反相）")
y_neg, _, _ = proc(xq, gain=(-GAIN_UNITY,) * N_BANDS)
pb = peak(y_neg)
y_pos, _, _ = proc(xq, gain=(GAIN_UNITY,) * N_BANDS)
# 增益 -1 和 +1 应当互为反相（芯里输出级有截断，允许 2 个 LSB）
# 用同一段信号的峰值比，避免依赖相位
check("B3 增益 -1.0 输出与 +1.0 等幅", abs(pb - peak(y_pos)) <= 2,
      "%.0f vs %.0f" % (pb, peak(y_pos)))
result["groups"].append({"group": "B3_neg_gain",
                         "rows": [{"neg_peak": pb, "pos_peak": peak(y_pos)}]})


# ---------------- C. 块长 ----------------
print("\n" + "-" * 78)
print("C) 块长：同一段输入，不同切法必须逐位相同")

xc = sine(4096, 700.0, 12000.0)
try:
    ref_y, _, _ = proc(xc, chunk=8192, thr=(983,) * N_BANDS, ratio=(16384,) * N_BANDS,
                       attack=(5.0,) * N_BANDS, release=(100.0,) * N_BANDS)
    c_rows = []
    for ch in (1, 2, 17, 64, 157, 1024, 8192):
        t0 = time.time()
        y, _, blocks = proc(xc, chunk=ch, thr=(983,) * N_BANDS, ratio=(16384,) * N_BANDS,
                            attack=(5.0,) * N_BANDS, release=(100.0,) * N_BANDS)
        bad = int(np.count_nonzero(y != ref_y))
        c_rows.append({"chunk": ch, "blocks": len(blocks), "mismatch": bad,
                       "sec": round(time.time() - t0, 2)})
        check("C 块长 %5d（%4d 块）" % (ch, len(blocks)), bad == 0,
              "与 8192 比错 %d 个采样" % bad)
    # 超长必须被驱动拒绝，而不是让核去越界。
    # 注意 core.process() 会把 chunk 当成缓冲大小、按信号长度切块，
    # 它自己不会越界；真正挡住超长的是 run()，所以这里直接调 run()。
    raised = False
    try:
        from pynq import allocate
        ib = allocate(shape=(FirMultiband.MAX_LEN + 1,), dtype=np.int16)
        ob = allocate(shape=(FirMultiband.MAX_LEN + 1,), dtype=np.int16)
        try:
            core.run(ib, ob, FirMultiband.MAX_LEN + 1)
        finally:
            del ib, ob
    except ValueError:
        raised = True
    except Exception as e:
        print("      超长抛的是别的异常：%r" % (e,))
    check("C 块长 > %d 被拒绝（ValueError）" % FirMultiband.MAX_LEN, raised)
    result["groups"].append({"group": "C_chunk", "rows": c_rows,
                             "oversize_rejected": raised})
except Exception as e:
    check("C 块长", False, "抛异常：%r" % (e,))


# ---------------- D. 参数边界 ----------------
print("\n" + "-" * 78)
print("D) 参数边界：16 位的两端，包括负数")

xd = sine(N_SHORT, 1000.0, 16384.0)
y_by, _, _ = proc(xd, bypass=True)

d_rows = []


def dcase(name, expect, fn, note=""):
    try:
        y, _, _ = fn()
        val = expect(y)
        check("D %-34s" % name, val[0], val[1] + ("  " + note if note else ""))
        d_rows.append({"case": name[2:].strip(), "passed": bool(val[0]),
                       "detail": val[1]})
    except Exception as e:
        check("D %-34s" % name, False, "抛异常：%r" % (e,))
        d_rows.append({"case": name[2:].strip(), "passed": False, "error": repr(e)})


def alive(y):
    """越界参数只验"核跑完了、有输出"。数值本身按定义就是无意义的。

    注意这个判据是**能失败的**：核没跑起来时输出会整段为 0，
    或者 acc 绕回时峰值仍然有界但符号全乱 —— 前者就被 count_nonzero 抓住。
    """
    return (np.count_nonzero(y) > 0 and peak(y) <= 32768.0,
            "峰值 %.0f，非零 %d" % (peak(y), int(np.count_nonzero(y))))


# D1 阈值 0、压缩比 0 -> 阈值以上全压成 0
dcase("D1 阈0/比0 → 输出全零",
      lambda y: (peak(y[-2000:]) == 0.0, "末段峰值 %.1f" % peak(y[-2000:])),
      lambda: proc(xd, thr=(0,) * N_BANDS, ratio=(0,) * N_BANDS))

# D2 阈值 32767、压缩比 32767 -> 永不触发，应当等于直通
dcase("D2 阈32767/比32767 → 等于直通",
      lambda y: (peak((y.astype(np.int32) - y_by.astype(np.int32))[200:]) <= 2,
                 "与直通最大差 %.0f 个 LSB"
                 % peak((y.astype(np.int32) - y_by.astype(np.int32))[200:])),
      lambda: proc(xd, thr=(32767,) * N_BANDS, ratio=(32767,) * N_BANDS))

# D3 阈值 0、压缩比接近 1 -> 几乎直通
def d3_expect(y):
    d = peak((y.astype(np.int32) - y_by.astype(np.int32))[200:])
    return d <= 8, "与直通最大差 %.0f 个 LSB" % d


dcase("D3 阈0/比32767 → 几乎直通", d3_expect,
      lambda: proc(xd, thr=(0,) * N_BANDS, ratio=(32767,) * N_BANDS))

# D4 负压缩比。两条分支行为不同，都要撞：
#   无记忆 drc()：y = thr + (a-thr)*ratio，ratio=-1 时 y = 2*thr - a —— **反相**
#   WDRC wdrc()：gr = (env-thr)(1-ratio) = 2(env-thr)，衰减量翻倍 —— **压死**
# 两条都不崩、都有界，但都不再有意义。参数的有效范围是 ratio ∈ [0, 1]。
def flip_pct(y):
    ref = shifted(xd)[200:].astype(np.int32)
    yy = y[200:].astype(np.int32)
    big = np.abs(ref) > 1000
    if not np.any(big):
        return 0.0
    return 100.0 * np.count_nonzero(np.sign(yy[big]) != np.sign(ref[big])) / np.count_nonzero(big)


def d4_expect(y):
    ok, det = alive(y)
    return (ok, det + "，与输入反相 %.0f%%" % flip_pct(y))


dcase("D4a 负压缩比（无记忆路径） → 有界",
      d4_expect,
      lambda: proc(xd, thr=(3277,) * N_BANDS, ratio=(-32768,) * N_BANDS))
dcase("D4b 负压缩比（WDRC 路径） → 有界",
      alive,
      lambda: proc(xd, thr=(3277,) * N_BANDS, ratio=(-32768,) * N_BANDS,
                   attack=(5.0,) * N_BANDS, release=(100.0,) * N_BANDS))

# D5 负阈值。thr=-1.0 时 `a <= thr` 永远不成立，全段都走压缩公式
#     y = -1 + (a+1)*0.7 = 0.7a - 0.3 —— 过零点被抬到 -0.3，输出是坏的（主机预演峰值 1.2）。
#     这里只验"不崩"，数值不作要求。
dcase("D5 负阈值(-32768) → 不崩、有界", alive,
      lambda: proc(xd, thr=(-32768,) * N_BANDS, ratio=(22938,) * N_BANDS))

# D6 起停时间取最快（0.001 ms → 步进 32767）：应当≈无记忆
#     ⚠️ 对照必须用**同一组**阈值/压缩比。用默认参数比会差好几个 dB，
#        那不是核的问题，是两条曲线本来就不一样（第 3 项里栽过同一个坑）。
def d6_expect(y):
    y_mem, _, _ = proc(xd, thr=(983,) * N_BANDS, ratio=(16384,) * N_BANDS)
    p1 = peak(y[-4800:].astype(np.float64))
    p2 = peak(y_mem[-4800:].astype(np.float64))
    d = abs(db(p1) - db(p2))
    return d < 0.05, "与无记忆差 %.4f dB" % d


print("      （ms_to_coef(0.001) = %d，已顶到 Q1.15 上限）" % ms_to_coef(0.001))
dcase("D6 起停 0.001ms（最快） → ≈无记忆", d6_expect,
      lambda: proc(xd, thr=(983,) * N_BANDS, ratio=(16384,) * N_BANDS,
                   attack=(0.001,) * N_BANDS, release=(0.001,) * N_BANDS))

# D7 起停时间取最慢（1e9 ms → 步进 1）
print("      （ms_to_coef(1e9) = %d，已顶到 Q1.15 下限）" % ms_to_coef(1e9))
dcase("D7 起停 1e9ms（最慢） → 不崩、有界", alive,
      lambda: proc(xd, thr=(983,) * N_BANDS, ratio=(16384,) * N_BANDS,
                   attack=(1e9,) * N_BANDS, release=(1e9,) * N_BANDS))

# D8 四段参数全不同
dcase("D8 四段参数各不相同 → 不崩", alive,
      lambda: proc(xd, thr=(0, 983, 16384, 32767), ratio=(16384, 9830, 32767, 0),
                   attack=(1.0, 5.0, 20.0, 100.0), release=(10.0, 50.0, 200.0, 500.0)))

# D9 四段增益全不同，含负数和最大值
dcase("D9 四段增益 0/1/-1/max → 不崩", alive,
      lambda: proc(xd, gain=(0, GAIN_UNITY, -GAIN_UNITY, 32767)))

result["groups"].append({"group": "D_param_edge", "rows": d_rows})


# ---------------- E. 状态 ----------------
print("\n" + "-" * 78)
print("E) 状态：reset 的确定性、跨块保持、反复 reset")

xe = sine(4800, 1000.0, 12000.0)

# E1 reset 后重复跑，必须逐位相同
try:
    y1, _, _ = proc(xe)
    y2, _, _ = proc(xe)
    bad = int(np.count_nonzero(y1 != y2))
    check("E1 同一输入、都 reset → 逐位相同", bad == 0, "错 %d 个采样" % bad)

    # E2 不 reset 接着跑，状态必须真的在传（否则包络/延迟线没保持）
    core.process(xe, reset=True, chunk=CHUNK)
    y3, _, _ = core.process(xe, reset=False, chunk=CHUNK)
    bad_carry = int(np.count_nonzero(y3 != y1))
    check("E2 不 reset 接着跑 → 结果确实不同（状态在传）", bad_carry > 0,
          "相同采样 %d / %d" % (len(xe) - bad_carry, len(xe)))

    # E3 反复 reset：200 次小段，不能挂，末次结果要和第一次相同
    xs = sine(256, 1000.0, 12000.0)
    t0 = time.time()
    first = None
    last = None
    for k in range(200):
        yk, _, _ = proc(xs)
        if k == 0:
            first = yk.copy()
        last = yk
    bad_r = int(np.count_nonzero(first != last))
    dt = time.time() - t0
    check("E3 反复 reset 200 次 → 不挂、末次与首次相同", bad_r == 0,
          "错 %d 个采样，用时 %.1f 秒" % (bad_r, dt))
    result["groups"].append({"group": "E_state", "reset_deterministic": bad == 0,
                             "carry_differs": bad_carry,
                             "repeat_bad": bad_r, "repeat_sec": round(dt, 1)})
except Exception as e:
    check("E 状态", False, "抛异常：%r" % (e,))


# ---------------- 收尾 ----------------
print("\n" + "=" * 78)
print("结论：%s" % ("全过" if ok_all else "有失败项"))
print("=" * 78)
print("说明：A 组的判据是「直通逐位 + 压缩不崩 + 符号不翻」，")
print("      极端输入下没有独立参考可以逐位对压缩输出，那里只验性质不验数值。")

with open(OUT_JSON, "w") as f:
    json.dump(result, f, indent=1, ensure_ascii=False)
print("\n原始数据写到 %s" % OUT_JSON)

sys.exit(0 if ok_all else 1)
