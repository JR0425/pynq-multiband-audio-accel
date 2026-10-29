"""对照：同一个 codec 配置下，出货位流和我们的位流录到的到底是"真声音"还是"悬空噪声"。

为什么要这一步：出货位流有声、我们的没声，而 codec 寄存器逐字节相同。
剩下的分岔只有两条 ——
  (A) codec 在我们的位流里**根本没在转换**（MCLK / 时钟断了）→ 录到的应是悬空噪声：
      频谱白、逐窗口几乎不变、和说话的节奏无关；
  (B) codec 活着，只是没解出我们送去的串行数据 → 录到的应和出货那边**同类**：
      有低频隆起、逐窗口起伏大。

做法：两边都用**完全同一套** codec 配置（`codec_init()`：pll + codec + select_mic + unmute），
各录 3 秒，给 rms、逐窗口 rms、以及低/高段能量比。

    ... mic_cmp.py
"""

import os
import sys
import time

import numpy as np

FS = 48828.125


def sext24(x):
    """出货位流的 buffer 是 24 位**零扩展**成 int32 的（峰值恰好 0xFFFFFF、
    负值占比 0%），我们的是符号扩展。统一按 24 位补码重算，两边才可比。
    对已经符号扩展过的值，这个变换是恒等的。"""
    v = np.asarray(x, dtype=np.int64) & 0xFFFFFF
    return np.where(v >= (1 << 23), v - (1 << 24), v)


def describe(tag, x):
    x = sext24(x).astype(np.float64)
    n = len(x)
    X = np.abs(np.fft.rfft((x - x.mean()) * np.hanning(n)))
    f = np.fft.rfftfreq(n, 1.0 / FS)
    lo = float(np.sum(X[(f > 50) & (f < 500)] ** 2))
    hi = float(np.sum(X[(f > 4000) & (f < 15000)] ** 2))
    w = np.array_split(x, 8)
    wr = [float(np.sqrt(np.mean(v ** 2))) for v in w]
    print("\n  --- %s ---" % tag)
    print("    rms=%.0f  逐窗口 rms=%s" % (float(np.sqrt(np.mean(x ** 2))),
                                          " ".join("%.0f" % v for v in wr)))
    print("    窗内起伏 = %.1f 倍（真声音通常 >3；悬空噪声接近 1）"
          % (max(wr) / (min(wr) + 1e-9)))
    print("    低频(50-500Hz)/高频(4-15kHz) 能量比 = %.1f（房间噪声>1；白噪声≈0.03）"
          % (lo / (hi + 1e-9)))


def main():
    os.environ.setdefault("XILINX_XRT", "/usr")
    sys.path.insert(0, "/home/xilinx/dma_exp")
    import dma_audio_probe as P
    from pynq import Overlay, allocate

    import gc
    gc.disable()

    # ---- 出货位流，用我们这套 codec 配置 ----
    ol = Overlay("/home/xilinx/fir.bit")
    a = ol.audio_codec_ctrl_0
    a.configure()
    P.codec_init(1)              # 覆盖成和下面完全一样的配置
    a._configured = True         # 别让 record() 再调一次 configure() 把它盖回去
    a.record(3.0)
    describe("出货 fir.bit（同一套 codec 配置）", np.array(a.buffer, dtype=np.int32))

    # ---- 我们的位流 ----
    P.BITFILE = "i2sdbg.bit"
    P.HWHFILE = "i2sdbg.hwh"
    dma, ctrl, stat = P.load()
    P.codec_init()
    P.flush_all(ctrl)
    P.set_ctrl(ctrl, P.blk_bits(480))
    time.sleep(0.3)

    nblk = int(3.0 * FS / 480)
    bufs = [allocate(shape=(960,), dtype=np.int32) for _ in range(nblk)]
    for b in bufs:
        P.cap_block(dma, b)
    P.set_ctrl(ctrl, 0)
    st = P.read_stat(stat)
    print("\n  stat: overrun=%d underrun=%d" % (st["ovf"], st["und"]))
    describe("我们的 i2sdbg.bit", np.concatenate([np.array(b, dtype=np.int32) for b in bufs]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
