"""关键对照 C：让官方通路一直"在放音"，但送的是**全零**；引脚上的数据换成 tone_ser。

能同时解释 A/B 结果和 ILA 的只有一个假设：codec 的 DAC/耳机通路只有在
`Audio.play()` 正在跑的时候才是打开的（play() 会补写几条寄存器，见
dma_audio_probe.codec_unmute 的注释）。B 里面 play() 早退出了。

C 把这两件事拆开：
    sel=1（引脚 = tone_ser 的 440 Hz 方波）
    + a.play() 一直在跑（官方通路活着，但样本全是 0）

  听到 440 Hz 方波 -> 之前 B 不响是 codec 状态问题，不是数据问题
  还是静音       -> 数据这条路真的断了

用法（板上，root，cwd /home/xilinx/dma_exp）：
    python3 ipmux_c.py --secs 6
"""

import argparse
import os
import sys
import time
import wave

sys.path.insert(0, "/home/xilinx/dma_exp")
os.environ.setdefault("XILINX_XRT", "/usr")


def silent_wav(path, secs, fs=48000):
    frame = b"\x00\x00\x00" * 2          # 两声道、24 位，全零
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(3)
        f.setframerate(fs)
        f.writeframes(frame * int(fs * secs))
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default="/home/xilinx/dma_exp/fir_ipmux_ila.bit")
    ap.add_argument("--secs", type=float, default=6.0)
    args = ap.parse_args()

    from pynq import MMIO, Overlay

    ol = Overlay(args.bit)
    a = ol.audio_codec_ctrl_0
    a.configure()
    a.set_volume(60)
    g = MMIO(0x43C10000, 0x1000)

    silent_wav("/dev/shm/sil.wav", args.secs + 1.0)
    a.load("/dev/shm/sil.wav")

    g.write(0, 1)                        # 引脚 = tone_ser
    time.sleep(0.3)
    print("C_START sel=1 + official playing silence %s" % time.strftime("%H:%M:%S"))
    sys.stdout.flush()
    a.play()
    g.write(0, 0)
    print("C_END %s" % time.strftime("%H:%M:%S"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
