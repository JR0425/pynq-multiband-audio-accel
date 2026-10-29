"""对照组放音：出货 fir.bit（一定响） vs fir_officialtx.bit（官方发送器）。

目的：把"PL 直连不出声"劈成两半。
  · 两个都响 → 我们自己 i2s_duplex 的发送段有问题
  · 只有第一个响 → 官方发送器配我们的时钟也不响，问题在时钟/板级
  · 两个都不响 → 耳机/插座/codec 那一侧（和 PL 无关）

两段都用同一套 codec 配置。第二段在**装完位流之后**重新配一次 codec ——
装位流会重配整片 PL，MCLK 会断一下，codec 的 PLL 可能因此失锁。

    ... ab_officialtx.py --secs 6
"""

import argparse
import os
import sys
import time

os.environ.setdefault("XILINX_XRT", "/usr")
sys.path.insert(0, "/home/xilinx/dma_exp")

import dma_audio_probe as P  # noqa: E402
import hearing_tone as HT     # noqa: E402

GPIO_CTRL = 0x43C00000
GPIO_DBG = 0x43C30000


def round_ship(secs):
    from pynq import Overlay
    print(">>> 第 1 段：出货 fir.bit（官方音频 IP），440 Hz 正弦，%.0f 秒  开始 %s"
          % (secs, time.strftime("%H:%M:%S")))
    sys.stdout.flush()
    ol = Overlay("/home/xilinx/fir.bit")
    a = ol.audio_codec_ctrl_0
    a.configure()
    a.set_volume(40)
    HT._write_tone("/dev/shm/ab_otx.wav", 440.0, -6.0, secs)
    a.load("/dev/shm/ab_otx.wav")
    a.play()
    print("    结束 %s" % time.strftime("%H:%M:%S"))
    sys.stdout.flush()


def round_officialtx(secs):
    from pynq import MMIO, Overlay
    print(">>> 第 2 段：fir_officialtx.bit（官方 iis_ser），440 Hz 方波，%.0f 秒  开始 %s"
          % (secs, time.strftime("%H:%M:%S")))
    sys.stdout.flush()
    ol = Overlay("/home/xilinx/dma_exp/fir_officialtx.bit")
    print("    里面：", sorted(ol.ip_dict.keys()))
    P.codec_init(1)                      # 装完位流之后重新配
    print("    0x4023=0x%02X（bit1=1 表示耳机未静音）" % (P.i2c_rd(0x4023) or 0))
    ctrl = MMIO(GPIO_CTRL, 0x1000)
    dbg = MMIO(GPIO_DBG, 0x1000)
    ctrl.write(0, 0)
    time.sleep(0.2)
    ph0 = dbg.read(0) & 0xFFFF
    ctrl.write(0, 1)
    time.sleep(secs)
    ctrl.write(0, 0)
    ph1 = dbg.read(0) & 0xFFFF
    print("    结束 %s   相位 0x%04X → 0x%04X（变了=音源在走）"
          % (time.strftime("%H:%M:%S"), ph0, ph1))
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=6.0)
    args = ap.parse_args()

    import gc
    gc.disable()

    round_ship(args.secs)
    time.sleep(2.0)
    round_officialtx(args.secs)
    print("== 结束 ==")


if __name__ == "__main__":
    raise SystemExit(main())
