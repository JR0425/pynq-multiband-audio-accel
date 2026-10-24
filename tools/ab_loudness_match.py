"""§3.4.1：用板上录到的那一对 wav 做等响度 A/B。

先在板上跑一趟取回两个 wav（tools/ab_board_run.py），然后这里：
  1. 核对进核 wav 与 data/audio/board_input_48k.wav 逐位相同；
  2. 用整数参考模型在同一输入上重算，与出核 wav 逐位对拍；
  3. 三个文件乘同一个下调量（给补偿留峰值余量），过核输出再整体提
     rms(原始)/rms(过核)，使 03 的整段 RMS 与 01 完全相等。

输出：
  data/audio/ab_20261024_01_original.wav
  data/audio/ab_20261024_02_core.wav
  data/audio/ab_20261024_03_matched.wav
  data/results/ab_loudness_20261024.json
"""

import json
import math
import os
import sys
import wave

import numpy as np

D = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(D, "tools"))
import ref_model_wdrc as M  # noqa: E402

FS = 48000
THR_Q15 = 983        # 0.030，设定 A[0]
RATIO_Q15 = 16384    # 0.50
ATK_MS, REL_MS = 5.0, 100.0
AUD = os.path.join(D, "data", "audio")


def ms_to_coef(ms, fs=FS):
    """同 board/scripts/fir_core.py:ms_to_coef，毫秒 -> 核里的 Q1.15 步进。"""
    return max(1, min(32767, int(round((1.0 - math.exp(-1.0 / (ms * 1e-3 * fs))) * 32768.0))))


ATK_REG = ms_to_coef(ATK_MS)
REL_REG = ms_to_coef(REL_MS)


def read(path):
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        assert w.getframerate() == FS
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.int64)


def write(path, x):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(np.asarray(x, dtype="<i2").tobytes())


def run_core(x, taps=193):
    """与出货核逐位一致的一遍。FIR 用 numpy 卷积（整数精确），DRC 调模型里的 wdrc()。"""
    coeffs, _na = M.load_coeffs(taps)
    n = len(x)
    ylp = [np.convolve(x, np.asarray(c, dtype=np.int64))[:n] for c in coeffs]

    atk, rel = ATK_REG << 7, REL_REG << 7
    thr, ratio = THR_Q15 << 7, RATIO_Q15 << 7
    env = [0] * M.N_BANDS

    out = np.empty(n, dtype=np.int64)
    for i in range(n):
        b0, b1, b2 = int(ylp[0][i]), int(ylp[1][i]), int(ylp[2][i])
        d = (int(x[i - 96]) << 17) if i >= 96 else 0
        band = (b0, b1 - b0, b2 - b1, d - b2)
        acc = 0
        for b in range(M.N_BANDS):
            gb = M.sat24(band[b] >> 10)
            gb = M.sat24((gb * 256) >> 8)
            yv, env[b] = M.wdrc(gb, thr, ratio, atk, rel, env[b])
            acc += yv << 10
        out[i] = M.sat16(acc >> 17)
    return out


def stats(x):
    xf = np.asarray(x, dtype=np.float64)
    rms = float(np.sqrt(np.mean(xf ** 2)))
    peak = float(np.max(np.abs(xf)))
    rdb = 20 * math.log10(rms / 32768.0) if rms > 0 else -math.inf
    pdb = 20 * math.log10(peak / 32768.0) if peak > 0 else -math.inf
    return {"rms_db": round(rdb, 2), "peak_db": round(pdb, 2),
            "crest_db": round(pdb - rdb, 2), "rms": rms, "peak": peak}


def main():
    src = read(os.path.join(AUD, "board_input_48k.wav"))
    x_in = read(os.path.join(AUD, "ab_board_in.wav"))
    y_brd = read(os.path.join(AUD, "ab_board_out.wav"))

    if len(x_in) != len(src) or not np.array_equal(x_in, src):
        raise SystemExit("板上录的进核 wav 与音源不一致")
    print("进核 wav = board_input_48k.wav，%d 点逐位相同" % len(x_in))

    y_mod = run_core(x_in)
    neq = int(np.sum(y_mod != y_brd))
    worst = int(np.max(np.abs(y_mod - y_brd)))
    print("离线模型 vs 板上输出：%d / %d 点不同，最大差 %d LSB"
          % (neq, len(y_brd), worst))
    if worst > 4:
        raise SystemExit("差得太多，先查这段音源是不是同一趟")

    sa, sb = stats(src), stats(y_brd)
    g = sa["rms"] / sb["rms"]
    # 统一下调，使补偿后峰值留 1 dB 余量；01/02/03 乘同一个数，相对关系不变
    t = 10 ** (-1.0 / 20.0) * 32768.0 / (sb["peak"] * g)
    xa = np.round(src * t).astype(np.int64)
    xb = np.round(y_brd * t).astype(np.int64)
    z = np.round(y_brd * g * t).astype(np.int64)
    if np.max(np.abs(z)) > 32767:
        raise SystemExit("补偿后越界")

    sa2, sb2, sc2 = stats(xa), stats(xb), stats(z)
    names = ["ab_20261024_01_original.wav", "ab_20261024_02_core.wav",
             "ab_20261024_03_matched.wav"]
    for n, arr in zip(names, (xa, xb, z)):
        write(os.path.join(AUD, n), arr)

    res = {
        "source": "board_input_48k.wav",
        "seconds": round(len(src) / FS, 2),
        "how": "板上 src=wav 跑一趟，取 fir_live 的 cap_in / cap_out（逐位自检用的那一对）",
        "params": {"thr": THR_Q15 / 32768.0, "ratio_q15": RATIO_Q15,
                   "attack_ms": ATK_MS, "release_ms": REL_MS,
                   "attack_reg": ATK_REG, "release_reg": REL_REG,
                   "band_gain_db": 0.0, "L": 480, "nbuf": 3},
        "checks": {"in_matches_source": True,
                   "out_vs_offline_model_mismatch": neq,
                   "out_vs_offline_model_max_lsb": worst},
        "common_trim_db": round(20 * math.log10(t), 2),
        "makeup_gain_db": round(20 * math.log10(g), 2),
        "original": {k: sa2[k] for k in ("rms_db", "peak_db", "crest_db")},
        "core": {k: sb2[k] for k in ("rms_db", "peak_db", "crest_db")},
        "matched": {k: sc2[k] for k in ("rms_db", "peak_db", "crest_db")},
        "files": names,
    }
    with open(os.path.join(D, "data", "results", "ab_loudness_20261024.json"),
              "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
