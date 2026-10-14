# -*- coding: utf-8 -*-
"""cap_probe16.py —— 拔插找 U41:话筒到底哪一秒通。

为什么是这一步
--------------
到 2026-10-14 深夜,已经排掉的事:

  · 板子录音通路是好的 —— cap_probe13:电脑经公对公线打 Line-in,峰 2559;
    同一套代码把录音混音器关掉,峰 2。
  · 话筒不是"增益不够" —— cap_probe14/15:A(PYNQ 原样 0x10/0xB3)与
    C(fir_live 当时那套 0x08/0xD3)交叉跑,读数跟着寄存器走,差约 11 dB;
    那四行压增益的调用已从 fir_live.py 删掉。
  · **敲麦也没反应** —— cap_probe15 之后用真管线跑了 40 秒,让人用指尖一直敲
    耳机线上的麦克风:进核 rms 13~28、峰 37~82,一条平线。手指敲麦是最强的
    机械冲击,麦要是通着必然蹦起来。

剩下的唯一解释:**HP+Mic 那个口里的麦克风触点没有接到 codec 上。**

那颗开关是 U41(TI TS3A226AE,自动耳机开关)。它只在**插头插入的那个上升沿**
做一次 CTIA/OMTP 检测,然后**锁存**。所以插一次可能就锁在错的那一档,
得反复拔插去撞对的那次 —— 2026-10-09 就是这么找到的,当时实测要连续拔插到
第 35.5 秒才跳。但**那次 90 秒拔插是用坏的增益配置量的,等于没测**。

这个脚本就是把它重做一遍:连续采 90 秒,每秒一行,最后一并排出"最响的十秒"。
人一边拔插一边"嗯————",哪一秒锁对了,表上会立刻出现一个几百上千的峰。

只读 RX,不开 I2S 输出,耳机不会响。

跑法(板上,和 fir_live.py 同一条 sudo 命令):
    echo xilinx | sudo -S sh -c "cd /home/xilinx/jupyter_notebooks && \
        XILINX_XRT=/usr PYTHONIOENCODING=utf-8 \
        /usr/local/share/pynq-venv/bin/python3 cap_probe16.py"
"""

import fcntl
import os
import time

import numpy as np
from pynq import Overlay

HERE = "/home/xilinx/jupyter_notebooks"
L = 480                 # 10 ms 一块
MMIO_LEN = 0x10000
ADDR = 0x3B             # ADAU1761 的 7 位 I2C 地址
FULL24 = 1 << 24
HALF24 = 1 << 23

RUN_SEC = 90.0
PRINT_EVERY = 1.0
VOICE_PEAK = 300        # 峰过这个数就算"这一秒话筒通了"


def hr(s):
    print()
    print("=" * 78)
    print(s)
    print("=" * 78)


fd = os.open("/dev/i2c-1", os.O_RDWR)
fcntl.ioctl(fd, 0x0703, ADDR)


def rd(reg):
    try:
        os.write(fd, bytes([0x40, reg]))
        return os.read(fd, 1)[0]
    except Exception:
        return None


def snap():
    return " ".join("%02X=%s" % (r, "??" if rd(r) is None else "%02X" % rd(r))
                    for r in (0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10))


def find_uio(name):
    table = []
    for i in range(64):
        try:
            with open("/sys/class/uio/uio%d/name" % i) as f:
                table.append((i, f.read().strip()))
        except IOError:
            pass
    for i, n in table:
        if n == name:
            return i, table
    for i, n in table:
        if "audio" in n.lower():
            return i, table
    return None, table


hr("0. 加载 overlay 与驱动")
ol = Overlay(os.path.join(HERE, "fir.bit"))
audio = ol.audio_codec_ctrl_0
audio.configure()
audio.select_microphone()       # R4/R6 = 0x01,录音混音器开;R5/R7=0x10, R8/R9=0xB3
time.sleep(0.3)
print("   configure() + select_microphone() 完了。寄存器:%s" % snap())

UIO, UIO_TABLE = find_uio("audio-codec-ctrl")
print("   uio 设备表:%s -> 用 uio%s" % (UIO_TABLE, UIO))
if UIO is None:
    print("   [X] overlay 没把 audio IP 绑到 uio 上,到此为止")
    raise SystemExit(1)

try:
    ffi = audio._ffi
except AttributeError:
    import cffi
    ffi = cffi.FFI()
ffi.cdef("""
    void* capture_begin(unsigned int mmio, int uio);
    int   capture_block(void* h, unsigned int* buf, unsigned int n);
    void  capture_end(void* h);
""")
lib = ffi.dlopen(os.path.join(HERE, "libaudio_stream.so"))

rbuf = ffi.new("unsigned int[]", 2 * L)


def levels():
    """本块两个声道的 (去直流 rms, 峰),单位 int16 域(满量程 32767)。
    低 24 位是二进制补码,必须先符号扩展,否则近零信号会被读成满幅。"""
    a = np.frombuffer(bytes(ffi.buffer(rbuf, 4 * 2 * L)), dtype=np.int32)
    c = a.reshape(-1, 2).astype(np.int64) & 0xFFFFFF
    s = np.where(c >= HALF24, c - FULL24, c).astype(np.float64) / 256.0
    out = []
    for ch in (0, 1):
        v = s[:, ch]
        v = v - v.mean()
        out.append((float(np.sqrt(np.mean(v * v))), float(np.abs(v).max())))
    return out


hr("开始。连续 %.0f 秒,每秒一行。人一边拔插一边'嗯————'。" % RUN_SEC)
print("  拔的时候要**拔到底**(整根抽出来),停两秒,再**插到底**。来回做,别停。")
print("  参考:敲麦那趟的底噪是 rms 13~28 / 峰 37~82;通了的峰应该在 300 以上。")

h = lib.capture_begin(MMIO_LEN, UIO)
if h == ffi.NULL:
    print("   [X] capture_begin 失败")
    raise SystemExit(1)

per_sec = []            # 每秒一行:(t, rms, peak) 取该秒的峰
cur = []
try:
    for b in range(int(RUN_SEC * 100)):
        if lib.capture_block(h, rbuf, L) != L:
            print("  [X] capture_block 没填满")
            break
        (lr, lp), (rr, rp) = levels()
        cur.append((max(lr, rr), max(lp, rp)))
        if (b + 1) % int(PRINT_EVERY * 100) == 0:
            t = float(len(per_sec) + 1)
            a = np.array(cur)
            rms = float(np.median(a[:, 0]))
            pk = float(a[:, 1].max())
            per_sec.append((t, rms, pk))
            cur = []
            print("  t=%5.0fs  rms(中位)=%6.1f  峰=%7.0f   %s"
                  % (t, rms, pk, "有 <<<" if pk >= VOICE_PEAK else ""))
finally:
    lib.capture_end(h)

hr("最响的十秒")
ranked = sorted(per_sec, key=lambda r: -r[2])[:10]
print("  %8s %12s %12s" % ("第几秒", "rms(中位)", "峰"))
for t, rms, pk in ranked:
    print("  %8.0f %12.1f %12.0f   %s"
          % (t, rms, pk, "有 <<<" if pk >= VOICE_PEAK else ""))

hit = [r for r in per_sec if r[2] >= VOICE_PEAK]
print()
if hit:
    print("  **有 %d 秒冲过了 %d。** 最早的是第 %.0f 秒(峰 %.0f)。"
          % (len(hit), VOICE_PEAK, hit[0][0], hit[0][2]))
    print("  -> U41 锁到对的那一档了。把这副耳机的插法固定下来,别再拔。")
else:
    print("  90 秒里一秒都没冲过 %d —— 拔插没有把它撞对。" % VOICE_PEAK)
    print("  -> 先确认:插头是不是插到底了(要塞到听见'咔')、麦在不在那一侧。")
    print("     再不行就换一副带麦耳机试;两副都不行,那就是板子那个口的硬件问题,")
    print("     演示改走 Line-in(今天已经验证过是通的,峰 2559)。")
print("   done")
