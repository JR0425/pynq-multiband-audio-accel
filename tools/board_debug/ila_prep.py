"""给 ILA 抓波形做准备：在板子上把位流加载好、codec 配好、持续放音。

PC 侧另一边用 Vivado 的 ila_capture.tcl 通过 JTAG 抓波形。两边是独立进程，
所以这个脚本要**一直占着板子**（默认 40 秒），别提前退出。

    python3 ila_prep.py --sel 0 --secs 40    # 官方分支出声（对照）
    python3 ila_prep.py --sel 1 --secs 40    # 我们的 tone_ser 出声

sel=0 走 PYNQ 的 Audio.play()：PS 一直往音频 IP 的寄存器写 24 位样本，
        IP 内部 iis_ser 再往外发 —— 和出货 fir.bit 完全同一条路。
sel=1 只把 GPIO 置 1：tone_ser 的 EN 和 mux 的 sel 是同一位，
       声音由 PL 内部的相位累加器产生，PS 不参与数据。
"""

import argparse
import os
import sys
import time

os.environ.setdefault("XILINX_XRT", "/usr")
sys.path.insert(0, "/home/xilinx/dma_exp")

import hearing_tone as HT
from pynq import MMIO, Overlay

BIT = "/home/xilinx/dma_exp/fir_ipmux_ila.bit"
GPIO_BASE = 0x43C10000
AUDIO_BASE = 0x43C00000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sel", type=int, default=0, choices=[0, 1])
    ap.add_argument("--secs", type=float, default=40.0)
    ap.add_argument("--bit", default=BIT)
    args = ap.parse_args()

    ol = Overlay(args.bit)
    g = MMIO(GPIO_BASE, 0x1000)
    g.write(0, args.sel)

    a = ol.audio_codec_ctrl_0
    a.configure()
    a.set_volume(60)

    print("READY sel=%d bit=%s %s" % (args.sel, args.bit, time.strftime("%H:%M:%S")))
    sys.stdout.flush()

    if args.sel == 0:
        # 官方分支：PS 往音频 IP 的 DataTx 寄存器持续写 440 Hz 样本。
        # play() 是阻塞的，所以每轮重新 load 一次，一直放到时间用完。
        HT._write_tone("/dev/shm/ila.wav", 440.0, -1.0, 6.0)
        t_end = time.time() + args.secs
        while time.time() < t_end:
            a.load("/dev/shm/ila.wav")
            a.play()
    else:
        time.sleep(args.secs)

    g.write(0, 0)
    print("DONE %s" % time.strftime("%H:%M:%S"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
