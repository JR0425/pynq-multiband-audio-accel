# -*- coding: utf-8 -*-
"""声学延迟：耳机贴着板载话筒，量一圈"放出去 → 收回来"。

为什么要有这个
==============
数字法（`LAT_TRACE`）只能量到"交给 codec"为止，量不到模拟端：
DAC 的重建滤波、耳机、空气、话筒、ADC 的抗混叠，这几段都不在数字通路里。
这个脚本补的就是这一段。

量出来的数怎么用
================
    嘴 → 耳 总延迟 = 数字延迟（L=480/NBUF=3 是 29.8 ms）+ 这里量到的 R

因为 R 正好是"DAC 模拟端 + 空气 + ADC 模拟端"，而数字延迟是从
"采样被采到"到"交给 codec"。两段首尾相接，加起来才是完整的链路。

（前提：耳机和话筒之间几乎没有空气距离，所以 R 里没有声速那一项。）

怎么跑（板上）
==============
    cd /home/xilinx/jupyter_notebooks
    echo xilinx | sudo -S env XILINX_XRT=/usr \
        /usr/local/share/pynq-venv/bin/python3 acoustic_lat.py

要人在旁边做一件事：把耳机那个耳塞**贴住板上那个话筒孔**，按住别动。
脚本会依次用三档音量放同一段扫频，挑一档既不削顶也不太小来算延迟。
原始波形写到 /tmp/acoustic_lat.npz，算完可以拉回主机复核。

⚠️ 只在"耳机确实插在 HP+MIC 口、话筒电平已经跳起来"之后跑。
   U41 那个坑：插头要插到底，插入检测只在上升沿做一次，实测要等到第 18 秒左右。
   脚本开头会读 0.3 秒环境声；电平贴近 0 会提示话筒没通（只是提示，不停）。

房间吵没关系：每档连发 REPS 次扫频，对齐后同步平均，随机噪声按 √REPS 衰减，
回波是相干相加。要的只是"贴住话筒 + 别在两次扫频之间挪动"。
"""

import os
import sys
import time

import numpy as np
from pynq import Overlay

FS = 48000
L = 480                      # 10 ms 一块
VOLUME = int(os.environ.get("LIVE_VOLUME", "40"))
HERE = os.path.dirname(os.path.abspath(__file__))

T_LEAD = 0.20                # 扫频前留白
T_CHIRP = 0.060              # 扫频长度
REPS = 6                     # 每档连发几次再平均（房间不安静也不怕）
T_GAP = 0.25                 # 相邻两次扫频起点的间隔
T_TAIL = 0.30                # 最后一次之后留白
# 三档音量（满量程 = 1.0）。耳机贴着话筒时增益很大，三档全削顶就调小重跑：
#   ACOUSTIC_AMPS="0.001,0.003,0.01"
AMPS = tuple(float(x) for x in os.environ.get(
    "ACOUSTIC_AMPS", "0.01,0.03,0.10").split(","))
F0, F1 = 500.0, 6000.0
SEARCH = 0.15                # 在发出后多少秒之内找回波

_D24_HALF = 1 << 23
_D24_FULL = 1 << 24


def hr(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def adc_i16(buf, ch=0):
    """24 位原始缓冲区 → int16，带符号扩展。

    ⚠️ 低 24 位是补码且**没有符号扩展**，不还原就会把静音读成满量程
    （2026-10-13 栽过这个）。这里和 fir_live.py 的 adc_to_i16 同一套算法，
    但不做自适应直流跟踪 —— 相关性分析前会统一减均值。
    """
    u = buf.reshape(-1, 2)[:, ch].astype(np.int64) & 0xFFFFFF
    s = np.where(u >= _D24_HALF, u - _D24_FULL, u)
    return np.clip(np.rint(s.astype(np.float64) / 256.0),
                   -32768.0, 32767.0).astype(np.int16)


def chirp(n, amp):
    """线性扫频 + 半汉宁窗包络（两端归零，避免起止的咔哒声干扰定位）。"""
    t = np.arange(n) / FS
    k = (F1 - F0) / T_CHIRP
    ph = 2 * np.pi * (F0 * t + 0.5 * k * t * t)
    w = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / (n - 1))
    return (amp * 32767.0 * np.sin(ph) * w).astype(np.int16)


def build_tx(amp):
    """一档里连发 REPS 次同样的扫频，间隔 T_GAP。"""
    n = int((T_LEAD + (REPS - 1) * T_GAP + T_CHIRP + T_TAIL) * FS)
    tx = np.zeros(n, dtype=np.int16)
    c = chirp(int(T_CHIRP * FS), amp)
    for r in range(REPS):
        i = int((T_LEAD + r * T_GAP) * FS)
        tx[i:i + len(c)] = c
    return tx


def starts_of():
    return [int((T_LEAD + r * T_GAP) * FS) for r in range(REPS)]


def i32_stereo(x16):
    v = x16.astype(np.int32) << 8
    out = np.empty(v.size * 2, dtype=np.int32)
    out[0::2] = v
    out[1::2] = v
    return out


def main():
    hr("0. 加载 overlay 和音频通路")
    bit = os.path.join(HERE, "fir.bit")
    so = os.path.join(HERE, "libaudio_stream.so")
    for p in (bit, so):
        if not os.path.isfile(p):
            raise SystemExit("找不到 %s" % p)

    ol = Overlay(bit)
    audio = ol.audio_codec_ctrl_0
    audio.configure()
    audio.select_microphone()   # 不切这一下,ADC 抓的是线路输入,读数恒为 0.7 LSB
    audio.set_volume(VOLUME)

    ffi = audio._ffi
    mmio_len, uio, iic = audio.mmio.length, audio.uio_index, audio.iic_index
    ffi.cdef("""
        void* stream_begin(unsigned int mmap_size, unsigned int volume,
                           int uio_index, int iic_index);
        int   duplex_block(void* h, unsigned int* in_buf,
                           unsigned int* out_buf, unsigned int nsamples);
        void  stream_end(void* h);
    """)
    lib = ffi.dlopen(so)
    h = lib.stream_begin(mmio_len, VOLUME, uio, iic)
    if h == ffi.NULL:
        raise SystemExit("stream_begin 失败")
    print("   音量 %d，uio=%d iic=%d" % (VOLUME, uio, iic))

    abuf_in = np.zeros(L * 2, dtype=np.int32)
    abuf_out = np.zeros(L * 2, dtype=np.int32)
    pin = ffi.cast("unsigned int*", ffi.from_buffer(abuf_in))
    pout = ffi.cast("unsigned int*", ffi.from_buffer(abuf_out))

    def run(tx16):
        """把 tx16 一帧一帧放出去，同时把话筒收到的存下来。"""
        rx = np.zeros_like(tx16)
        nblk = len(tx16) // L
        out32 = i32_stereo(tx16)
        for b in range(nblk):
            abuf_out[:] = out32[b * L * 2:(b + 1) * L * 2]
            got = lib.duplex_block(h, pin, pout, L)
            if got != L:
                print("   ⚠️ duplex 只搬了 %d/%d" % (got, L))
            rx[b * L:(b + 1) * L] = adc_i16(abuf_in)
        return rx

    # 先读一段环境声，确认话筒是活的（U41 没锁上时这里会贴 0）
    hr("1. 环境声检查（房间吵没关系，这里只看话筒通没通）")
    quiet = run(np.zeros(int(0.3 * FS), dtype=np.int16))
    q_rms = float(np.sqrt(np.mean(quiet.astype(np.float64) ** 2)))
    print("   环境声 rms = %.1f LSB" % q_rms)
    if q_rms < 10.0:
        print("   ⚠️ 电平太低，话筒可能没通。确认耳机插到底、插到电平跳起来"
              "（约 18 秒）再跑。")
        print("   仍然继续，但结果可能不可用。")

    win = int((T_CHIRP + SEARCH) * FS)
    starts = starts_of()
    seg = chirp(int(T_CHIRP * FS), 1.0).astype(np.float64)

    trials = []
    for amp in AMPS:
        hr("2. 试第 %d 档：幅度 %.0f%% 满量程（连发 %d 次）"
           % (AMPS.index(amp) + 1, amp * 100, REPS))
        tx = build_tx(amp)
        time.sleep(0.15)
        rx = run(tx)
        peak = int(np.max(np.abs(rx.astype(np.int32))))
        clipped = peak >= 32700
        x = rx.astype(np.float64)

        # **同步平均**：每次扫频之后的观察窗对齐后相加。
        # 回波在窗里的位置固定，所以相加是相干的；房间里的随机噪声互不相干，
        # 平均 REPS 次之后按 √REPS 衰减。房间吵不吵只影响噪声底，不改峰值位置。
        acc = np.zeros(win)
        for s in starts:
            acc += x[s:s + win]
        acc /= REPS
        acc -= acc.mean()

        n = 1
        while n < win + len(seg):
            n <<= 1
        cc = np.fft.irfft(np.fft.rfft(acc, n) * np.conj(np.fft.rfft(seg, n)), n)
        seg_cc = cc[:win]
        k = int(np.argmax(seg_cc))
        delay = k / FS * 1000.0
        sharp = float(seg_cc.max() / (np.sqrt(np.mean(seg_cc ** 2)) + 1e-9))
        print("   峰值 %d%s，回环延迟 %.2f ms，峰/底 %.1f"
              % (peak, "（削顶，本档作废）" if clipped else "", delay, sharp))
        trials.append((amp, peak, clipped, delay, sharp, rx))

    np.savez("/tmp/acoustic_lat.npz",
             amps=np.array([t[0] for t in trials]),
             peaks=np.array([t[1] for t in trials]),
             delays=np.array([t[3] for t in trials]),
             **{"rx%d" % i: t[5] for i, t in enumerate(trials)})
    print("\n   原始波形 -> /tmp/acoustic_lat.npz")

    hr("3. 结果")
    if any(t[3] < 1.0 for t in trials if not t[2]):
        print("   ⚠️ 有档位量到 1 ms 以内的延迟 —— 那多半是电路串扰"
              "（DAC 的电信号直接窜进 ADC），不是声学回环。")
        print("      把耳机贴紧话筒、离板子远一点再跑。")
    usable = [t for t in trials if not t[2] and 1000 < t[1] < 30000
              and t[4] > 8 and t[3] >= 1.0]
    if not usable:
        print("   三档都不合格（削顶 / 太小声 / 峰不明显）。")
        print("   削顶就调小再跑：ACOUSTIC_AMPS=\"0.001,0.003,0.01\"")
        print("   峰不明显就把耳塞再贴紧一点，或者把另一只耳塞也捂住。")
        return
    ds = np.array([t[3] for t in usable])
    print("   可用 %d 档：%s ms" % (len(ds), ", ".join("%.2f" % d for d in ds)))
    print("   取中位：**R = %.2f ms**" % float(np.median(ds)))
    print()
    print("   嘴 → 耳 = 数字延迟 + R")
    print("      演示配置（L=480 NBUF=3，29.80 ms）→ %.1f ms"
          % (29.80 + float(np.median(ds))))
    print("      低延迟（L=480 NBUF=2，19.86 ms）  → %.1f ms"
          % (19.86 + float(np.median(ds))))


if __name__ == "__main__":
    main()
