"""把两边唯一可写的寄存器差异（0x400A / 0x400C）改回出货版的值，再放一遍。

背景：0x4000-0x40FF 整片比完，只有 8 个不同；其中 6 个是只读电平表或脚本自己
改的音量。**真正可写、且我们和出货不同的只剩 0x400A / 0x400C**（我们 0x01，
出货 0x00）。这两个原来被当成"录音混音器"，是 select_mic() 写的，但没验证过
它们和播放无关。

本脚本放三段，每段 6 秒，中间自动切换，最后把值恢复原状：
  A 当前值（0x01 / 0x01，预期没声）
  B 改成 0x00 / 0x00
  C 再改回 0x01 / 0x01

    ... reg_ab.py
"""

import os
import sys
import time

os.environ.setdefault("XILINX_XRT", "/usr")
sys.path.insert(0, "/home/xilinx/dma_exp")

import dma_audio_probe as P  # noqa: E402

TONE_BITS = P.CTRL_RUN | (1 << 3)
SECS = 6.0


def show(tag):
    a = P.i2c_rd(0x400A)
    c = P.i2c_rd(0x400C)
    v = P.i2c_rd(0x4023)
    print("    [%s] 0x400A=0x%02X 0x400C=0x%02X 0x4023(HP音量)=0x%02X"
          % (tag, a or 0, c or 0, v or 0))
    sys.stdout.flush()


def tone(ctrl, tag):
    t0 = time.strftime("%H:%M:%S")
    print("\n>>> %s：放音 %.0f 秒（开始于 %s）" % (tag, SECS, t0))
    sys.stdout.flush()
    P.set_ctrl(ctrl, TONE_BITS)
    time.sleep(SECS)
    P.set_ctrl(ctrl, 0)
    print("    停（%s）" % time.strftime("%H:%M:%S"))
    sys.stdout.flush()


def main():
    P.BITFILE = "i2sdbg.bit"
    P.HWHFILE = "i2sdbg.hwh"
    dma, ctrl, stat = P.load()
    P.codec_init(1)
    print("\n  配完 codec：")
    show("初始")

    tone(ctrl, "A 当前值 0x01/0x01")

    P.i2c_wr(0x400A, 0x00)
    P.i2c_wr(0x400C, 0x00)
    print("\n  已把 0x400A / 0x400C 写成 0x00（出货版的值）：")
    show("改后")
    tone(ctrl, "B 改成 0x00/0x00")

    P.i2c_wr(0x400A, 0x01)
    P.i2c_wr(0x400C, 0x01)
    print("\n  恢复原状：")
    show("恢复")
    tone(ctrl, "C 恢复 0x01/0x01")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
