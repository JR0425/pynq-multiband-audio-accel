"""同一口径量两个通路的 ADC 电平 —— 回答"我们这版录到的到底是不是真信号"。

2026-10-27 的记录里有两个对不上的数：出货位流 `rms=13.5M`，我们的 `rms=8.6e4`。
两者**如果都做了 `>>8`**，前者已经超过满量程 2^23 = 8.39M，物理上不可能
—— 所以这两个数一定不是同一个读法。

本脚本不再猜：把原始 int32 字按十六进制打出来，数据落在哪几位一眼就能看出来。

    low-justified（24 位值在低 24 位）  →  0x00ABCDEF
    left-justified（24 位值左移 8 位）  →  0xABCDEF00

然后两种假设下的 dBFS 一起给，谁对得上满量程就是谁。

用法（板上，cwd 在 /home/xilinx/dma_exp）：
    sudo /usr/local/share/pynq-venv/bin/python3 mic_scale_ab.py --mode ship --secs 3
    sudo /usr/local/share/pynq-venv/bin/python3 mic_scale_ab.py --mode dma  --secs 3

两个模式用同一段窗口、同一个 dBFS 公式，输出可以直接并排看。
"""

import argparse
import os
import sys
import time

import numpy as np


FS_ACT = 100e6 / 2048          # 48.828 kHz，两个通路的真实采样率
FS24 = float(1 << 23)          # 24 位满量程


def dbfs(peak, full):
    if peak <= 0:
        return float("-inf")
    return 20.0 * np.log10(peak / full)


def sext24(x):
    """把低 24 位按补码符号扩展成 32 位有符号数。

    出货位流的 buffer 是把 24 位值**零扩展**成 int32 的（实测前 8 个字
    形如 0x00FFE259，峰值恰好 0xFFFFFF，负值占比 0.0%）。不先符号扩展
    就直接算 rms，安静段会被读成 +1.6e7，"接近满量程"就是这么来的。
    """
    v = np.asarray(x, dtype=np.int64) & 0xFFFFFF
    return np.where(v >= (1 << 23), v - (1 << 24), v)


def report(tag, x, fs):
    """x: int32 原始字（不预先移位）"""
    x = np.asarray(x, dtype=np.int32)
    n = x.size
    print("\n  --- %s ---" % tag)
    print("    字数 %d，dtype=%s" % (n, x.dtype))
    print("    前 8 个原始字（hex）：%s" % " ".join("%08X" % (v & 0xFFFFFFFF) for v in x[:8]))

    neg = float(np.mean(x < 0)) * 100.0
    print("    负值占比 %.1f%%（安静时约 50%%；0%% 说明这条通路没有做符号扩展）" % neg)

    cases = (
        ("低24位符号扩展", sext24(x)),
        ("算术 >>8      ", (x >> 8).astype(np.int64)),
        ("原样（不做处理）", x.astype(np.int64)),
    )
    for name, y in cases:
        y = y.astype(np.float64)
        p = float(np.max(np.abs(y)))
        r = float(np.sqrt(np.mean(y ** 2)))
        print("    %s  rms=%12.1f  peak=%10d  peak=%7.1f dBFS"
              % (name, r, p, dbfs(p, FS24)))

    # 逐窗口 rms：敲桌子 / 说话会在某一格里跳起来，纯噪声底则平得很
    nw = 8
    if n >= nw * 8:
        w = np.array_split(sext24(x).astype(np.float64), nw)
        print("    逐窗口 rms（24 位符号扩展）：%s"
              % " ".join("%9.1f" % float(np.sqrt(np.mean(v ** 2))) for v in w))


def mode_ship(secs):
    from pynq import Overlay
    ol = Overlay("/home/xilinx/fir.bit")
    name = None
    for k, v in ol.ip_dict.items():
        if "audio_codec_ctrl" in v.get("type", "").lower():
            name = k
            break
    if name is None:
        print("ERROR: fir.bit 里没有 audio_codec_ctrl")
        return 1
    a = getattr(ol, name)
    a.configure()
    print("  出货位流：configure ok，volume=%d" % a.volume)
    a.record(secs)
    report("出货 fir.bit（pynq.lib.audio 的 buffer）", np.array(a.buffer, dtype=np.int32), FS_ACT)
    return 0


def mode_dma(secs, frames):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import dma_audio_probe as P

    from pynq import allocate
    dma, ctrl, stat = P.load()
    P.codec_init()
    P.flush_all(ctrl)
    P.set_ctrl(ctrl, P.blk_bits(frames))       # run=0，只录音

    nw = frames * 2
    nblk = max(1, int(secs * FS_ACT / frames))
    # 一次一块地分配：cap_block 会对缓冲区调 invalidate()，numpy 的切片视图
    # 没有这个方法，所以不能在大缓冲上切片复用。
    bufs = [allocate(shape=(nw,), dtype=np.int32) for _ in range(nblk)]
    for b in bufs:
        P.cap_block(dma, b)
    P.set_ctrl(ctrl, 0)

    st = P.read_stat(stat)
    print("  stat: overrun=%d underrun=%d cap_lvl=%d" % (st["ovf"], st["und"], st["cap_lvl"]))
    data = np.concatenate([np.array(b, dtype=np.int32) for b in bufs])
    report("我们的 fir_dma（i2s_duplex 的录音 FIFO → S2MM）", data, FS_ACT)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="ship", choices=["ship", "dma"])
    ap.add_argument("--secs", type=float, default=3.0)
    ap.add_argument("--frames", type=int, default=480)
    args = ap.parse_args()

    import gc
    gc.disable()
    t0 = time.time()
    rc = mode_ship(args.secs) if args.mode == "ship" else mode_dma(args.secs, args.frames)
    print("\n  （用时 %.1f s）" % (time.time() - t0))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
