# -*- coding: utf-8 -*-
"""流式播放修好了没有？—— 拿原版 `play()` 当对照，只量时间。

============================ 这是在验什么 ============================
`audio_overhead_probe.py` 量出来：原版 `play()` **每调一次白花 3.33 ms**，
全是那 8 次 I2C 写造成的（每次约 416 µs），和块长无关。

于是 `board/scripts/audio_stream.cpp` 把它拆成"一个会话 + 很多块"：
开声和静音整个会话各做一次，中间的每一块只搬数据。

这个脚本就是那道**对照**：同一个块长，两条路各跑 N 块，比总时间。

    原版     每块 ≈ 3.33 ms + L/48000
    流式版   每块 ≈          L/48000      （开声/静音各一次，摊到 N 块上）

判据很硬：**流式版的每块耗时应该正好等于 L/48000 秒**。
如果它还是多出几毫秒，那说明开销不在 I2C 上，得回去查。

============================ 怎么跑 ============================
**先编库**（板上就有 gcc）：

    sh /home/xilinx/jupyter_notebooks/build_audio_stream.sh

再跑这个（板上，要 root，和 fir.bit 同目录）：

    echo xilinx | sudo -S env XILINX_XRT=/usr \
      /usr/local/share/pynq-venv/bin/python3 audio_stream_test.py

推给 TX 的**全是零**（静音），不会出声。
"""

import cffi
import os
import resource
import sys
import threading
import time

import numpy as np
from pynq import Overlay


def hr(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


_HERE = os.path.dirname(os.path.abspath(__file__))
L = int(os.environ.get("TEST_L", "480"))
NBLK = int(os.environ.get("TEST_NBLK", "50"))
VOLUME = 62

hr("0. 加载 overlay + 我们自己编的那个库")

# fir.bit 和 libaudio_stream.so 可能跟脚本不同目录，都找一找
def _find(name, extra=()):
    for c in (os.path.join(os.getcwd(), name),
              os.path.join(_HERE, name)) + tuple(extra):
        if os.path.isfile(c):
            return os.path.abspath(c)
    return None


bit = _find("fir.bit", (os.path.join(_HERE, "..", "overlay", "fir.bit"),))
if bit is None:
    raise SystemExit("找不到 fir.bit")
print("   fir.bit：            %s" % bit)

so = _find("libaudio_stream.so")
if so is None:
    raise SystemExit(
        "找不到 libaudio_stream.so —— 先把库编出来：\n"
        "  sh %s/build_audio_stream.sh\n"
        "（板上自带 gcc，不用交叉编译。编完再跑这个脚本。）" % _HERE)
print("   libaudio_stream.so： %s" % so)

ol = Overlay(bit)
audio = ol.audio_codec_ctrl_0
audio.configure()
audio.set_volume(0)                 # 保险：万一有直流，音量也是 0

ffi = cffi.FFI()
ffi.cdef("""
    void* stream_begin(unsigned int mmap_size, unsigned int volume,
                       int uio_index, int iic_index);
    int   stream_block(void* h, unsigned int* buf, unsigned int nsamples);
    void  stream_end(void* h);
    void* capture_begin(unsigned int mmap_size, int uio_index);
    int   capture_block(void* h, unsigned int* buf, unsigned int nsamples);
    void  capture_end(void* h);
""")
lib = ffi.dlopen(so)
print("   库加载好了，需要的函数都在。")

MMIO_LEN = audio.mmio.length
UIO = audio.uio_index
IIC = audio.iic_index
print("   uio=%s iic=%s mmio.length=0x%x" % (UIO, IIC, MMIO_LEN))

# 缓冲填零 —— 推给 TX 的就是静音
buf = np.zeros(max(L, 1) * 2, dtype=np.int32)
aptr = ffi.cast("unsigned int*", ffi.from_buffer(buf))

stock = audio._libaudio
stock_ptr = audio._ffi.cast(
    "unsigned int*", audio._ffi.from_buffer(buf))

# ---------------- 计时 ----------------
hr("1. 每个动作花多久")


def timeit(fn, n=7):
    best = None
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return best


exp = L / 48000.0

# --- 原版 ---
stock.play(MMIO_LEN, stock_ptr, 0, VOLUME, UIO, IIC)          # 热身
stock.play(MMIO_LEN, stock_ptr, L, VOLUME, UIO, IIC)
t_stock_0 = timeit(lambda: stock.play(MMIO_LEN, stock_ptr, 0, VOLUME, UIO, IIC))
t_stock_L = timeit(lambda: stock.play(MMIO_LEN, stock_ptr, L, VOLUME, UIO, IIC))

# --- 流式版 ---
h = lib.stream_begin(MMIO_LEN, VOLUME, UIO, IIC)
if h == ffi.NULL:
    raise SystemExit("stream_begin 返回 NULL —— 开声那一步失败了，看板上的输出")
print("   stream_begin 返回了句柄（开声只做这一次）")

# 先确认它真在搬数据：返回值必须等于请求的采样数。
# 不等于说明状态寄存器那一轮等超时了（I2S 没在跑），那是另一类故障。
got = lib.stream_block(h, aptr, L)
print("   stream_block(L=%d) 返回 %d   %s"
      % (L, got, "[OK ] 搬满了" if got == L else "⚠️ 没搬满，I2S 没在跑？"))

t_str_0 = timeit(lambda: lib.stream_block(h, aptr, 0))
t_str_L = timeit(lambda: lib.stream_block(h, aptr, L))

# --- 收摊 ---
t0 = time.perf_counter()
lib.stream_end(h)
t_end = time.perf_counter() - t0
print("   stream_end（静音 + 收摊，只做这一次）  %.3f ms" % (t_end * 1e3))

print()
print("   %-28s %10s %12s %12s" % ("动作", "实测", "期望", "超出"))
print("   " + "-" * 66)
print("   %-28s %8.3f ms %10s %10s"
      % ("原版 play(nsamples=0)", t_stock_0 * 1e3, "—", "—"))
print("   %-28s %8.3f ms %8.2f ms %+9.3f ms"
      % ("原版 play(L)", t_stock_L * 1e3, exp * 1e3, (t_stock_L - exp) * 1e3))
print("   %-28s %8.3f ms %10s %10s"
      % ("流式 stream_block(0)", t_str_0 * 1e3, "—", "—"))
print("   %-28s %8.3f ms %8.2f ms %+9.3f ms"
      % ("流式 stream_block(L)", t_str_L * 1e3, exp * 1e3, (t_str_L - exp) * 1e3))

# ---------------- 摊到 N 块上 ----------------
hr("2. 连续跑 %d 块（%.1f 秒音频）总共要多久" % (NBLK, NBLK * exp))

def run_stock():
    for _ in range(NBLK):
        stock.play(MMIO_LEN, stock_ptr, L, VOLUME, UIO, IIC)

def run_stream():
    h2 = lib.stream_begin(MMIO_LEN, VOLUME, UIO, IIC)
    for _ in range(NBLK):
        lib.stream_block(h2, aptr, L)
    lib.stream_end(h2)

t0 = time.perf_counter(); run_stock();  t_a = time.perf_counter() - t0
t0 = time.perf_counter(); run_stream(); t_b = time.perf_counter() - t0
audio_sec = NBLK * exp

print("   原版    %7.3f s   实时倍率 %.2fx   每块 %+.3f ms"
      % (t_a, audio_sec / t_a, (t_a / NBLK - exp) * 1e3))
print("   流式    %7.3f s   实时倍率 %.2fx   每块 %+.3f ms"
      % (t_b, audio_sec / t_b, (t_b / NBLK - exp) * 1e3))
print("   → 流式版比原版快 %.2f 倍" % (t_a / t_b))

# ---------------- 两个方向同时流式跑 ----------------
hr("3. 两个方向**同时**流式跑 —— 这是实时的上限")

# 这一段是给 `fir_live.py` 定位用的。
# 那两个 C 循环都是**空转轮询** MMIO（这个 IP 没有 DMA，只有状态寄存器握手），
# 所以每个方向都会把一个核吃到 100%。板子是**双核** A9 —— 两个方向跑起来
# 就是两个核都满了，中间那点 Python（过核、转格式）没有富余的核可用。
#
# 判据：如果**只有这两个方向**都跑不到 1.0 倍，那 `fir_live.py` 差的那截
# 就不是它的错，是这块板子在"CPU 搬样"这个架构下的天花板。

NDUP = int(os.environ.get("TEST_DUP_BLK", "100"))
h_cap = lib.capture_begin(MMIO_LEN, UIO)
h_play2 = lib.stream_begin(MMIO_LEN, VOLUME, UIO, IIC)
if h_cap == ffi.NULL or h_play2 == ffi.NULL:
    raise SystemExit("流的 begin 失败了，没法做这一段")

rx = np.zeros(L * 2, dtype=np.uint32)          # 收：unsigned int*
tx = np.zeros(L * 2, dtype=np.int32)           # 发：全零 = 静音
rx_ptr = ffi.cast("unsigned int*", ffi.from_buffer(rx))
tx_ptr = ffi.cast("unsigned int*", ffi.from_buffer(tx))

dup = {"rec_short": 0, "play_short": 0}


def _loop(which):
    t0 = time.perf_counter()
    for _ in range(NDUP):
        if which == "rec":
            got = lib.capture_block(h_cap, rx_ptr, L)
            if got != L:
                dup["rec_short"] += 1
        else:
            got = lib.stream_block(h_play2, tx_ptr, L)
            if got != L:
                dup["play_short"] += 1
    dup[which] = time.perf_counter() - t0


tb = threading.Thread(target=_loop, args=("rec",))
tp = threading.Thread(target=_loop, args=("play",))
_c0 = resource.getrusage(resource.RUSAGE_SELF)
wall0 = time.perf_counter()
tb.start()
tp.start()
tb.join()
tp.join()
wall_dup = time.perf_counter() - wall0
_c1 = resource.getrusage(resource.RUSAGE_SELF)
dup_cpu = ((_c1.ru_utime - _c0.ru_utime) + (_c1.ru_stime - _c0.ru_stime))

lib.capture_end(h_cap)
lib.stream_end(h_play2)

want = NDUP * exp
print("   录 %d 块（%.1f 秒音频） 线程内 %.3f s   每块 %+.3f ms"
      % (NDUP, want, dup["rec"], (dup["rec"] / NDUP - exp) * 1e3))
print("   播 %d 块（%.1f 秒音频） 线程内 %.3f s   每块 %+.3f ms"
      % (NDUP, want, dup["play"], (dup["play"] / NDUP - exp) * 1e3))
print("   两个一起：墙钟 %.3f s  →  **实时倍率 %.2fx**" % (wall_dup, want / wall_dup))
print("   这两段烧掉 CPU %.3f s  →  **平均占 %.2f 个核**（板子总共 2 个核）"
      % (dup_cpu, dup_cpu / wall_dup))
if dup["rec_short"] or dup["play_short"]:
    print("   ⚠️ 有块没搬满（等超时了）：录 %d 块、播 %d 块"
          % (dup["rec_short"], dup["play_short"]))
try:
    print("   跑完时的系统负载（1/5/15 分钟）：%s" % (os.getloadavg(),))
except OSError:
    pass
print("""
   怎么读这一节：

     实时倍率接近 1.0 以上，而且「平均占几个核」明显**小于 2**
       → **CPU 还有富余**。`fir_live.py` 差的那截是它自己的 Python 开销
         （过核、转格式）和线程调度没排好，可以在它那边调。

     实时倍率到不了 1.0，或者「平均占几个核」**贴着 2**
       → 这块板子在这个架构下的天花板就在这儿。两个方向都是空转轮询 MMIO，
         把两个核都吃干了，中间那点 Python 没有多余的核。要真的到 1.0，
         得让 CPU 别参与搬样 —— 给音频 IP 加 DMA，或者把核做成 PL 内闭环
         （排期计划里的「① 全流式」），那是要重跑 Vivado 的。
         在此之前 `fir_live.py` 只能"尽量少丢"，做不到一点不丢。

   ⚠️ 要留意的是「一个核」这条线：这里只有 2 个核，却要摆
      录、算、放**三段**。如果录和放各占满一个，那"算"就没地方站了 ——
      它每次被调度上去，都要把某个空转线程挤开零点几毫秒，
      被挤开的那一头当场就丢几个采样。""")

# ---------------- 怎么读 ----------------
hr("4. 怎么读前面那些数")
print("""   要看的只有一个数：**「流式 stream_block(L)」那一行的「超出」**。

     接近 0（±0.2 ms 以内）
       → 修好了。原版白花的 3.33 ms 确实就是那 8 次 I2C 写，
         拆成"会话开头开声一次、结尾静音一次"就没了。
         接下来 `fir_live.py` 换用这个库，实时倍率应该能过 1。

     还是好几毫秒
       → 开销不在 I2C 上，这个判断被推翻了。回去看 `audio_overhead_probe.py`
         第 2 节：如果 `play(0) − record(0)` 也是好几毫秒，那就还是 I2C；
         如果那两个数都很小、只有 `play(L)` 慢，那说明是 I2S TX 那边有额外的
         等待或握手 —— 那是 PL 里 `audio_codec_ctrl` 的寄存器行为，
         得去查它的手册，是另一件事。

   第 2 节那个「每块 +ms」是同一件事摊到多块上的说法：原版每一块都多花，
   流式版只在**整个会话**的头尾各花一次（第 1 节的 stream_end 那个数）。""")
print()
print("""   ⚠️ 这个脚本只证明「快了」，**不证明「响了」**。
      真出声要靠 `fir_live.py`，而且好不好听只能靠耳朵。
      这里推给 TX 的全是零，所以就算通路是通的，也听不到任何东西。""")
