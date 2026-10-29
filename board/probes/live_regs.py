"""抓"正在放音那一刻"的 codec 寄存器 —— 之前所有对比都只抓了放音结束后。

已知：`AudioADAU1761.play()` 每调一次要写 8 次 I2C（4 开声 4 静音）。
如果那 4 次"开声"里有 `codec_init()` 没覆盖的寄存器，且结束时又恢复原值，
那么任何"放完之后再 dump"的对比都看不见它 —— 而这正好能解释
"寄存器逐字节相同、却一个响一个不响"。

做法：出货位流放 8 秒音，**在放的过程中**连 dump 两遍 0x4000-0x40FF，
放完再 dump 一遍。三者互相对比。

    ... live_regs.py
"""

import os
import sys
import threading
import time

os.environ.setdefault("XILINX_XRT", "/usr")
sys.path.insert(0, "/home/xilinx/dma_exp")

import dma_audio_probe as P  # noqa: E402
import hearing_tone as HT     # noqa: E402


def snap():
    return {r: P.i2c_rd(r) for r in range(0x4000, 0x4100)}


def diff(tag_a, a, tag_b, b):
    d = [(r, a.get(r), b.get(r)) for r in sorted(set(a) | set(b)) if a.get(r) != b.get(r)]
    print("\n  %s vs %s：%d 处不同" % (tag_a, tag_b, len(d)))
    for r, x, y in d:
        print("    0x%04X  %-5s=%-6s %-5s=%s"
              % (r, tag_a, "?" if x is None else "0x%02X" % x,
                 tag_b, "?" if y is None else "0x%02X" % y))
    return d


def main():
    from pynq import Overlay

    ol = Overlay("/home/xilinx/fir.bit")
    a = ol.audio_codec_ctrl_0
    a.configure()
    a.set_volume(40)
    HT._write_tone("/dev/shm/live_regs.wav", 440.0, -6.0, 8.0)
    a.load("/dev/shm/live_regs.wav")

    th = threading.Thread(target=a.play)
    th.start()
    time.sleep(0.4)
    during1 = snap()
    during2 = snap()
    th.join(timeout=20)
    time.sleep(0.3)
    after = snap()

    diff("放音中#1", during1, "放音中#2", during2)   # 只读电平表应该在这里
    diff("放音中", during2, "放音后", after)          # ★ 这一组才是要找的
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
