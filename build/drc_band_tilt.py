"""量压缩前后各频段的能量比变了多少。

听感上报的是"某个频段变亮了"。压缩是**逐频段**做的，四段各自被压的程度不一样，
加起来以后频谱倾斜就会变 —— 这个能直接算出来，不用靠猜。

跑：python build/drc_band_tilt.py
只量，不产出交付物。
"""
import sys
import wave

import numpy as np
import scipy.signal as signal

sys.path.insert(0, "src/python")
from band_design import bands_from_lowpasses, design_lowpasses  # noqa: E402

FS = 48000
N = 193
EDGES = [0, 500, 1000, 2000, 24000]


def drc(band, thr, ratio):
    a = np.abs(band)
    return np.where(a > thr, np.sign(band) * (thr + (a - thr) * ratio), band)


def rms(a):
    return float(np.sqrt(np.mean(a ** 2)))


with wave.open("data/audio/board_input_48k.wav", "rb") as f:
    x = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").astype(np.float64) / 32768.0
print("输入 %d 个采样，有效值 %.4f" % (len(x), rms(x)))

coefs = bands_from_lowpasses(design_lowpasses(N), N)

# 先确认四段确实能还原输入（延迟 96 拍），否则下面量的是别的东西。
_GD = (N - 1) // 2
_b = [signal.lfilter(c, 1.0, x) for c in coefs]
print("四段之和 对 输入延迟 %d 拍：最大差 %.3e（浮点精度）"
      % (_GD, float(np.abs(np.sum(_b, axis=0)[_GD:] - x[:-_GD]).max())))

print("\n注意：四段**不互相正交**（相减式分频，过渡带互相抵消），所以「各段占整体的百分之几」")
print("      这个量没有意义（加不到 100%）。能直接读的是**压缩器对每一段施加了多少 dB 增益**。")

for thr_q, ratio_q, name in [(983, 16384, "4号 0.030/0.50"), (328, 9830, "6号 0.010/0.30")]:
    thr, ratio = thr_q / 32768.0, ratio_q / 32768.0
    dry_b = [signal.lfilter(c, 1.0, x) for c in coefs]
    wet_b = [drc(b, thr, ratio) for b in dry_b]
    g0 = rms(np.sum(dry_b, axis=0)) / rms(np.sum(wet_b, axis=0))

    print("\n%s   响度补回 %.3f 倍（%+.2f dB）" % (name, g0, 20 * np.log10(g0)))
    print("  %-16s %10s %10s %12s %12s" % ("频段", "直通", "压缩后", "压缩器增益", "补回后净变化"))

    gains = []
    for i, (lo, hi) in enumerate(zip(EDGES[:-1], EDGES[1:])):
        d, w = rms(dry_b[i]), rms(wet_b[i])
        g = 20 * np.log10(w / d)                     # 压缩器对这一段的增益（dB）
        net = g + 20 * np.log10(g0)                  # 补回等响度之后的净变化
        gains.append(g)
        print("  %5d-%5d Hz   %10.4f %10.4f %+11.2f dB %+11.2f dB"
              % (lo, hi, d, w, g, net))

    tilt = max(gains) - min(gains)
    print("  → 各段被压的程度不同：压得最轻的一段比压得最狠的一段少降 %.2f dB" % tilt)
    print("     （%d-%d Hz 压得最轻，%d-%d Hz 压得最狠）"
          % (EDGES[gains.index(max(gains))], EDGES[gains.index(max(gains)) + 1],
             EDGES[gains.index(min(gains))], EDGES[gains.index(min(gains)) + 1]))
    print("  → 4 kHz 段相对 0-500 Hz 段：%+.2f dB" % (gains[3] - gains[0]))
