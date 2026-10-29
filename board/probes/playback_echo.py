"""不放音给人听，也能判断 DAC 到底有没有出声。

做法：同一时刻让 MM2S 连续放一个单音、S2MM 连续录，录完在频谱里找那个频率。
板载麦克风和耳机口在同一块板上，DAC 只要真的在驱动输出，就会有电/声泄漏
进 ADC —— 就算耳机没戴也是一样。所以"录音里出现这个频率"= 放音通路通了。

判据看两个数：
    peak_band  目标频率附近的谱峰
    floor      中位数（相当于噪底）
两者差 > 20 dB 就认为确实听到了。

用法（板上，要 root）：

    echo xilinx | sudo -S env XILINX_XRT=/usr \
        /usr/local/share/pynq-venv/bin/python3 playback_echo.py --freq 3000
"""

import argparse
import os
import sys
import time

import numpy as np
from pynq import allocate

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dma_audio_probe import (  # noqa: E402
    CTRL_RUN, DMASR_MM2S, DMASR_S2MM, FS,
    blk_bits, codec_init, flush_all, load, make_tone, set_ctrl, wait_ch,
)


def spectrum_peaks(x, freq, top=5):
    """返回 (目标峰/噪底的 dB 差, 最强几个峰的 (Hz, 相对 dB))。"""
    x = x - np.mean(x)
    w = np.hanning(len(x))
    X = np.abs(np.fft.rfft(x * w))
    f = np.fft.rfftfreq(len(x), 1.0 / FS)

    k = int(np.argmin(np.abs(f - freq)))
    lo, hi = max(0, k - 4), min(len(X), k + 5)
    peak_band = float(X[lo:hi].max())
    floor = float(np.median(X)) or 1e-12
    snr = 20.0 * np.log10(max(peak_band, 1e-12) / floor)

    # 找最强的几个局部峰
    idx = np.argsort(X)[::-1][:60]
    picked = []
    for i in idx:
        if any(abs(i - j) <= 3 for j, _ in picked):
            continue
        picked.append((int(i), float(X[i])))
        if len(picked) >= top:
            break
    tops = [(float(f[i]), 20.0 * np.log10(max(v, 1e-12) / floor))
            for i, v in picked]
    return snr, tops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=480, help="每块帧数（480=10ms）")
    ap.add_argument("--blocks", type=int, default=50, help="总共放几块")
    ap.add_argument("--freq", type=float, default=3000.0, help="放音频率")
    ap.add_argument("--amp", type=float, default=0.5, help="相对满量程的幅度")
    args = ap.parse_args()

    dma, ctrl, stat = load()
    codec_init()
    flush_all(ctrl)
    set_ctrl(ctrl, CTRL_RUN | blk_bits(args.frames))

    nw = args.frames * 2
    tone = make_tone(args.frames, args.freq)
    tone = ((tone.astype(np.int64) * args.amp).astype(np.int32) & 0xFFFFFF)
    tbuf = allocate(shape=(nw,), dtype=np.int32)
    tbuf[:] = tone
    tbuf.flush()

    cbuf = allocate(shape=(nw * args.blocks,), dtype=np.int32)
    cbuf[:] = 0
    cbuf.flush()

    print("  放 %.0f Hz × %d 块（%.2f s），同时录音 %.2f s"
          % (args.freq, args.blocks, args.blocks * args.frames / FS,
             args.blocks * args.frames / FS))

    dma.recvchannel.transfer(cbuf)
    t0 = time.perf_counter()
    for i in range(args.blocks):
        dma.sendchannel.transfer(tbuf)
        wait_ch(dma, DMASR_MM2S, "MM2S")
    wait_ch(dma, DMASR_S2MM, "S2MM", timeout=5.0)
    dt = time.perf_counter() - t0
    cbuf.invalidate()
    set_ctrl(ctrl, 0)

    x = np.array(cbuf, dtype=np.int32) >> 8
    print("  实际用时 %.2f s" % dt)
    print("  录音整体 rms=%.1f peak=%d"
          % (np.sqrt(np.mean(x.astype(np.float64) ** 2)), int(np.max(np.abs(x)))))

    # 麦克风没信号的时候，下面那个"高出噪底多少 dB"会拿一个接近 0 的噪底去除，
    # 算出漂亮的假数字（实测过 +25 dB）。先卡一道，别让它冒充结论。
    ac = {}
    for name, ch in (("左", x[0::2]), ("右", x[1::2])):
        c = ch.astype(np.float64)
        ac[name] = float(np.sqrt(np.mean((c - c.mean()) ** 2)))
    print("  交流分量（去掉直流）rms：左 %.1f  右 %.1f" % (ac["左"], ac["右"]))
    if max(ac.values()) < 50.0:
        print("\n  麦克风这一侧基本没有交流信号。这是这块板子耳机座 U41（TS3A226AE）"
              "的老问题：它只在插头插入的上升沿检测一次然后锁存，上电时耳机已经插着\n"
              "  就一直停在'没接上'。**先把耳机拔出来再插回去**（一次不一定够，"
              "2026-10-09 那次拔到第 35.5 秒电平才跳），用\n"
              "      dma_audio_probe.py --stage b\n"
              "  看录到的交流量起来了，再跑这个测试。本次结论不算。")
        return 2

    ok = False
    for name, ch in (("左", x[0::2]), ("右", x[1::2])):
        snr, tops = spectrum_peaks(ch.astype(np.float64), args.freq)
        print("  %s声道：目标 %.0f Hz 高出噪底 %.1f dB" % (name, args.freq, snr))
        print("    最强 5 个峰：" + "，".join("%.0f Hz(+%.0f dB)" % t for t in tops))
        ok = ok or (snr > 20.0)

    print("\n  %s" % ("DAC 确实在出声（录音里找到了目标频率）"
                     if ok else "没找到目标频率 —— DAC 大概率没输出"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
