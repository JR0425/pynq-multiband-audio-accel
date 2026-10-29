"""量真实的采样率（帧率）—— 数固定块数，掐墙钟，倒推 clk。

2026-10-27 发现 `dbg2` 的喂字数每秒只涨 32244（按 100 MHz 应该是 97656），
差正好 3 倍。这要么是时钟不对，要么是音源没按每帧两个字在喂。
本脚本从**采集侧**独立量一遍：I2S 收满 N 个字就是 N/2 帧，用墙钟一除即得帧率，
再乘 2048 就是 clk。不依赖任何内部计数器。

    ... rate_probe.py --blocks 100 --frames 480
"""

import argparse
import os
import sys
import time

import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=100)
    ap.add_argument("--frames", type=int, default=480)
    ap.add_argument("--bit", default="i2sdbg.bit")
    args = ap.parse_args()

    os.environ.setdefault("XILINX_XRT", "/usr")
    sys.path.insert(0, "/home/xilinx/dma_exp")
    import dma_audio_probe as P
    from pynq import allocate

    import gc
    gc.disable()

    P.BITFILE = args.bit
    P.HWHFILE = args.bit.replace(".bit", ".hwh")
    dma, ctrl, stat = P.load()
    P.codec_init()
    P.flush_all(ctrl)

    bufs = [allocate(shape=(args.frames * 2,), dtype=np.int32)
            for _ in range(args.blocks)]

    for tag, bits in (("不跑（只有 I2S 时钟）", P.blk_bits(args.frames)),
                      ("跑 + 内置音源", P.CTRL_RUN | (1 << 3) | P.blk_bits(args.frames))):
        P.set_ctrl(ctrl, bits)
        time.sleep(0.3)
        t0 = time.perf_counter()
        for b in bufs:
            P.cap_block(dma, b)
        dt = time.perf_counter() - t0
        words = args.blocks * args.frames * 2
        wps = words / dt
        fps = wps / 2.0
        print("  %-18s %6d 字 / %.3f s = %8.1f 字/s = %8.1f 帧/s  -> clk = %.1f MHz"
              % (tag, words, dt, wps, fps, fps * 2048 / 1e6))
        P.set_ctrl(ctrl, 0)

    print("\n  100 MHz 下应为 97656 字/s、48828 帧/s。差 3 倍就是时钟，差别的数再看流控。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
