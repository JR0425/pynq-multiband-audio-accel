# 让人耳朵听全 PL 直连版：配一次 codec，把链路放行，然后挂着不动。
#
#   python3 pl_listen.py            # 过压缩（产品通路）
#   python3 pl_listen.py --drc 0    # 只过前端（bypass 对照）
#   python3 pl_listen.py --secs 120 # 两分钟后自己停
#
# Ctrl-C 停。**停之前别拔耳机插头**：板载 U41 只在插入的上升沿锁存一次，
# 已经插着的话要先拔掉、停两秒、再插回去，电平才会跳起来。
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pynq import Overlay, MMIO  # noqa: E402

from dma_audio_probe import codec_init  # noqa: E402

BASE_CTRL = 0x43C00000
BASE_STAT = 0x43C10000
BASE_STAT2 = 0x43C20000


def arg(name, default):
    if name in sys.argv:
        return sys.argv[sys.argv.index(name) + 1]
    return default


def mmio_at(ol, base):
    for k, v in ol.ip_dict.items():
        if v.get("phys_addr") == base:
            return getattr(ol, k).mmio
    return MMIO(base, 0x1000)


def main():
    secs = float(arg("--secs", "0"))
    drc = arg("--drc", "1") not in ("0", "no", "false")
    bit = arg("--bit", "/home/xilinx/dma_exp/fir_pl.bit")

    ol = Overlay(bit)
    ctrl = mmio_at(ol, BASE_CTRL)
    stat = mmio_at(ol, BASE_STAT)
    stat2 = mmio_at(ol, BASE_STAT2)

    print("== 配 codec ==")
    codec_init()

    base = 1 | (2 if drc else 0)
    ctrl.write(0, base | 4)
    time.sleep(0.02)
    ctrl.write(0, base)
    print("== 放行 ctrl=0x%02X（drc_en=%d），应该马上能听到自己的声音 =="
          % (base, 1 if drc else 0))
    print("   Ctrl-C 停")

    t0 = time.time()
    try:
        while True:
            time.sleep(1.0)
            s0, s1, s2 = stat.read(0), stat.read(8), stat2.read(0)
            print("  %4.0fs ovf=%d und=%d tx=%d cap=%d oovf=%d peak=%d"
                  % (time.time() - t0, s0 & 0xFFFF, (s0 >> 16) & 0xFFFF,
                     s1 & 0xFFFF, (s1 >> 16) & 0xFFFF,
                     s2 & 0xFFFF, (s2 >> 16) & 0xFFFF))
            if secs and time.time() - t0 >= secs:
                break
    except KeyboardInterrupt:
        pass
    finally:
        ctrl.write(0, 0)
        print("  已停")


if __name__ == "__main__":
    main()
