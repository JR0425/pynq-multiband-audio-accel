"""让板子自己听自己 —— 用麦克风收耳机漏出来的声音，判断 DAC 到底响没响。

为什么这么做：PL 内部一切正常、codec 可写寄存器逐字节相同、引脚有驱动，但耳机
里没声。人耳验一次要人在场，而**同一副耳麦的话筒离耳机只有几厘米**，所以
"放音时话筒里有没有那个频率"可以自己量出来，不用问人。

做法（只有我们这一颗位流，自带音源）：
  1. 配 codec（含 select_mic）；
  2. 静音状态下录 N 块，算频谱，记下 440 Hz 附近的底；
  3. 开内置音源（440 Hz 方波，-12 dBFS）再录 N 块，算同一频点；
  4. 报两者之比。

判据：
  放音时 440 Hz 明显高出静音（>10 dB）  →  DAC 在响，问题在耳机/佩戴
  两者差不多                         →  这块板子没耦合上，结论无效
                                        （换：把耳机贴到话筒上再跑一次）

用法（板上，root，cwd 在 /home/xilinx/dma_exp）：
    ... echo_test.py --secs 3
"""

import argparse
import os
import sys
import time

import numpy as np


def capture(P, dma, ctrl, stat, frames, nblk):
    from pynq import allocate
    bufs = [allocate(shape=(frames * 2,), dtype=np.int32) for _ in range(nblk)]
    for b in bufs:
        P.cap_block(dma, b)
    # i2s_duplex 收侧写的是 {{8{rl[23]}}, rl}，即已经做过 24→32 位符号扩展，
    # 所以这里直接当有符号数用，不要再 & 0xFFFFFF（那会变成无符号，凭空造出一个方波）
    return np.concatenate([np.array(b, dtype=np.int32) for b in bufs])


def band_peak(x, fs, f0, half=25.0):
    """f0 附近的最大幅度 / 200Hz~8kHz 的中位幅度 —— 无量纲，越大越像"有那个音"。"""
    n = len(x)
    w = np.hanning(n)
    X = np.abs(np.fft.rfft(x.astype(np.float64) / (1 << 23) * w))
    f = np.fft.rfftfreq(n, 1.0 / fs)
    band = (f > f0 - half) & (f < f0 + half)
    ref = (f > 200) & (f < 8000) & ~((f > f0 - half) & (f < f0 + half))
    med = float(np.median(X[ref])) + 1e-12
    pk = float(X[band].max())
    return pk / med, float(f[band][np.argmax(X[band])])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=3.0)
    ap.add_argument("--frames", type=int, default=480)
    ap.add_argument("--tone", type=float, default=440.0,
                    help="内置音源的基频（RTL 里写死 440.5，这里只用来找峰）")
    args = ap.parse_args()

    os.environ.setdefault("XILINX_XRT", "/usr")
    import dma_audio_probe as P

    import gc
    gc.disable()

    P.BITFILE = "i2sdbg.bit"
    P.HWHFILE = "i2sdbg.hwh"
    dma, ctrl, stat = P.load()
    P.codec_init()
    P.flush_all(ctrl)

    fs = P.FS_ACT
    nblk = max(4, int(args.secs * fs / args.frames))

    # 静音：只录音，不发
    P.set_ctrl(ctrl, P.blk_bits(args.frames))
    time.sleep(0.3)
    quiet = capture(P, dma, ctrl, stat, args.frames, nblk)
    q_db, q_f = band_peak(quiet, fs, args.tone)

    # 放音 + 录音
    P.set_ctrl(ctrl, P.CTRL_RUN | (1 << 3) | P.blk_bits(args.frames))
    time.sleep(0.3)
    loud = capture(P, dma, ctrl, stat, args.frames, nblk)
    l_db, l_f = band_peak(loud, fs, args.tone)
    st = P.read_stat(stat)
    P.set_ctrl(ctrl, 0)

    print("\n  采样 %d 点 / 每档 %.1f s   真实 fs = %.1f Hz" % (len(quiet), nblk * args.frames / fs, fs))
    print("  静音  440Hz 附近：峰/中位 = %6.2f  (最大在 %.1f Hz)   rms=%d"
          % (q_db, q_f, int(np.sqrt(np.mean(quiet.astype(np.float64) ** 2)))))
    print("  放音  440Hz 附近：峰/中位 = %6.2f  (最大在 %.1f Hz)   rms=%d"
          % (l_db, l_f, int(np.sqrt(np.mean(loud.astype(np.float64) ** 2)))))
    print("  放音/静音 = %.1f dB" % (20 * np.log10((l_db + 1e-12) / (q_db + 1e-12))))
    print("  stat: overrun=%d underrun=%d" % (st["ovf"], st["und"]))
    print("\n  >10 dB -> 耳机确实在响（或者至少 DAC 在动）；<3 dB -> 没耦合上，"
          "把耳机贴到话筒上再跑一次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
