"""D：只靠 I2C 手写解静音，完全不调 Audio.play()，引脚走 tone_ser。

来源：/home/xilinx/pynq/lib/audio.py 里 `set_volume()` 只把数值存进
self.volume，**不写 codec**；真正写耳机音量/解静音的是
`libaudio.play(..., self.volume, ..., iic_index)` 这个 C 函数。
也就是说 `configure()` 之后耳机一直停在 config_audio_codec 写下的
0x4023/0x4024 = 0xE5（bit1 = 0 -> 静音），只有 play()/bypass() 才会改成 0xE7。

D 把这四条直接写掉，不跑 play()，看 B 的静音是不是就这么来的。

用法（板上，root，cwd /home/xilinx/dma_exp）：
    python3 ipmux_d.py --secs 6
"""

import argparse
import os
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")
os.environ.setdefault("XILINX_XRT", "/usr")

# play() 实际写的值（2026-10-28 用 codec_diff.py 差分出来的精确值）。
# 旧 codec_unmute() 里那套 0x01/0x01/0xE7/0xE7 是错的 —— 0x401C 停在 0x01
# 时耳机仍然不响，说明起作用的不是 bit0。
UNMUTE = [
    (0x401C, 0x21),   # 播放混音器左  0x00 -> 0x21
    (0x401E, 0x41),   # 播放混音器右  0x00 -> 0x41
    (0x4023, 0xF3),   # 耳机左       0xE5 -> 0xF3（音量码 60，bit1=1 不静音）
    (0x4024, 0xF3),   # 耳机右
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default="/home/xilinx/dma_exp/fir_ipmux_ila.bit")
    ap.add_argument("--secs", type=float, default=6.0)
    args = ap.parse_args()

    import dma_audio_probe as P
    from pynq import MMIO, Overlay

    ol = Overlay(args.bit)
    a = ol.audio_codec_ctrl_0
    a.configure()
    a.set_volume(60)                     # 只存变量，不写硬件

    print("  解静音前:", " ".join("0x%04X=0x%02X" % (r, P.i2c_rd(r)) for r, _ in UNMUTE))
    for r, v in UNMUTE:
        P.i2c_wr(r, v)
    print("  解静音后:", " ".join("0x%04X=0x%02X" % (r, P.i2c_rd(r)) for r, _ in UNMUTE))
    sys.stdout.flush()

    g = MMIO(0x43C10000, 0x1000)
    g.write(0, 1)                        # 引脚 = tone_ser
    print("D_START sel=1 no-play %s" % time.strftime("%H:%M:%S"))
    sys.stdout.flush()
    time.sleep(args.secs)
    g.write(0, 0)
    print("D_END %s" % time.strftime("%H:%M:%S"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
