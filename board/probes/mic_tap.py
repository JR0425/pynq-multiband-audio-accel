"""敲桌子测试 —— 判断 codec 在我们的位流里到底有没有在转换。

判据很直接：真的在转 ADC 的话，敲桌子那一下的 rms 会明显跳起来（几倍到几十倍）；
如果只读到噪声/悬空线，rms 会纹丝不动。

对比方式：同一只话筒、同样敲法，出货位流和我们的位流各跑一遍。

    ... mic_tap.py --mode ship --secs 8
    ... mic_tap.py --mode ours --secs 8
"""

import argparse
import os
import sys
import time

import numpy as np

FS = 48828.125


def sext24(x):
    v = np.asarray(x, dtype=np.int64) & 0xFFFFFF
    return np.where(v >= (1 << 23), v - (1 << 24), v)


def show(tag, x):
    y = sext24(x).astype(np.float64)
    win = int(FS * 0.25)
    n = len(y) // win
    vals = [float(np.sqrt(np.mean(y[i * win:(i + 1) * win] ** 2))) for i in range(n)]
    print("\n  --- %s ---" % tag)
    print("    每 0.25 s 的 rms：")
    for i, v in enumerate(vals):
        bar = "#" * min(60, int(v / max(max(vals), 1e-9) * 60))
        print("      %5.2fs %9.0f  %s" % (i * 0.25, v, bar))
    print("    最大/中位 = %.1f 倍   （真在转换：>3；噪声：≈1）"
          % (max(vals) / (float(np.median(vals)) + 1e-9)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="ship", choices=["ship", "ours"])
    ap.add_argument("--secs", type=float, default=8.0)
    args = ap.parse_args()

    os.environ.setdefault("XILINX_XRT", "/usr")
    sys.path.insert(0, "/home/xilinx/dma_exp")
    import dma_audio_probe as P
    from pynq import allocate

    import gc
    gc.disable()

    if args.mode == "ship":
        from pynq import Overlay
        ol = Overlay("/home/xilinx/fir.bit")
        a = ol.audio_codec_ctrl_0
        a.configure()
        P.codec_init(1)
        a._configured = True
        print("  开录 %.0f 秒 —— 请连续敲桌子" % args.secs)
        sys.stdout.flush()
        a.record(args.secs)
        show("出货 fir.bit", np.array(a.buffer, dtype=np.int32))
        return 0

    P.BITFILE = "i2sdbg.bit"
    P.HWHFILE = "i2sdbg.hwh"
    dma, ctrl, stat = P.load()
    P.codec_init()
    P.flush_all(ctrl)
    P.set_ctrl(ctrl, P.blk_bits(480))
    time.sleep(0.3)
    nblk = int(args.secs * FS / 480)
    bufs = [allocate(shape=(960,), dtype=np.int32) for _ in range(nblk)]
    print("  开录 %.0f 秒 —— 请连续敲桌子" % args.secs)
    sys.stdout.flush()
    for b in bufs:
        P.cap_block(dma, b)
    P.set_ctrl(ctrl, 0)
    st = P.read_stat(stat)
    print("  stat: overrun=%d underrun=%d" % (st["ovf"], st["und"]))
    show("我们的 i2sdbg.bit", np.concatenate([np.array(b, dtype=np.int32) for b in bufs]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
