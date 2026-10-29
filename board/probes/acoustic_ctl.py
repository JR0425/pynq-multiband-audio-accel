"""验证"板子自己听自己"这个量法靠不靠得住 —— 用**已知会响**的出货位流做对照。

原理：同一副耳麦，耳机离话筒只有几厘米。放 440 Hz 时话筒里应该能收到这个频率；
不放时收不到。只要这个差异足够明显，就有了一个**不需要人耳**的判据，
后面任何位流响不响都能自己测。

做法：出货 fir.bit 同时开录音和放音（两个 DMA 通道），
比较"放音期间"和"纯录音"两段的 440 Hz 频点能量。

    ... acoustic_ctl.py
"""

import os
import sys
import threading
import time

import numpy as np

os.environ.setdefault("XILINX_XRT", "/usr")
sys.path.insert(0, "/home/xilinx/dma_exp")

FS = 48828.125


def band_peak(x, f0, half=25.0):
    x = np.asarray(x, dtype=np.float64)
    x = x - x.mean()
    n = len(x)
    X = np.abs(np.fft.rfft(x * np.hanning(n)))
    f = np.fft.rfftfreq(n, 1.0 / FS)
    band = (f > f0 - half) & (f < f0 + half)
    ref = (f > 200) & (f < 8000) & ~band
    med = float(np.median(X[ref])) + 1e-12
    return float(X[band].max()) / med, float(f[band][np.argmax(X[band])])


def main():
    import hearing_tone as HT
    from pynq import Overlay

    import gc
    gc.disable()

    ol = Overlay("/home/xilinx/fir.bit")
    a = ol.audio_codec_ctrl_0
    a.configure()
    a.set_volume(60)                 # 开大一点，保证漏音明显

    SECS = 5.0
    HT._write_tone("/dev/shm/ac_tone.wav", 440.0, -6.0, SECS)
    a.load("/dev/shm/ac_tone.wav")

    # --- 第一段：只录（对照） ---
    print("\n  [1/2] 纯录音 %.0f 秒 —— 请不要出声" % SECS)
    sys.stdout.flush()
    a.record(SECS)
    quiet = np.array(a.buffer, dtype=np.int32) >> 8
    q, qf = band_peak(quiet, 440.0)

    # --- 第二段：边放边录 ---
    print("  [2/2] 边放 440 Hz 边录 %.0f 秒" % SECS)
    sys.stdout.flush()
    th = threading.Thread(target=a.play)
    th.start()
    time.sleep(0.2)
    a.record(SECS)
    th.join(timeout=20)
    loud = np.array(a.buffer, dtype=np.int32) >> 8
    l, lf = band_peak(loud, 440.0)

    print("\n  纯录音   440Hz 峰/中位 = %6.2f  (峰在 %.1f Hz)" % (q, qf))
    print("  边放边录 440Hz 峰/中位 = %6.2f  (峰在 %.1f Hz)" % (l, lf))
    print("  差值 = %.1f dB" % (20 * np.log10((l + 1e-12) / (q + 1e-12))))
    print("\n  >10 dB -> 这个量法可用，以后不需要人耳也能判")
    print("  <3 dB  -> 耳机和话筒耦合太弱，考虑把耳机贴到话筒上再试")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
