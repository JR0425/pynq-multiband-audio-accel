"""干净的 A/B：同一颗位流、同一套 codec 初始化，只差 GPIO bit0。

上一轮的 A/B 两边初始化不一样（sel=0 走 a.configure()，sel=1 走
codec_init(1) 且多调了 select_mic()），所以"sel=1 不响"这个结论不干净。
这里两边完全相同，只翻 GPIO。

A: sel=0，PS 放 440 Hz wav（官方分支）
B: sel=1，PL 内部 tone_ser 的 440 Hz 方波（±0.25 满量程）

用法（板上，root，cwd /home/xilinx/dma_exp）：
    python3 ipmux_ab.py --secs 5
"""

import argparse
import os
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")
os.environ.setdefault("XILINX_XRT", "/usr")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default="/home/xilinx/dma_exp/fir_ipmux_ila.bit")
    ap.add_argument("--secs", type=float, default=5.0)
    ap.add_argument("--gap", type=float, default=2.0)
    args = ap.parse_args()

    import hearing_tone as HT
    from pynq import MMIO, Overlay

    ol = Overlay(args.bit)
    a = ol.audio_codec_ctrl_0
    a.configure()
    a.set_volume(60)
    g = MMIO(0x43C10000, 0x1000)
    g.write(0, 0)

    HT._write_tone("/dev/shm/ab.wav", 440.0, -1.0, args.secs)
    a.load("/dev/shm/ab.wav")

    print("A_START sel=0 official %s" % time.strftime("%H:%M:%S"))
    sys.stdout.flush()
    a.play()
    print("A_END %s" % time.strftime("%H:%M:%S"))
    sys.stdout.flush()

    time.sleep(args.gap)

    print("B_START sel=1 tone_ser %s" % time.strftime("%H:%M:%S"))
    sys.stdout.flush()
    g.write(0, 1)
    time.sleep(args.secs)
    g.write(0, 0)
    print("B_END %s" % time.strftime("%H:%M:%S"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
