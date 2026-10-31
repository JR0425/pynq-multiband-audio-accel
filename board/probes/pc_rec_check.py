# -*- coding: utf-8 -*-
"""T12 电脑侧：录一段，找"啪"那种瞬态。

耳机罩扣在电脑麦克风上，板子播 250 Hz 单音，这里录 60 秒存成 wav，再按
5 ms 一帧算短时能量，找"突然跳起来、随即落下去"的帧。

判读：
  有单次瞬态  → 模拟侧（耳机/插头/功放）真在响，不是数字侧
  整段干净    → 板子输出没有可录到的"啪"，问题在耳机本身、佩戴，或耳朵
  整段都高    → 麦克风增益太大或房间太吵，这轮不算数，调低再录

用法（电脑上，不碰板子）：
    python board/probes/pc_rec_check.py --seconds 60 --out rec_t12.wav
"""
import argparse
import sys
import time

import numpy as np
import sounddevice as sd

FS = 48000


def record(seconds, device=None, fs=FS):
    n = int(seconds * fs)
    print("recording %.1f s ..." % seconds, flush=True)
    r = sd.rec(n, samplerate=fs, channels=2, dtype="float32", device=device)
    sd.wait()
    return r


def frames(x, ms=5.0, fs=FS):
    """返回每帧 rms（dBFS）。"""
    k = max(1, int(ms * 1e-3 * fs))
    m = len(x) // k
    if m == 0:
        return np.array([])
    y = x[: m * k].reshape(m, k)
    rms = np.sqrt((y ** 2).mean(1)) + 1e-12
    return 20 * np.log10(rms)


def find_transients(db, hop_ms=5.0, rise_db=12.0, floor_db=None):
    """一帧比它之前 400 ms 的中位数高 rise_db 以上，且持续不到 50 ms 的，算一次瞬态。"""
    k = int(400 / hop_ms)
    hits = []
    for i in range(len(db)):
        lo = max(0, i - k)
        if i - lo < 10:
            continue
        base = np.median(db[lo:i])
        if db[i] - base < rise_db:
            continue
        if floor_db is not None and db[i] < floor_db:
            continue
        hits.append((i, db[i] - base))
    # 合并相邻帧（同一次"啪"跨 2~3 帧）
    merged = []
    for i, d in hits:
        if merged and i - merged[-1][0] <= 3:
            if d > merged[-1][1]:
                merged[-1] = (i, d)
            continue
        merged.append((i, d))
    return merged


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--seconds", type=float, default=60.0)
    a.add_argument("--out", default="rec_t12.wav")
    a.add_argument("--device", type=int, default=None)
    a.add_argument("--fs", type=int, default=FS)
    a.add_argument("--save", action="store_true", help="把录到的 wav 存下来")
    a.add_argument("--start-delay", type=float, default=0.0,
                   help="按下后等几秒再开录（给板子起播留时间）")
    args = a.parse_args()

    if args.start_delay > 0:
        time.sleep(args.start_delay)
    x = record(args.seconds, args.device, args.fs)
    mono = x.mean(1)

    db = frames(mono, 5.0, args.fs)
    floor = float(np.percentile(db, 20)) if len(db) else -120.0
    hits = find_transients(db, 5.0, 12.0, floor + 12.0)

    print("frames=%d  floor=%.1f dBFS  median=%.1f  max=%.1f"
          % (len(db), floor, float(np.median(db)), float(db.max())))
    print("transients(5ms frame, >12 dB over 400ms median, >floor+12): %d" % len(hits))
    for i, d in hits[:20]:
        t = i * 0.005
        seg = mono[max(0, int((t - 0.05) * args.fs)):int((t + 0.05) * args.fs)]
        if len(seg) < 16:
            continue
        sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        f = np.fft.rfftfreq(len(seg), 1.0 / args.fs)
        # 250 Hz 附近和其余部分各占多少能量
        near = sp[(f > 200) & (f < 300)].sum()
        tot = sp.sum() + 1e-12
        print("  t=%.2fs  +%.1fdB  peak=%.3f  250Hz-share=%.0f%%"
              % (t, d, float(np.abs(seg).max()), 100.0 * near / tot))

    if args.save:
        from scipy.io import wavfile
        wavfile.write(args.out, args.fs, (np.clip(x, -1, 1) * 32767).astype(np.int16))
        print("saved", args.out)


if __name__ == "__main__":
    sys.exit(main())
