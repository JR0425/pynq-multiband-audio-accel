"""验证 PL 直连位流现在能不能出声（codec_unmute 修好之后）。

默认跑 i2sdbg.bit：最简的 PL 直连设计，音源在 PL 内部，PS 只写控制位。
打印 codec 那四个寄存器，方便确认确实被打开了。

用法（板上，root，cwd /home/xilinx/dma_exp）：
    python3 pl_tone.py --bit i2sdbg.bit --hwh i2sdbg.hwh --secs 8
"""

import argparse
import os
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")
os.environ.setdefault("XILINX_XRT", "/usr")

REG = ((0x401C, "混音左"), (0x401E, "混音右"), (0x4023, "耳机左"), (0x4024, "耳机右"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default="i2sdbg.bit")
    ap.add_argument("--hwh", default="i2sdbg.hwh")
    ap.add_argument("--secs", type=float, default=8.0)
    args = ap.parse_args()

    import dma_audio_probe as P

    P.BITFILE = args.bit
    P.HWHFILE = args.hwh
    dma, ctrl, stat = P.load()
    P.codec_init(1)
    P.flush_all(ctrl)

    print("  codec: " + "  ".join(
        "0x%04X(%s)=0x%02X" % (r, n, P.i2c_rd(r) or 0) for r, n in REG))
    sys.stdout.flush()

    print("TONE_START %s" % time.strftime("%H:%M:%S"))
    sys.stdout.flush()
    P.set_ctrl(ctrl, 1 | (1 << 3))
    time.sleep(args.secs)
    P.set_ctrl(ctrl, 0)
    print("TONE_END %s" % time.strftime("%H:%M:%S"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
