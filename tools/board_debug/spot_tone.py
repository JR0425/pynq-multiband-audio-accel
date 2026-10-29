"""最直白的检查：放一段 440 Hz，把放音段和静音段的频谱峰并排打出来。

listen_probe 用的是固定 ±30 Hz 带，一旦采样率对不上（44.1k/48k 换算）
或者音高被拉伸，带内就没有能量，看起来像"不响"。
这个脚本不预设频率，直接把两段里最强的几个分量和它们的频率打出来。

    python spot_tone.py --bit ship
"""

import argparse
import time

import numpy as np
import sounddevice as sd

import listen_probe as LP

PLAY_SECS = 6.0
PRE_SECS = 3.0
POST_SECS = 3.0


def top_peaks(x, fs, fmin=100.0, fmax=3000.0, n=6):
    x = x - x.mean()
    w = np.hanning(len(x))
    X = np.abs(np.fft.rfft(x * w))
    f = np.fft.rfftfreq(len(x), 1.0 / fs)
    m = (f >= fmin) & (f <= fmax)
    Xm, fm = X[m], f[m]
    order = np.argsort(Xm)[::-1]
    picked, out = [], []
    for k in order:
        if all(abs(fm[k] - p) > 15.0 for p in picked):
            picked.append(fm[k])
            out.append((fm[k], Xm[k] / (len(x) / 2.0)))
        if len(out) >= n:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default="ship", choices=list(LP.PLAY))
    args = ap.parse_args()

    dev = sd.query_devices(kind="input")
    fs = int(dev["default_samplerate"])
    print("  设备 %s @ %d Hz" % (dev["name"], fs))

    secs = PRE_SECS + PLAY_SECS + POST_SECS
    rec = sd.rec(int(secs * fs), samplerate=fs, channels=1, dtype="float32")
    time.sleep(PRE_SECS)
    print("  开始放音…")
    rc, o, e = LP.board(LP.PLAY[args.bit])
    sd.wait()
    print("  板子回了：", o.strip().replace("\n", " | ") or e.strip()[:200])

    x = rec[:, 0].astype(np.float64)
    pre = x[: int(PRE_SECS * fs)]
    on = x[int(PRE_SECS * fs): int((PRE_SECS + PLAY_SECS) * fs)]

    for tag, seg in (("静音段", pre), ("放音段", on)):
        print("\n  --- %s  rms=%.5f  peak=%.3f ---" % (tag, np.sqrt((seg ** 2).mean()), np.abs(seg).max()))
        for f, a in top_peaks(seg, fs):
            print("     %7.1f Hz   幅度 %.5f" % (f, a))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
