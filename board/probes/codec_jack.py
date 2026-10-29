# -*- coding: utf-8 -*-
"""ADAU1761 全片读数，只标出和"耳机有没有声"直接相关的寄存器。

当前状态：出货位流和我们自己的位流都放不出声，所以先要确认 codec 到底
认为耳机插没插、DAC/HP 有没有上电。这几条是决定性的：

    0x4000/0x4002  CLK_ENABLE 0/1     核心时钟与串口时钟使能
    0x4014         SERIAL_PORT_CTRL_0 主从模式、字长、I2S/LJ 格式
    0x4015         SERIAL_PORT_CTRL_1 BCLK/LRCLK 极性
    0x4017         CONVERTER_CTRL_0   DAC / ADC 使能
    0x4029         PLAY_POWER_MGMT    DAC、耳机、线出上电
    0x4023/0x4024  HP_LEFT/RIGHT_VOL  音量 + 静音位
    0x40E0~0x40EF  状态位（含插孔/输出检测），每次读都可能变

用法（板上 root）：
    echo xilinx | sudo -S env XILINX_XRT=/usr \
        /usr/local/share/pynq-venv/bin/python3 codec_jack.py [--loop 5]
"""
import argparse
import os
import sys
import time

os.environ.setdefault("XILINX_XRT", "/usr")
sys.path.insert(0, "/home/xilinx/dma_exp")

import dma_audio_probe as P  # noqa: E402

NAMED = [
    (0x4000, "CLK_ENABLE_0"),
    (0x4002, "CLK_ENABLE_1"),
    (0x4004, "REC_PWR_MGMT"),
    (0x4008, "MICBIAS"),
    (0x4014, "SERIAL_PORT_CTRL_0"),
    (0x4015, "SERIAL_PORT_CTRL_1"),
    (0x4016, "SERIAL_PORT_PAD"),
    (0x4017, "CONVERTER_CTRL_0"),
    (0x4018, "CONVERTER_CTRL_1"),
    (0x4019, "ADC_CTRL"),
    (0x401C, "PLAY_MIXER_L"),
    (0x401E, "PLAY_MIXER_R"),
    (0x4020, "PLAY_LR_MIX_L"),
    (0x4021, "PLAY_LR_MIX_R"),
    (0x4022, "PLAY_MIXER_L1"),
    (0x4023, "HP_LEFT_VOL"),
    (0x4024, "HP_RIGHT_VOL"),
    (0x4025, "LINEOUT_L_VOL"),
    (0x4026, "LINEOUT_R_VOL"),
    (0x4029, "PLAY_POWER_MGMT"),
]

STATUS = list(range(0x40E0, 0x40F0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=1)
    a = ap.parse_args()

    for k in range(a.loop):
        if a.loop > 1:
            print("=== 第 %d 次 ===" % (k + 1))
        for reg, name in NAMED:
            v = P.i2c_rd(reg)
            print("  0x%04X %-22s %s"
                  % (reg, name, "None" if v is None else "0x%02X" % v))
        s = []
        for reg in STATUS:
            v = P.i2c_rd(reg)
            s.append("%02X" % v if v is not None else "--")
        print("  0x40E0..EF 状态: " + " ".join(s))
        sys.stdout.flush()
        if k + 1 < a.loop:
            time.sleep(2.0)


if __name__ == "__main__":
    main()
