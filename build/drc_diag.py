"""诊断：这套压缩到底把动态范围压窄了多少？换素材救不救得回来？

只做测量，不产生交付物。跑：
    python build/drc_diag.py
"""
import sys
import wave

import numpy as np
import scipy.signal as signal

sys.path.insert(0, "src/python")
from band_design import bands_from_lowpasses, design_lowpasses, group_delay  # noqa: E402

FS = 48000
N = 193
SWEEP = [(3277, 22938, "1号 0.100/0.70"), (1638, 22938, "2号 0.050/0.70"),
         (983, 22938, "3号 0.030/0.70"), (983, 16384, "4号 0.030/0.50"),
         (656, 13107, "5号 0.020/0.40"), (328, 9830, "6号 0.010/0.30")]


def drc(band, thr, ratio):
    a = np.abs(band)
    return np.where(a > thr, np.sign(band) * (thr + (a - thr) * ratio), band)


def frames_rms(x, w=0.020):
    W = int(FS * w)
    return np.array([np.sqrt(np.mean(x[i:i + W] ** 2))
                     for i in range(0, len(x) - W, W)])


def dyn_range(x):
    r = frames_rms(x)
    r = r[r > 0]
    return 20 * np.log10(np.percentile(r, 95) / np.percentile(r, 5))


def crest(x):
    x = np.asarray(x, dtype=np.float64)
    return float(np.abs(x).max() / np.sqrt(np.mean(x ** 2)))


def load(path):
    with wave.open(path, "rb") as f:
        d = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2")
    return d.astype(np.float64) / 32768.0


def run(x, coefs, thr_f, ratio, d):
    """返回 4 段压缩后相加的结果（浮点，未归一化）。"""
    total = np.zeros_like(x)
    for c in coefs:
        b = signal.lfilter(c, 1.0, x)
        total += drc(b, thr_f, ratio)
    return total


def match(y, x):
    """把 y 的整体响度补到和 x 一样 —— 对应听的时候的『等响度补回』。"""
    ry = np.sqrt(np.mean(y ** 2))
    rx = np.sqrt(np.mean(x ** 2))
    return y * (rx / ry) if ry else y


def report(tag, x, y_raw):
    y = match(y_raw, x)
    dx, dy = dyn_range(x), dyn_range(y)
    print("  %-22s 动态范围 %5.1f dB → %5.1f dB   压窄 %4.1f dB ｜ "
          "峰均比 %.2f → %.2f（%.2f dB）"
          % (tag, dx, dy, dx - dy, crest(x), crest(y),
             20 * np.log10(crest(x) / crest(y))))
    return dx - dy


coefs = bands_from_lowpasses(design_lowpasses(N), N)
D = group_delay(N)

x0 = load("data/audio/board_input_48k.wav")
# 板子上把增益调成 1.0000，所以这里也归一到同样电平
x0 = x0 * (0.15 * 32767 / np.sqrt(np.mean(x0 ** 2)) / 32768.0)

print("=" * 78)
print("标的：动态范围被压窄多少 dB（< 1 dB 人耳基本分不出）")
print("=" * 78)

print("\n【一】现在的素材（真实语音，音量起伏自然）")
print("  直通（不压缩）动态范围 = %.1f dB" % dyn_range(x0))
for thr, ratio, name in SWEEP:
    report(name, x0, run(x0, coefs, thr / 32768.0, ratio / 32768.0, D))

print("\n【二】把素材的起伏人为放大（模拟『语调/力度更有变化』的录音）")
for exp_db in (6, 12, 20):
    t = np.arange(len(x0)) / FS
    env = 10 ** ((exp_db / 2) * np.sin(2 * np.pi * 0.8 * t) / 20.0)
    x2 = x0 * env
    x2 = x2 * (np.sqrt(np.mean(x0 ** 2)) / np.sqrt(np.mean(x2 ** 2)))
    print("  —— 起伏放大 ±%.0f dB（动态范围 %.1f dB）" % (exp_db, dyn_range(x2)))
    report("4号 0.030/0.50", x2, run(x2, coefs, 983 / 32768.0, 16384 / 32768.0, D))
    report("6号 0.010/0.30", x2, run(x2, coefs, 328 / 32768.0, 9830 / 32768.0, D))

print("\n【三】一段『响度台阶』测试信号（每 1 秒降 8 dB，从 0 到 -32 dB）")
LV = [0, -8, -16, -24, -32]
n = FS * len(LV)
t = np.arange(n) / FS
step = np.repeat(10 ** (np.array(LV, dtype=float) / 20.0), FS)
tone = step * np.sin(2 * np.pi * 500 * t)
for thr, ratio, name in [(983, 16384, "4号 0.030/0.50"), (328, 9830, "6号 0.010/0.30")]:
    y = run(tone, coefs, thr / 32768.0, ratio / 32768.0, D)
    y = match(y, tone)
    print("  %s（响度已补回）" % name)
    print("    输入各档有效值：", "  ".join("%.4f" % v for v in
          [np.sqrt(np.mean((tone[i * FS:(i + 1) * FS]) ** 2)) for i in range(len(LV))]))
    print("    输出各档有效值：", "  ".join("%.4f" % v for v in
          [np.sqrt(np.mean((y[i * FS:(i + 1) * FS]) ** 2)) for i in range(len(LV))]))
    print("    相对输入抬了多少 dB：", "  ".join("%+.1f" % v for v in
          [20 * np.log10(np.sqrt(np.mean((y[i * FS:(i + 1) * FS]) ** 2)) /
                         np.sqrt(np.mean((tone[i * FS:(i + 1) * FS]) ** 2)))
           for i in range(len(LV))]))
