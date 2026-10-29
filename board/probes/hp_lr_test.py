"""hp_lr_test —— 只靠 codec 寄存器判"耳机左右是不是真的分开"。

不碰 PL、不碰位流、不改调音参数，只写 ADAU1761 的两个耳机音量寄存器：

    0x4023  耳机左音量   [7:2] 音量码（-57..+6 dB），bit1=1 不静音，bit0=1 输出使能
    0x4024  耳机右音量   同上

每 5 秒换一次：两边都响 → 只右响 → 只左响，循环 2 轮，最后恢复原值。
听到什么就说明什么：

    该响的响、该静的那边真的静  -> codec 左右是分开的，左右耳相同另有原因
    左静的时候两边都静          -> 耳机左右在硬件上被短接了（插头没插到底 / TRRS 接触）
    该静的左边却响、右边静      -> 左右在插头这一级反了

用法（板上 root，耳机戴好再跑）：
    echo xilinx | sudo -S env XILINX_XRT=/usr \\
        /usr/local/share/pynq-venv/bin/python3 hp_lr_test.py
"""
import os
import subprocess
import sys
import time

os.environ.setdefault("XILINX_XRT", "/usr")
sys.path.insert(0, "/home/xilinx/dma_exp")

import dma_audio_probe as P  # noqa: E402

HP_L, HP_R = 0x4023, 0x4024
ON = 0xF3          # 音量码 60（0 dB），bit1=1 不静音，bit0=1 使能
OFF = 0xF1         # 同音量，bit1=0 静音


def wr(reg, val):
    P.i2c_wr(reg, val)


def rd(reg):
    out = subprocess.check_output(
        ["i2ctransfer", "-y", "1", "w2@0x3b", "0x%02x" % (reg >> 8),
         "0x%02x" % (reg & 0xFF), "r1"]).decode().strip()
    return int(out.split()[0], 16)


def main():
    l0, r0 = rd(HP_L), rd(HP_R)
    print("原值 0x4023=0x%02X 0x4024=0x%02X" % (l0, r0))
    try:
        for rnd in range(2):
            for name, l, r in (("两边都响", ON, ON),
                               ("只有右耳（左静音）", OFF, ON),
                               ("只有左耳（右静音）", ON, OFF)):
                wr(HP_L, l)
                wr(HP_R, r)
                for k in range(5, 0, -1):
                    print("\r  第%d轮 %-18s -> %d" % (rnd + 1, name, k), end="")
                    sys.stdout.flush()
                    time.sleep(1)
                print()
    finally:
        wr(HP_L, l0)
        wr(HP_R, r0)
        print("已恢复 0x4023=0x%02X 0x4024=0x%02X" % (l0, r0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
