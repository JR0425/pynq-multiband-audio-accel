# -*- coding: utf-8 -*-
"""cap_probe13.py —— 一次把「话筒没信号」切成两半:是板子的录音通路,还是 HP+Mic 那个口。

要回答的问题
------------
现在只知道「录进来的数一直是 2~4」,分不清下面两件事:

  A. 板子这条**录音通路本身**(ADC / I2S / AXI / 读寄存器)是坏的;
  B. 通路是好的,坏在 **HP+Mic 那个口** —— 也就是 U41 那颗自动耳机开关、插孔触点、
     或者耳机自己的麦。

**分辨的办法拿一个已知信号打进去。** 板子上还有一个 `Line-in` 口(纯输入),
拿电脑当音源、走公对公线进去,把 codec 切到 line-in (`audio.select_line_in()`):

  · Line-in 有反应  -> ADC + 数字通路是好的 -> 问题在 HP+Mic 那个口(B)
  · Line-in 也没反应 -> 这条录音通路本身有问题(A)

同一个脚本里顺带把话筒那一路按 **10-09 那次听到声音时的原样配置**再量一遍,
所以跑一趟两个问题都有答案。

⚠️ 为什么不能信 cap_probe12.py 那张增益扫描表(别再照它下结论)
------------------------------------------------------------
那张表的第一档调了 `audio.deselect_inputs()`,它把 **R4/R6(录音混音器)写成 0x00
= 静音**;而后面每一档**只写 0x0B/0x0D/0x0E/0x0F,再没有把 0x0A/0x0C 写回去**。
于是那 47 dB 的扫描**从头到尾都是在"录音混音器关着"的状态下量的** ——
当然一动不动。它证明的是"关着的时候没信号",不是"话筒坏了"。

依据是板子自己那份驱动,PYNQ v2.7.0 `pynq/lib/_pynq/_audio/audio_adau1761.cpp`:
`configure()` 写 R4/R6=0x00(源码注释:"Mute Mixer1 and Mixer2 here,
enable when MIC and Line In used"),`select_microphone()` / `select_line_in()`
才写 0x01。这一条 2026-10-14 从 GitHub 上的 v2.7.0 tag 核对过。

这个脚本只读 RX(用自编 C 库的 `capture_begin`/`capture_block`),
**不开 I2S 输出、不调 stream_begin** —— 耳机全程不会响,不存在啸叫。

跑法(板上,和 fir_live.py 同一条 sudo 命令):
    echo xilinx | sudo -S sh -c "cd /home/xilinx/jupyter_notebooks && \
        XILINX_XRT=/usr PYTHONIOENCODING=utf-8 \
        /usr/local/share/pynq-venv/bin/python3 cap_probe13.py"
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

PHASE_SEC = 5.0
PRINT_EVERY = 0.5       # 每 0.5 秒打一行(这个故障的尺度是「秒」,不是「毫秒」)


def hr(s):
    print()
    print("=" * 78)
    print(s)
    print("=" * 78)


# ---------------- I2C:只为回读 ----------------
fd = os.open("/dev/i2c-1", os.O_RDWR)
fcntl.ioctl(fd, 0x0703, ADDR)


def rd(reg):
    try:
        os.write(fd, bytes([0x40, reg]))
        return os.read(fd, 1)[0]
    except Exception:
        return None


def snap():
    """回读录音这一路的关键寄存器:名字见 PYNQ 的 audio_adau1761.h。

    R4/R6  = 0x0A/0x0C  录音混音器左/右控制 0 —— **bit0 是这一路的使能**
    R5/R7  = 0x0B/0x0D  录音混音器左/右控制 1 —— PYNQ:话筒 0x10 / line-in 0x07
    R8/R9  = 0x0E/0x0F  差分输入音量                    —— PYNQ 写 0xB3
    R10    = 0x10       话筒偏置 MICBIAS                —— PYNQ 写 0x01
    """
    out = []
    for reg in (0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10):
        v = rd(reg)
        out.append("%02X=%s" % (reg, "??" if v is None else "%02X" % v))
    return " ".join(out)


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
audio.configure()               # PLL + 整套 codec 配置(此刻 R4/R6 是 0x00,录音混音器关着)
print("   configure() 完了。寄存器:%s" % snap())
print("   ⚠️ configure() 把 R4/R6 写成 0x00 = 录音混音器**关着**,")
print("      所以它自己这个状态是量不到话筒的 —— 下面每一档才显式把它打开。")

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

    低 24 位是**二进制补码**,先做符号扩展 —— 不做的话一个跨零的近零信号
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


# ---------------- 四档 ----------------
PHASES = [
    ("P0  对照组 —— 录音混音器关着(R4/R6=0x00),什么都不该有",
     lambda: audio.deselect_inputs(),
     "什么都别做,安静 5 秒"),
    ("P1  话筒 —— PYNQ 原样(R4/R6=0x01, R5/R7=0x10, R8/R9=0xB3)= 10-09 听到声音那套",
     lambda: audio.select_microphone(),
     ">>> 对着耳机线上的麦说话 <<<"),
    ("P2  Line-in —— 电脑放音乐,经公对公线进板子 Line-in 口(R4/R6=0x01, R5/R7=0x07)",
     lambda: audio.select_line_in(),
     ">>> 让电脑放音乐(声音开大点) <<<"),
    ("P3  回到话筒 —— 重复 P1,看能不能重复出来",
     lambda: audio.select_microphone(),
     ">>> 再对着麦说话 <<<"),
]

hr("开始。四档,每档 %.0f 秒。请按屏幕提示做 —— 每档开头的第一行就是提示。" % PHASE_SEC)
print("   顺序:P0 安静 → P1 说话 → P2 放音乐 → P3 说话")

summary = []
for tag, action, prompt in PHASES:
    action()
    time.sleep(0.4)
    print()
    print("-" * 78)
    print("  %s" % tag)
    print("  寄存器:%s" % snap())
    print("  %s" % prompt)
    print("-" * 78)
    print("  %6s  %9s %9s | %9s %9s" % ("秒", "左 rms", "左 峰", "右 rms", "右 峰"))

    h = lib.capture_begin(MMIO_LEN, UIO)
    if h == ffi.NULL:
        print("  [X] capture_begin 失败")
        break
    t0 = time.time()
    acc = []
    try:
        for b in range(int(PHASE_SEC * 100)):
            if lib.capture_block(h, rbuf, L) != L:
                print("  [X] capture_block 没填满")
                break
            (lr, lp), (rr, rp) = levels()
            acc.append((lr, lp, rr, rp))
            if (b + 1) % int(PRINT_EVERY * 100) == 0:
                print("  %6.1f  %9.1f %9.0f | %9.1f %9.0f"
                      % (time.time() - t0, lr, lp, rr, rp))
    finally:
        lib.capture_end(h)

    if acc:
        a = np.array(acc)
        # 后半段(前 2 秒有 ADC 的直流建立瞬态,见 cap_probe11)
        tail = a[len(a) // 2:]
        lr = float(np.median(tail[:, 0]))
        lp = float(tail[:, 1].max())
        rr = float(np.median(tail[:, 2]))
        rp = float(tail[:, 3].max())
        summary.append((tag.split()[0], lr, lp, rr, rp))
        print("  --> 后半段中位:左 rms %8.1f 峰 %8.0f | 右 rms %8.1f 峰 %8.0f"
              % (lr, lp, rr, rp))

hr("汇总(后半段中位 rms / 最大峰)")
print("  %-4s %10s %10s %10s %10s" % ("档", "左 rms", "左 峰", "右 rms", "右 峰"))
for row in summary:
    print("  %-4s %10.1f %10.0f %10.1f %10.0f" % row)

print("""
怎么判读
--------
· **P2(Line-in)有明显读数、P1/P3 没有** → 板子的 ADC 和录音通路是好的,
  坏的是 HP+Mic 那个口:U41 那颗自动耳机开关没把话筒接到触点上,或者耳机自己的麦坏了。
  → 演示可以先用「电脑 → Line-in」这条(也完全不叫"退而求其次":音频是实时的,
    滑杆照样实时改)。
· **P2 也没有** → 先把公对公线两头插紧、电脑确认在放声,再重复一次;
  确认不是外面这些东西,那就是板子这条录音通路本身的问题,往下查 ADC/AXI。
· **P1/P3 有读数、P2 没有** → 话筒是好的,只是 Line-in 没配置对(那就不用管 P2)。
· **P0 有读数** → 说不通(混音器关着还能收到东西),说明读的数不是从模拟侧来的,
  先别信这一趟的任何结论。
· 峰在 500 以下基本都算"没有信号":人声进核正常是几百~几千,说话时峰能到上万。
""")
print("   done")
