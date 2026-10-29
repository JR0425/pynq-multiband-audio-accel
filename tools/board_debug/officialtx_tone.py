"""放「官方发送器」对照位流（fir_officialtx.bit）的 440 Hz，等人耳判定。

用途：这颗位流把发送器换成官方 `iis_ser.vhd`，时钟仍是我们自己的计数器
（`cntr[4]`/`cntr[10]`，和官方 user_logic.vhd 一样是组合输出）。同一个音源、
同一套 codec 配置，**唯一变量就是发送器**。

    · 有声 → 我们 `i2s_duplex` 的发送段有问题
    · 无声 → 时钟/板级那一侧，软件到头了

先编位流：`sh build/run_officialtx_build.sh`，产出 `board/overlay/fir_officialtx.bit`。

板上用法（root，cwd 在 /home/xilinx/dma_exp）：
    ... officialtx_tone.py --secs 10
"""

import argparse
import os
import sys
import time

os.environ.setdefault("XILINX_XRT", "/usr")
sys.path.insert(0, "/home/xilinx/dma_exp")

import dma_audio_probe as P  # noqa: E402

GPIO_CTRL = 0x43C00000
GPIO_DBG = 0x43C30000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default="fir_officialtx.bit")
    ap.add_argument("--secs", type=float, default=10.0)
    args = ap.parse_args()

    from pynq import MMIO, Overlay

    for f in (args.bit, args.bit.replace(".bit", ".hwh")):
        if not os.path.exists(f):
            print("ERROR: 找不到 %s（先跑 build/run_officialtx_build.sh）" % f)
            return 1

    ol = Overlay(args.bit)
    print("  IP：", sorted(ol.ip_dict.keys()))

    P.codec_init(1)          # 和 reg_ab / ab_ship_tone 用同一套 codec 配置
    print("  codec 配好，0x4023=0x%02X（bit1=1 表示耳机未静音）" % (P.i2c_rd(0x4023) or 0))

    ctrl = MMIO(GPIO_CTRL, 0x1000)
    dbg = MMIO(GPIO_DBG, 0x1000)

    ctrl.write(0, 0)
    time.sleep(0.2)
    ph0 = dbg.read(0) & 0xFFFF

    print("\n>>> 放音 %.0f 秒（开始于 %s）" % (args.secs, time.strftime("%H:%M:%S")))
    sys.stdout.flush()
    ctrl.write(0, 1)                      # ctrl[0] = run
    time.sleep(args.secs)
    ctrl.write(0, 0)
    ph1 = dbg.read(0) & 0xFFFF
    print("    停（%s）" % time.strftime("%H:%M:%S"))
    print("    相位从 0x%04X 变到 0x%04X —— 变了说明音源在走" % (ph0, ph1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
