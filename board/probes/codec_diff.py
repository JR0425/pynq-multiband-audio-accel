"""把 codec 寄存器整片 dump 两遍：configure() 之后 vs Audio.play() 正在跑的时候。

D 证明 play() 的效果不止那四条（0x4023/0x4024/0x401C/0x401E），
所以别再猜，直接把 0x4000..0x40FF 读两遍做差分，看 play() 到底还写了什么。

用法（板上，root，cwd /home/xilinx/dma_exp）：
    python3 codec_diff.py
"""

import os
import sys
import threading
import time

sys.path.insert(0, "/home/xilinx/dma_exp")
os.environ.setdefault("XILINX_XRT", "/usr")

SWEEP = range(0x4000, 0x4100)


def dump(P):
    return {r: P.i2c_rd(r) for r in SWEEP}


def main():
    import dma_audio_probe as P
    import hearing_tone as HT
    from pynq import Overlay

    ol = Overlay("/home/xilinx/dma_exp/fir_ipmux_ila.bit")
    a = ol.audio_codec_ctrl_0
    a.configure()
    a.set_volume(60)

    HT._write_tone("/dev/shm/diff.wav", 440.0, -6.0, 8.0)
    a.load("/dev/shm/diff.wav")

    print("dump1 configure() 之后 ...")
    sys.stdout.flush()
    d1 = dump(P)

    print("dump2 play() 正在跑 ...")
    sys.stdout.flush()
    th = threading.Thread(target=a.play)
    th.start()
    time.sleep(1.0)
    d2 = dump(P)
    th.join(timeout=15)

    n = 0
    for r in SWEEP:
        v1, v2 = d1.get(r), d2.get(r)
        if v1 is None or v2 is None or v1 == v2:
            continue
        n += 1
        print("  0x%04X  %02X -> %02X" % (r, v1, v2))
    print("  共 %d 个寄存器被 play() 改了" % n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
