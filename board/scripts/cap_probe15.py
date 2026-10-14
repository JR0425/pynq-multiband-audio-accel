# -*- coding: utf-8 -*-
"""cap_probe15.py —— 交叉验证:话筒的读数到底跟不跟着寄存器走。

cap_probe14 一趟跑出三段:
    A  PYNQ 原样(0x0B/0x0D=0x10, 0x0E/0x0F=0xB3)  峰 6407   <- 有声音
    B  差分音量拉满(0x0E/0x0F=0xFF)               峰 1203   <- 有声音
    C  fir_live.py 现在实际在用的(0x08, 0xD3)      峰   12   <- 死

C 正是 `fir_live.py` 里那四行 `stream_reg_write` 写的值,而它比 A 低约 40 dB,正好
对上两处改动的代价:`0xB3 -> 0xD3` 是 +26.25 dB -> +2.25 dB(掉 24 dB),
`0x10 -> 0x08` 是关掉 LDBOOST(掉 20 dB)。

**但 C 也可能是"人那时候正好不说了"。** 一个顺序量的东西分不清是寄存器还是人。
所以这一趟把顺序打成交叉:

    A1(6s) -> C1(6s) -> A2(6s) -> C2(6s)

人从头到尾说个不停。读数要是**跟着寄存器来回跳**,那就跟人没关系,是寄存器;
要是**从某个点掉下去就再没起来**,那多半是人不说了,结论作废。

只读 RX,不开 I2S 输出,耳机不会响。

跑法(板上,和 fir_live.py 同一条 sudo 命令):
    echo xilinx | sudo -S sh -c "cd /home/xilinx/jupyter_notebooks && \
        XILINX_XRT=/usr PYTHONIOENCODING=utf-8 \
        /usr/local/share/pynq-venv/bin/python3 cap_probe15.py"
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

STAGE_SEC = 6.0
PRINT_EVERY = 0.5
VOICE_PEAK = 500


def hr(s):
    print()
    print("=" * 78)
    print(s)
    print("=" * 78)


fd = os.open("/dev/i2c-1", os.O_RDWR)
fcntl.ioctl(fd, 0x0703, ADDR)

READ_REGS = (0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10)


def rd(reg):
    try:
        os.write(fd, bytes([0x40, reg]))
        return os.read(fd, 1)[0]
    except Exception:
        return None


def wr(reg, val):
    try:
        os.write(fd, bytes([0x40, reg, val]))
        return True
    except Exception:
        return False


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
audio.select_microphone()       # R4/R6 = 0x01,录音混音器**开**
time.sleep(0.3)
print("   configure() + select_microphone() 完了")

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


A_HDR = "A  PYNQ 原样话筒(0x0B/0x0D=0x10, 0x0E/0x0F=0xB3)"
C_HDR = "C  fir_live.py 现在实际在用的(0x0B/0x0D=0x08, 0x0E/0x0F=0xD3)"
A_WR = [(0x0B, 0x10), (0x0D, 0x10), (0x0E, 0xB3), (0x0F, 0xB3)]
C_WR = [(0x0B, 0x08), (0x0D, 0x08), (0x0E, 0xD3), (0x0F, 0xD3)]

STAGES = [
    ("A1", A_HDR, A_WR),
    ("C1", C_HDR, C_WR),
    ("A2", A_HDR, A_WR),
    ("C2", C_HDR, C_WR),
]

hr("交叉验证: A1 -> C1 -> A2 -> C2,每段 %.0f 秒。"
   "**现在开始一直对着麦克风说话,不要停。**" % STAGE_SEC)

h = lib.capture_begin(MMIO_LEN, UIO)
if h == ffi.NULL:
    print("   [X] capture_begin 失败")
    raise SystemExit(1)

summary = []
try:
    for short, tag, writes in STAGES:
        for r, v in writes:
            wr(r, v)
        time.sleep(0.3)
        print()
        print("-" * 78)
        print("  [%s] %s" % (short, tag))
        print("-" * 78)
        print("  %6s  %9s %9s | %9s %9s   %s"
              % ("秒", "左 rms", "左 峰", "右 rms", "右 峰", "有声音?"))

        acc = []
        t0 = time.time()
        for b in range(int(STAGE_SEC * 100)):
            if lib.capture_block(h, rbuf, L) != L:
                print("  [X] capture_block 没填满")
                break
            (lr, lp), (rr, rp) = levels()
            acc.append((lr, lp, rr, rp))
            if (b + 1) % int(PRINT_EVERY * 100) == 0:
                loud = max(lp, rp) >= VOICE_PEAK
                print("  %6.1f  %9.1f %9.0f | %9.1f %9.0f   %s"
                      % (time.time() - t0, lr, lp, rr, rp,
                         "有 <<<" if loud else "-"))

        if acc:
            a = np.array(acc)
            tail = a[len(a) // 3:]
            lr = float(np.median(tail[:, 0]))
            lp = float(tail[:, 1].max())
            rr = float(np.median(tail[:, 2]))
            rp = float(tail[:, 3].max())
            n_loud = int(np.sum(np.maximum(tail[:, 1], tail[:, 3]) >= VOICE_PEAK))
            n_tot = len(tail)
            summary.append((short, lr, lp, rr, rp, n_loud, n_tot))
            print("  --> 左 rms %8.1f 峰 %8.0f | 右 rms %8.1f 峰 %8.0f"
                  % (lr, lp, rr, rp))
finally:
    lib.capture_end(h)

hr("汇总")
print("  %-4s %10s %10s %10s %10s %14s"
      % ("段", "左 rms", "左 峰", "右 rms", "右 峰", "响过的窗口"))
for row in summary:
    print("  %-4s %10.1f %10.0f %10.1f %10.0f   %4d / %-4d"
          % (row[0], row[1], row[2], row[3], row[4], row[5], row[6]))

print("""
怎么判读
--------
· A1 高 / C1 低 / A2 高 / C2 低(跟着寄存器来回跳)
  -> 铁证:读数由那两处寄存器决定,跟人说不说话无关。
     fir_live.py 里那四行 stream_reg_write(0x0B/0x0D=0x08, 0x0E/0x0F=0xD3)
     就是把话筒掐死的元凶,删掉即可。
· A1 高、之后一路低到底
  -> 是人后面不说了,这趟作废,换个人来配合或者换个测法。
· 四段都高 / 四段都低
  -> 寄存器这件事不是主因,得另找。
""")
print("   done")
