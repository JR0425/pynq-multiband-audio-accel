# -*- coding: utf-8 -*-
"""cap_probe14.py —— 话筒这一段重做:从开始到结束,人**一直在说话**。

为什么要重做
------------
cap_probe13 那一趟已经证明了一件大事:**板子的录音通路是好的** ——
电脑经公对公线打 Line-in,读到峰 2559;同一套代码把录音混音器关掉,峰 2。
ADC / I2S / AXI / 读数这条路整条都通,这部分不用再查了。

它没证明的是话筒那一侧:因为台上打的那几行"该你说话了"的提示**只在串口那头**,
人看不到,所以没法确定那 5 秒里到底有没有出声。时序对不上的测量不算数。

这个脚本把时序问题**从根上消掉**:
    · 全程只做**一个动作** —— 一直对着麦克风说话,不停;
    · 脚本自己分三段换寄存器,不用人配合节奏;
    · 哪一段有声音、哪一段没有,事后看表就行。

三段(每段 8 秒,人一直说)
    A  PYNQ 原样话筒:R4/R6=0x01, R5/R7=0x10, R8/R9=0xB3  (= 10-09 那套配置)
    B  A 的基础上把差分输入音量从 +26.25 dB 拉到 +35.25 dB(0x0E/0x0F=0xFF)
    C  fir_live.py 现在实际在用的那套:R5/R7=0x08, R8/R9=0xD3

A/B 差 9 dB,C 是"去掉那 +20 dB 的 LDBOOST(0x10 -> 0x08)"。三段都静,才叫真没信号。

这个脚本只读 RX(自编 C 库的 capture_begin/capture_block),**不开 I2S 输出、
不调 stream_begin** —— 耳机全程不会响,不存在啸叫。

跑法(板上,和 fir_live.py 同一条 sudo 命令):
    echo xilinx | sudo -S sh -c "cd /home/xilinx/jupyter_notebooks && \
        XILINX_XRT=/usr PYTHONIOENCODING=utf-8 \
        /usr/local/share/pynq-venv/bin/python3 cap_probe14.py"
"""

import fcntl
import os
import time

import numpy as np
from pynq import Overlay

HERE = "/home/xilinx/jupyter_notebooks"
L = 480                 # 10 ms 一块
MMIO_LEN = 0x10000      # overlay 给音频 IP 的 range(build_fir.tcl)
ADDR = 0x3B             # ADAU1761 的 7 位 I2C 地址
FULL24 = 1 << 24
HALF24 = 1 << 23

STAGE_SEC = 8.0
PRINT_EVERY = 0.5
VOICE_PEAK = 500        # 峰过这个数,才算"确实有声音进到 ADC"


def hr(s):
    print()
    print("=" * 78)
    print(s)
    print("=" * 78)


# ---------------- I2C:读/写 ----------------
# 帧格式(和 cap_probe11/12/13 一致):
#   写 [0x40, reg_lo, data]      读 [0x40, reg_lo] 然后读 1 字节
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


def snap():
    cells = []
    for r in READ_REGS:
        v = rd(r)
        cells.append("%02X=%s" % (r, "??" if v is None else "%02X" % v))
    return " ".join(cells)


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


# ---------------- overlay / 驱动 ----------------
hr("0. 加载 overlay 与驱动")
ol = Overlay(os.path.join(HERE, "fir.bit"))
audio = ol.audio_codec_ctrl_0
audio.configure()
audio.select_microphone()       # R4/R6 = 0x01,录音混音器**开**
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

    低 24 位是**二进制补码**,必须先符号扩展 —— 不做的话,一个跨零的近零信号
    会被读成 rms ≈ 46341 的"满幅"(2026-10-14 栽过,见 PROGRESS.md)。
    """
    a = np.frombuffer(bytes(ffi.buffer(rbuf, 4 * 2 * L)), dtype=np.int32)
    c = a.reshape(-1, 2).astype(np.int64) & 0xFFFFFF
    s = np.where(c >= HALF24, c - FULL24, c).astype(np.float64) / 256.0
    out = []
    for ch in (0, 1):
        v = s[:, ch]
        v = v - v.mean()
        out.append((float(np.sqrt(np.mean(v * v))), float(np.abs(v).max())))
    return out


# ---------------- 三段 ----------------
STAGES = [
    ("A  PYNQ 原样话筒(0x0B/0x0D=0x10, 0x0E/0x0F=0xB3)= 10-09 那套",
     [(0x0B, 0x10), (0x0D, 0x10), (0x0E, 0xB3), (0x0F, 0xB3)]),
    ("B  A + 差分音量拉满(0x0E/0x0F=0xFF,+35.25 dB)",
     [(0x0E, 0xFF), (0x0F, 0xFF)]),
    ("C  fir_live.py 现在实际在用的(0x0B/0x0D=0x08, 0x0E/0x0F=0xD3)",
     [(0x0B, 0x08), (0x0D, 0x08), (0x0E, 0xD3), (0x0F, 0xD3)]),
]

hr("三段 × %.0f 秒。**现在开始一直对着麦克风说话,中间不要停。**" % STAGE_SEC)

h = lib.capture_begin(MMIO_LEN, UIO)
if h == ffi.NULL:
    print("   [X] capture_begin 失败")
    raise SystemExit(1)

summary = []
try:
    for tag, writes in STAGES:
        for r, v in writes:
            wr(r, v)
        time.sleep(0.3)
        print()
        print("-" * 78)
        print("  %s" % tag)
        print("  寄存器:%s" % snap())
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
            tail = a[len(a) // 3:]          # 丢掉开头的寄存器切换瞬态
            lr = float(np.median(tail[:, 0]))
            lp = float(tail[:, 1].max())
            rr = float(np.median(tail[:, 2]))
            rp = float(tail[:, 3].max())
            n_loud = int(np.sum(np.maximum(tail[:, 1], tail[:, 3]) >= VOICE_PEAK))
            n_tot = len(tail)
            summary.append((tag.split()[0], lr, lp, rr, rp, n_loud, n_tot))
            print("  --> 后半段:左 rms %8.1f 峰 %8.0f | 右 rms %8.1f 峰 %8.0f"
                  % (lr, lp, rr, rp))
            print("      %.1f 秒窗口里,响过(峰>=%d)的有 %d / %d 个"
                  % (float(n_tot) / 100.0, VOICE_PEAK, n_loud, n_tot))
finally:
    lib.capture_end(h)

hr("汇总(每段后 2/3 时间)")
print("  %-4s %10s %10s %10s %10s %14s"
      % ("段", "左 rms", "左 峰", "右 rms", "右 峰", "响过的窗口"))
for row in summary:
    print("  %-4s %10.1f %10.0f %10.1f %10.0f   %4d / %-4d"
          % (row[0], row[1], row[2], row[3], row[4], row[5], row[6]))

print("""
怎么判读
--------
· 三段里**有任何一段**的"响过的窗口"不是 0 -> 话筒是活的,问题只是增益/配置,
  照着那段把寄存器配上就行。
· 三段**全是 0**,而人确实一直在说话 -> 话筒这一侧确实没把声音送到 ADC。
  那就不是寄存器的事了,是 HP+Mic 那个口(插孔触点 / U41 那颗自动耳机开关 /
  或者耳机自己的麦)。
· 参考底噪:录音混音器关掉时峰是 2、rms 0.7(cap_probe13 的 P0)。
  人声进 ADC 正常应该是几百到几千,大声说话能上万。
· 峰一直贴在 50 上下 -> 那是 PGA 被放大的本底噪声,不是声音。
""")
print("   done")
