"""用出货版 fir.bit 单独验 codec 硬件本身，不碰自写 I2S。

自写 overlay 上放不出声、录到的又只有 -70 dBFS 的噪声，需要先分清是
"codec 硬件/耳机这一侧坏了" 还是 "我们这版 overlay 的问题"。
这是已知能工作的那条路，跑一遍就知道。

用法（板上）：
    echo xilinx | sudo -S env XILINX_XRT=/usr \
        /usr/local/share/pynq-venv/bin/python3 codec_check.py
"""

import numpy as np
from pynq import Overlay

BITS = "/home/xilinx/fir.bit"
FS = 48000.0


def main():
    ol = Overlay(BITS)
    print("  IP：", sorted(ol.ip_dict.keys()))

    name = None
    for k, v in ol.ip_dict.items():
        if "audio_codec_ctrl" in v.get("type", "").lower():
            name = k
            break
    if name is None:
        print("ERROR: 这个 overlay 里没有 audio_codec_ctrl")
        return 1

    a = getattr(ol, name)
    a.configure()
    print("  configure ok，volume=%d" % a.volume)

    a.record(2.0)
    x = np.array(a.buffer, dtype=np.int32) >> 8
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    print("  录音：rms=%.1f  peak=%d  （满量程 %d）"
          % (rms, int(np.max(np.abs(x))), 1 << 23))

    n = int(FS * 4)
    t = np.arange(n * 2) / 2.0 / FS
    y = ((1 << 22) * np.sin(2 * np.pi * 440.0 * t)).astype(np.int32) & 0xFFFFFF
    a.sample_len = n
    a.buffer = y
    a.play()
    print("  放了 4 秒 440 Hz —— 请听")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
