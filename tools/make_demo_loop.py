"""生成两段用于"循环播放"演示的 48 kHz / 16 位单声道 WAV。

    python tools/make_demo_loop.py

输出到 data/audio/：
  demo_bands_48k.wav       四段各一个稳态正弦（250/750/1500/4000 Hz），2 s 无缝循环。
                           拖某一段增益时，频谱上对应的那根峰跟着上下走 —— 最直观的演示素材。
  demo_voice_loop_48k.wav  真实语音截 10 s，首尾 20 ms 交叉淡化，循环点不咔哒。
"""

import os
import wave

import numpy as np

FS = 48000
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "audio")


def write_wav(path, x):
    x = np.clip(np.round(x), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(x.tobytes())
    print("%-52s %6.2f s  %7.1f dBFS 峰值" % (
        os.path.basename(path), len(x) / float(FS),
        20 * np.log10(max(1, np.max(np.abs(x.astype(np.int32)))) / 32768.0)))


def bands_loop(sec=2.0):
    # 全部频率都是 1/sec 的整数倍 → 首尾天然接得上，不用淡化
    t = np.arange(int(FS * sec)) / float(FS)
    x = np.zeros_like(t)
    for f, db in [(250, -18), (750, -18), (1500, -18), (4000, -21)]:
        x += 10 ** (db / 20.0) * np.sin(2 * np.pi * f * t)
    return x * 32767.0


def read_wav16(path):
    with wave.open(path, "rb") as w:
        nch, sw, sr, nf = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(nf)
    if sw != 2:
        raise ValueError("只支持 16 位：%s" % path)
    a = np.frombuffer(raw, dtype="<i2").astype(np.float64)
    if nch > 1:
        a = a[0::nch]
    if sr != FS:
        m = int(len(a) * FS / float(sr))
        a = a[np.minimum(len(a) - 1, (np.arange(m) * sr // FS)).astype(int)]
    return a


def seam_loop(x, fade=0.020):
    """把尾巴的 fade 秒融进开头，循环点听不出接缝。"""
    n = int(FS * fade)
    y = x[:len(x) - n].copy()
    r = 0.5 - 0.5 * np.cos(np.pi * np.arange(n) / float(n))
    y[:n] = x[:n] * r + x[len(x) - n:] * (1.0 - r)
    return y


def main():
    if not os.path.isdir(OUT):
        raise SystemExit("找不到 %s" % OUT)
    write_wav(os.path.join(OUT, "demo_bands_48k.wav"), bands_loop())

    src = os.path.join(OUT, "demo_voice_48k.wav")
    if os.path.isfile(src):
        v = read_wav16(src)
        write_wav(os.path.join(OUT, "demo_voice_loop_48k.wav"), seam_loop(v))
    else:
        print("跳过语音循环：没有 %s" % src)


if __name__ == "__main__":
    main()
