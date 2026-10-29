"""codec 的 ADC 到底有没有在往外送数据 —— 用出货 fir.bit 的官方录音通路录 2 秒。

区分两件事：
  出货版能录到东西  -> codec ADC 正常，问题是 PL 收不到
  出货版也录到 0    -> codec 根本没在送，问题在 codec / 话筒

用法（板上，root）：python3 rec_check.py --secs 2
"""

import argparse
import os
import sys

os.environ.setdefault("XILINX_XRT", "/usr")
sys.path.insert(0, "/home/xilinx/dma_exp")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default="/home/xilinx/fir.bit")
    ap.add_argument("--secs", type=float, default=2.0)
    args = ap.parse_args()

    import numpy as np
    from pynq import Overlay

    ol = Overlay(args.bit)
    a = ol.audio_codec_ctrl_0
    a.configure()
    a.record(args.secs)

    n = a.sample_len * 2
    x = a.buffer[:n].astype(np.int64) & 0xFFFFFF      # 24 位零扩展
    v = np.where(x >= 0x800000, x - 0x1000000, x)      # 符号扩展
    L = v[0::2].astype(np.float64)
    print("REC n=%d  rms=%.0f  peak=%d  (满量程 8388607)"
          % (len(v), np.sqrt((L ** 2).mean()), int(np.abs(v).max())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
