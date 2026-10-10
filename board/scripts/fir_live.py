# -*- coding: utf-8 -*-
"""真·实时：麦克风 → 核 → 耳机，**边录边放**，不是一段一段来。

================================ 为什么现在能做 ================================
三条都已经在这块板子上实测过，缺一条这件事就做不成：

  ① 核够快       实测 **0.317 µs/采样**（每块 8000 点），48 kHz 的预算 20.83 µs —— 余量 65.7 倍
  ② 延迟线跨块保持 分块跑和整块跑**逐位相同**，0 错配（fir_selftest.py 第 5 节）
  ③ 音频通路能全双工  录 0.2 s ‖ 播 0.2 s 同时跑，墙钟 0.203 s（＝max 不是 sum）
                 （audio_duplex_probe.py）

**所以不用重写核、不用重跑 Vivado。**

================================ 两个坑，都在这条 Python 上 ==================
### 坑一：原版 `play()` 每调一次白花 3.3 ms

第一次跑只有 **0.65 倍实时**（每块 10 ms 要花 15.5 ms）。核只占 214 µs，
所以不是核。拆开量：`play(L)` 13.31 ms、`record(L)` 9.94 ms —— 差 3.31 ms。
拟合出来 `play_ms = 3.47 + 0.0205·L`，**截距是每次调用的固定开销，和块长无关**，
所以块长再怎么调也摊不掉：每播 1 秒音频要花 `1 + 166/L` 秒，永远到不了 1。

源头在驱动的 C 源码里（`pynq/lib/_pynq/_audio/audio_adau1761.cpp`，板上就有）：
`play()` **每调一次**都给 codec 写 8 次 I2C —— 开头 4 次"开声"、结尾 4 次"静音"。
`audio_overhead_probe.py` 把它钉死了：`play(nsamples=0)` 3.447 ms、
`record(nsamples=0)` 0.119 ms，差 3.329 ms，和 `play(L)` 的超额对得上。
（顺带：每秒静音再开声 100 次，那本来就该有咔声。）

修法：`board/scripts/audio_stream.cpp`，把"一次一块"拆成"一个会话 + 很多块"，
开声/静音整个会话各做一次。实测 `stream_block(L)` 超出 −0.18 ms。
编库：`sh board/scripts/build_audio_stream.sh`（板上自带 gcc）。

### 坑二：**两个方向搬样要占两个核，板子只有两个核**

换完驱动之后是 **0.85~0.88 倍**，还是不到 1。而且 10 秒的麦克风音频里
**丢了 1.5 秒**（850 块 × 10 ms = 8.5 秒音频，墙钟却走了 10.02 秒）。

原因不是"慢"，是**没地方站**：这个 IP 没有 DMA，`capture_block` 和
`stream_block` 各自都在**空转轮询**状态寄存器，各自把**一个核吃到 100%**。

    audio_stream_test.py 第 3 节实测（只跑这两个方向，什么都不算）：
        录 200 块 ‖ 播 200 块：墙钟 1.974 s（实时 1.01x）
                                烧掉 CPU 3.930 s = **平均占 1.99 个核**

两个核全满，中间那点 Python（过核、转格式）被挤来挤去 —— 谁抢到 CPU
就要把某个空转线程挤开零点几毫秒，被挤开的那一头当场丢几个采样。

修法：**收和放本来就是同一个 I2S、同一个采样时钟、同一个状态寄存器**，
所以能在**一次状态脉冲里同时做完**（驱动自己的 `bypass()` 就是这么写的）。
`duplex_block()` 把两个方向合进一次调用 —— **整个搬样只占一个核**，
另一个核空出来给 Python。

### 坑三：**搬样那条线程上多一行 Python，就是丢音**

合起来之后还是 **0.81 倍**。拆开量那一条线程的每一段：

    ①cout→24位（摆成 C 要的那份）     1.66 ms   17%
    ②搬样 C 调用                     9.87 ms   99%   ← 这就是 I2S 时钟本身
    ③收到的 → int16                   0.08 ms    1%
    ④算 rms + 入队                    0.57 ms    6%
    io 整圈                         12.20 ms  122%

②是**一点余量都没有**的：它按设计就等于一个块的长度（48000 Hz、480 采样
= 10 ms）。所以①③④加起来那 2.3 ms 全是净亏。

关键在于**它不是"慢"，是"不在"**：那段时间里没人来取 I2S 的数据，
硬件 FIFO 只有几个采样的深度，超了就被后到的覆盖 —— 直接丢，
而且听不出是哪一块丢的，只是"咔"一下。

修法：**把①③④全挪到过核那条线程上**（它每块只用 2.4 ms，预算 10 ms），
`stage_io()` 里除了那次 C 调用什么也不做。

    实测 8 秒：io 整圈 12.20 → 9.86 ms，实时 0.81 → **1.00x，一块没丢**。

另外两条路试过，**都没用**（记下来省得再试一遍）：

  · 线程切换间隔 0.0005 → 0.005 → 0.02 → 0.1：实时倍率 0.98~0.99，不动。
  · 两条线程提成 SCHED_FIFO 实时（搬样 20、过核 10）：最大尖峰从 20 ms
    压到 11 ms，但丢的块数一样。说明那截亏损是**每块都会发生的固定开销**，
    不是被谁抢占 —— 所以只能靠"别在那条线程上干活"解决，调优先级解决不了。
    （实时优先级保留了，它确实把尾部尖峰压下去一半，成本为零。）

================================ 流水线长什么样 ================================
只有两条线程，不是三条：

    槽位 i 上有四块缓冲：
      abuf_in[i]   从 I2S 收到的交织立体声（int32）
      abuf_out[i]  要送去 I2S 放的交织立体声（int32）
      cin[i]       核的输入（DDR，int16）
      cout[i]      核的输出

    io 线程    取一个**算好、也摆成 24 位**的槽
              → duplex_block(收进 abuf_in ‖ 放出 abuf_out)   ← 一个核
              → 扔给 q1                     **只做这一件事**（见坑三）

    dsp 线程   取 q1 → abuf_in 转 16 位进 cin → k.run(cin, cout, L)
              → cout 转 24 位进 abuf_out → 扔回 q2            ← 另一个核

  `q2` 一开始就填满 NBUF 个槽（cout 全零），所以开头放的是一小段静音，
  等第一批块算完就接上了。

  **端到端延迟 ≈ NBUF × L/48000 + 核的群延迟 96 个采样。**
  NBUF=3、L=480 → 30 ms + 2 ms = 32 ms 左右，再加 codec/驱动自己的缓冲（没量）。
  想要更小就把 NBUF 调到 2（约 22 ms）—— 代价是流水线的余量变薄。

========================== 实时调参（2026-10-10 加） ==========================
**跑着的时候能随时改 4 段增益**，不用停音、不用重启。

    界面（板子自带的 Jupyter 网页）
      │  写 /dev/shm/fir_tune.json
      ▼
    dsp 线程（每 0.05 s 看一次）→ 核的 band_gain 寄存器
      │  写 /dev/shm/fir_status.json
      ▼
    界面（后台线程轮询，20 Hz）

通道本身在 `fir_tune.py`，那里讲了为什么用文件、**以及为什么是 /dev/shm 而不是
/tmp**（板上实测：/tmp 在 SD 卡上，一次写 2168 µs，会让每 30 秒多丢 17 块音）。
这里只记三条被前三个坑逼出来的规矩：

  · **读旋钮的活放在 dsp 线程上**，io 线程一个字不碰（它零余量）。
    而且不是每块都读，每 LIVE_POLL 秒读一次（默认 0.05 s）——够跟手，
    也不给 dsp 那条线程薄薄的余量添负担。
  · **改增益不会"啪"一声**：新值分 LIVE_RAMP 块线性走到位（默认 4 块 = 40 ms）。
    直接跳变是幅度上的一个阶跃，耳机里就是一声脆响。
  · **界面完全不碰 MMIO**。电平表读的是这条程序自己算好写出来的数，
    不是让浏览器去轮询寄存器 —— 后者会把"电平表能干什么"说大。

自检也跟着改了：原来是一次 `process()` 重算（只有一个增益），现在**按每块当时
实际用的那组参数**逐块重算，所以"边拖滑杆边跑"这一趟也照样能逐位比对。

================================ 怎么跑 ================================

**先编那条音频通路**（板上自带 gcc；只要编一次）：

    sh board/scripts/build_audio_stream.sh

再跑：

    echo xilinx | sudo -S env XILINX_XRT=/usr \\
      /usr/local/share/pynq-venv/bin/python3 fir_live.py

对着耳机麦说话，耳机里应该听到**处理过的、实时的**自己的声音。

环境变量：
    LIVE_L=480        每块采样数（默认 480 = 10 ms）
    LIVE_NBUF=3       槽位数 = **端到端延迟有几个块**（默认 3）
    LIVE_SECONDS=20   跑多少秒（默认 20；给 0 表示一直跑到 Ctrl-C）
    LIVE_BYPASS=1     直通（不压缩）—— 想做 A/B 对照时用
    LIVE_GAIN_DB="0,0,0,0"  **开机那一刻**四段增益，低→高，单位 dB，逗号分隔。
                      例：-6,0,6,12 = 低频压 6 dB、1~2 kHz 不动、高频抬 12 dB。
                      量程 ±42 dB（Q7.8 定的，超出直接报错不静默削）。
                      界面上拖过滑杆之后会盖掉它 —— 这个只管开机那一下。
    LIVE_VOLUME=62    播放音量（0~62）。**不给就用音源自己的默认：麦克风 40、
                      wav 62。** 麦克风那一路的输入增益是 +19 dB（codec 寄存器
                      R8/R9 = 0xB3），音量再给满，耳机→空气→麦克风这一圈的总增益
                      就超过 1，会啸叫（自激），进核的信号整段贴满量程。
                      加了每段增益之后这条更要注意：抬 +20 dB 再配满音量，
                      啸叫的阈值一下就到了。
                      **怎么判断正在啸叫**：状态文件里的 in_rms 贴到 20000 以上、
                      而且你不说话它也不掉 —— 正常说话大概是 300~2000。
                      真啸叫了先摘耳机，再 Ctrl-C / 把 LIVE_VOLUME 调小重开。
                      （2026-10-10 之前这个口的输入还带一个 31% 满量程的直流偏置，
                      静音时 in_rms 也读 10000 上下，已由 adc_to_i16 去掉。）
    LIVE_PIN=0        关掉绑核（默认绑：io→cpu0，dsp→cpu1）
    LIVE_RT_IO=20     搬样线程的 SCHED_FIFO 优先级（0=不开实时）
    LIVE_RT_DSP=10    过核线程的（0=不开）
    LIVE_SWITCH=0.0005  Python 线程切换间隔（秒）
    LIVE_POLL=0.05    多久看一次旋钮文件（秒）。0.05 = 20 Hz，够跟手。
    LIVE_RAMP=4       新增益分几块走到位（4 块 = 40 ms）。给 1 = 直接跳变，会咔。
    LIVE_STATUS=0.1   多久写一次状态文件（秒）。界面靠它画电平表。
                      10 Hz 对一根电平条够了；**这是整条循环里唯一花钱的动作**
                      （一次 23 µs），所以宁可少写几帧也不要写贵了。
    LIVE_SRC=wav      不从麦克风录，改放 board_input_48k.wav（循环）——
                      人在外面、没插麦的时候也能验流水线。
                      注意它仍然走真的 duplex_block（放音那一半是真的），
                      只是"收到什么"这一半被 wav 顶掉了。

跑完会做一次**逐位自检**：把这次真跑出来的每块输入和每块输出拼起来，
再用离线那条路（`process(chunk=L)`）重算一遍，两边**逐位比对**。
对上了，说明这条流水线在数值上就是原来那条路，没有因为分线程而错位。
"""

import fcntl
import os
import queue
import sys
import threading
import time
import wave

import numpy as np
from pynq import Overlay, allocate

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fir_core import FirMultiband, db_to_gain, gain_to_db

try:
    from fir_tune import (read_tune, write_status, clear_status,
                          TUNE_PATH, STATUS_PATH)
except ImportError as _e:
    raise SystemExit("缺 fir_tune.py —— 它要和本文件放同一个目录：%s" % _e)

# ---------------- 同时只允许跑一条 ----------------
# 两条 fir_live 同时跑有两个后果，第二个很难查：
#   ① 两边都想开 /dev/mem 里的同一个音频 IP，MMIO 互相踩；
#   ② **两条都往同一个状态文件里写**，界面读到的帧在两条之间跳来跳去 ——
#      实测见过块号从 3002 跳回 401、实时倍率从 0.92 跳到 0.87，
#      看上去像"这条通路在抽搐"，其实是两个进程在抢着汇报。
# 用文件锁挡住。进程一没锁就自动消失，不会像 pid 文件那样留下假的"还在跑"。
_lock_file = open("/dev/shm/fir_live.lock", "w")
try:
    fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    raise SystemExit(
        "已经有一条 fir_live 在跑了。\n"
        "  同时跑两条会互相踩 MMIO，界面上的数字也会在两条之间乱跳。\n"
        "  先停掉那条：pkill -INT -f fir_live.py")

# ---------------- 配置 ----------------
FS = 48000.0
L = int(os.environ.get("LIVE_L", "480"))            # 每块采样数
NBUF = int(os.environ.get("LIVE_NBUF", "3"))        # 槽位数 = 延迟几个块
SECONDS = float(os.environ.get("LIVE_SECONDS", "20"))
BYPASS = os.environ.get("LIVE_BYPASS", "0") not in ("0", "", "no")
PIN = os.environ.get("LIVE_PIN", "1") not in ("0", "", "no")
SRC = os.environ.get("LIVE_SRC", "mic").lower()
# LIVE_DIAG=1 时把收到的原始数据打前几块出来（查"电平为什么贴顶"用）
DIAG = os.environ.get("LIVE_DIAG", "0") not in ("0", "", "no")
# 麦克风那一路的输入增益是 +19 dB（codec R8/R9 = 0xB3）。耳机戴在头上时，
# 耳机→空气→麦克风这一圈很容易自激，**环路增益过 1 就是啸叫**。
# 所以没显式给 LIVE_VOLUME 时，麦克风音源默认 40、不是满 62；
# wav 音源里没有麦克风这一圈，给满没事。
# （踩过：音量 45 + 每段增益里有 +16 dB，麦克风立刻贴满量程 = 啸叫。）
_v = os.environ.get("LIVE_VOLUME")
VOLUME = int(_v) if _v is not None else (40 if SRC == "mic" else 62)

# 压缩参数。出厂默认（阈值 0.1 / 比 0.7）对分成四段之后的信号几乎没作用 ——
# 每一段只剩总能量的一小块，够不到 0.1。这组是按实测电平重定的，
# 和 fir_audio_loop.py 现在用的那组一致（阈值 0.030 / 比 0.5）。
THR_Q15 = 983
RATIO_Q15 = 16384


# 每段增益（低→高四段），开机时的值。dB 进、"Q7.8 原始整数"出（1.0 = 256）。
# 量程是 Q7.8 定的：±128 ≈ ±42 dB。**超了直接报错，不静默削** ——
# 静默削会让人以为"拖到 +40 还在变"，其实早就到底了。
GAIN_LIMIT_DB = 41.9


def _parse_gain_db(s):
    v = []
    for x in str(s).replace(" ", "").split(","):
        if x != "":
            v.append(float(x))
    if len(v) != 4:
        raise SystemExit('LIVE_GAIN_DB 要 4 个数（低→高），给的是 %r' % (s,))
    return v


GAIN_DB = _parse_gain_db(os.environ.get("LIVE_GAIN_DB", "0,0,0,0"))
for _g in GAIN_DB:
    if not -GAIN_LIMIT_DB <= _g <= GAIN_LIMIT_DB:
        raise SystemExit("LIVE_GAIN_DB 超量程（Q7.8 只到 ±%.1f dB）：%r"
                         % (GAIN_LIMIT_DB, GAIN_DB))

# 调参通道的三个节奏（见 fir_tune.py、以及上面"实时调参"那一节）
POLL_SEC = float(os.environ.get("LIVE_POLL", "0.05"))        # 多久看一次旋钮
STATUS_SEC = float(os.environ.get("LIVE_STATUS", "0.1"))     # 多久写一次状态
RAMP_BLOCKS = max(1, int(os.environ.get("LIVE_RAMP", "4")))  # 新增益几块走到位

CAPTURE_MAX = int(FS * 20)      # 自检最多留 20 秒的样本，别把内存吃光


def hr(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


class _Stop(Exception):
    pass


stop = threading.Event()
errors = []
t_io = []            # io 线程：整个循环（里面只有那一次 C 调用）
t_prep = []          # dsp 里：cout → 24 位交织立体声 + 算 rms
t_dsp_wall = []      # dsp 里：调 k.run 的墙钟（不是它自己报的 dt）
t_proc = []          # 每块 k.run 自己报的耗时
n_blocks = [0]
diag = [0]           # LIVE_DIAG=1 时，把收到的原始数据长什么样打前几块出来
in_rms = []
out_rms = []
cap_in = []          # 自检用：这次真跑过的每块输入
cap_out = []         # 对应的每块输出
captured = [0]       # 已经存了多少个采样（CAPTURE_MAX 封顶，别吃光内存）

# ---------------- 实时调参的状态（见 fir_tune.py） ----------------
# gain_cur 是**正在施加**的（浮点 Q7.8，为了能做斜坡），gain_dst 是滑块想要的那个。
# 两者之间由 ramp 逐块逼近，见 stage_dsp。
gain_cur = [float(db_to_gain(g)) for g in GAIN_DB]      # 施加中
gain_dst = [db_to_gain(g) for g in GAIN_DB]             # 目标（整数）
gain_dst_db = list(GAIN_DB)                             # 目标（dB，给界面看）
ramp_left = [0]                                         # 还要走几块（列表是为了能在函数里改）
thr_cur = [(THR_Q15,) * 4]
ratio_cur = [(RATIO_Q15,) * 4]
bypass_cur = [BYPASS]
tune_last = [None]                                      # 上一次读到的旋钮内容
t_poll = [0.0]
t_status = [0.0]
t_start = [time.time()]                                 # 主循环和状态文件都要用
applied_log = []     # 每块**实际**用了什么参数，自检按它逐块重算
events = []          # 从实时线程里攒下来的话，交给主线程打印
                     # （dsp 线程不许 print —— 它带着 SCHED_FIFO，卡在 I/O 上会丢音）


def qget(q):
    """带超时地取。**不要永久阻塞** —— 否则 stop 一置位，卡在 get 上的线程
    永远退不出来，主线程 join 会挂死。"""
    while not stop.is_set():
        try:
            return q.get(timeout=0.25)
        except queue.Empty:
            continue
    raise _Stop()


def _pin(cpu, rt_prio=0):
    """把一个线程绑到某个核上。这里是**两个线程两个核**，所以直接各占一个，
    省得调度器把它俩摆到同一个核上互相让。

    rt_prio>0 时再把它提成 SCHED_FIFO 实时线程。

    为什么需要：搬样那条 C 循环是**纯空转轮询**，它自己的中位耗时就是
    9.87 ms ≈ 一个块的长度 —— 也就是说这条线程按设计**一点余量都没有**，
    它就是 I2S 时钟本身。内核随便调度一下（kworker、别的进程、页错误），
    它晚零点几毫秒，RX FIFO 里那几十个采样就被后到的覆盖掉，直接丢音。
    实测：中位 9.87 ms、最大 11~21 ms，8 秒里正好丢 10 块。

    实时优先级要看得比普通任务高，但**比硬中断低**（板上内核线程多在 50），
    所以取 20/10。不这么做，普通优先级下永远会偶尔被挤。
    """
    if not PIN:
        return
    try:
        os.sched_setaffinity(0, {cpu})
    except Exception as e:                               # noqa: BLE001
        errors.append(("pin", repr(e)))
        return
    if rt_prio > 0:
        try:
            os.sched_setscheduler(0, os.SCHED_FIFO,
                                  os.sched_param(rt_prio))
        except Exception as e:                           # noqa: BLE001
            errors.append(("rt", "SCHED_FIFO 没设上 %r（权限？）" % (e,)))


# ---------------- 实时调参：读旋钮 / 写状态 ----------------

def db_to_q15(db):
    """dBFS -> Q1.15 原始整数。1.0 满量程 = 32768。

    门限用这个（它和核里的阈值一样是"相对满量程的比例"，不能大于 1），
    增益不用 —— 增益要能大于 1，走 fir_core.db_to_gain 的 Q7.8。
    **这两个别搞混**，混了就正好差 8 位。
    """
    return max(0, min(32767, int(round(32768.0 * (10.0 ** (db / 20.0))))))


def poll_tune(now):
    """看一次旋钮文件。**只读、只改目标值** —— 不碰核、不碰 io 线程。

    不是每块都读：每 POLL_SEC 秒一次（0.05 s = 20 Hz）就够跟手，
    也省得给 dsp 那条线程本来就薄的余量添负担。

    文件里认四个键，都可有可无，缺哪个就哪个不动：

        gain_db   [4 个数]  每段增益（dB），低→高
        thr_db    [4 个数]  压缩门限（dBFS）
        ratio     [4 个数]  压缩比 0~1
        bypass    0 / 1

    数值不合法就**保留当前值**，不抛 —— 界面发来一个 NaN，不该让音频停。
    """
    if now - t_poll[0] < POLL_SEC:
        return
    t_poll[0] = now

    d = read_tune()
    if d is None or d == tune_last[0]:
        return
    tune_last[0] = d
    what = []

    g = d.get("gain_db")
    if isinstance(g, (list, tuple)) and len(g) == 4:
        for j in range(4):
            try:
                v = float(g[j])
            except (TypeError, ValueError):
                continue
            if not v == v:                       # NaN
                continue
            v = max(-GAIN_LIMIT_DB, min(GAIN_LIMIT_DB, v))
            gain_dst_db[j] = v
            gain_dst[j] = db_to_gain(v)
        ramp_left[0] = RAMP_BLOCKS               # 有新目标 → 重新开始走
        what.append("增益 " + "/".join("%.1f" % x for x in gain_dst_db) + " dB")

    t = d.get("thr_db")
    if isinstance(t, (list, tuple)) and len(t) == 4:
        try:
            thr_cur[0] = tuple(db_to_q15(float(x)) for x in t)
            what.append("门限 " + "/".join("%.3f" % (db_to_q15(float(x)) / 32768.0)
                                          for x in t))
        except (TypeError, ValueError):
            pass

    r = d.get("ratio")
    if isinstance(r, (list, tuple)) and len(r) == 4:
        try:
            ratio_cur[0] = tuple(max(0, min(32767, int(round(float(x) * 32768.0))))
                                 for x in r)
            what.append("压缩比 " + "/".join("%.2f" % float(x) for x in r))
        except (TypeError, ValueError):
            pass

    if "bypass" in d:
        bypass_cur[0] = bool(d["bypass"])
        what.append("直通=%s" % bypass_cur[0])

    if what:
        # **不在这里 print** —— 这条线程带着 SCHED_FIFO，卡在 stdout 上就是丢音。
        # 攒起来给主线程打印。
        events.append("   [调参] " + "；".join(what))


def _win_rms(ts, k=10):
    """最近 k 块的合成有效值。**不是**最后一块那个数 ——
    单块 rms 跳得厉害，做电平表会一闪一闪，看不出在说话。"""
    a = ts[-k:]
    if not a:
        return 0.0
    return float(np.sqrt(np.mean(np.square(np.asarray(a, dtype=np.float64)))))


def _median_ms(ts, k=64):
    a = ts[-k:]
    if not a:
        return 0.0
    return float(np.median(np.asarray(a, dtype=np.float64)) * 1e3)


def publish_status(now, running=True, ok=None):
    """把"现在什么状态"写出去给界面看。

    界面要的三样东西都在这里：**当前增益**（回显）、**电平**（画表）、
    **实时倍率 / 丢块数**（判断这条通路还活着）。
    """
    n = n_blocks[0]
    audio_sec = n * block_sec
    wall = now - t_start[0]
    lost = int(max(0.0, wall - audio_sec) / block_sec)
    write_status({
        "t": now,
        "n": n,
        "running": bool(running),
        "ok": ok,
        "gain_db": list(gain_dst_db),
        "gain_applied_db": [gain_to_db(g) for g in
                            (int(round(x)) for x in gain_cur)],
        "bypass": bool(bypass_cur[0]),
        "in_rms": _win_rms(in_rms),
        "out_rms": _win_rms(out_rms),
        "rt": (audio_sec / wall) if wall > 0 else 0.0,
        "lost_blocks": lost,
        "io_ms": _median_ms(t_io),
        "dsp_ms": _median_ms(t_dsp_wall),
        "error": errors[0][1] if errors else None,
    })


def maybe_status(now):
    if now - t_status[0] < STATUS_SEC:
        return
    t_status[0] = now
    try:
        publish_status(now)
    except OSError:
        pass          # 写不出去（目录没了、盘满）不该影响音频


# ---------------- 24 位 ⇄ 16 位 ----------------
# codec 每个采样是 24 位，塞在 int32 的低 3 字节里；核是 Q1.15 的 int16。
# 两头差 8 位，所以就是移 8 位。不引入任何误差，丢掉的只是最后 8 位精度
# （48 dB 底噪，远低于 codec 自己的本底）。
_HALF24 = 1 << 23
_FULL24 = 1 << 24


def i32_to_i16_chan(buf, ch=0):
    return (buf.reshape(-1, 2)[:, ch] >> 8).astype(np.int16)


# 去直流：一阶高通（慢速跟踪均值再减掉）。状态跨块带着走，块边界连续无台阶。
# 系数 0.05/块 → 时间常数 20 块 = 200 ms → 转折约 0.8 Hz，50 Hz 以上几乎不受影响。
#
# ⚠️ 这个函数的**由来**要记清楚，因为它一开始的判断是错的。
#
# 2026-10-10 用 LIVE_DIAG=1 打出收到的原始缓冲区，四块都是这样：
#     dtype=int32  size=960  min=2583601  max=2629504  均值=2609092
# 一个 10 ms 块里交流只有 ±4.5e4，均值却有 2.6e6 —— 换算到核的刻度是
# 「均值 10192，交流 ±90」。当时据此判断"这个口的输入带着 31% 满量程的
# 直流偏置"，于是加了这一步。
#
# **这个判断后来被证伪了。** 那 2.6e6 不是麦克风的直流偏置，是
# `stream_begin` 里 4 次 I2C 写把 codec 的输入通路搞成满幅自激的结果
# （见 cap_probe2.py 和 `audio_stream.cpp` 里 stream_reg_write 那段注释）。
# 修好那一处之后，输入本来就是干净的。
#
# 那这一步还留不留？**留。** 因为它去掉的是真东西：在**没被搞坏**的通路上
# 实测（cap_probe.py）原始均值约 1.0e5 → 16 位域约 390，也就是 1.2% 满量程
# 的直流偏置确实存在，只是没到 31% 那种夸张程度。一阶高通去掉它是对的，
# 而且给核省下动态范围、也让 in_rms 真的反映声音。
# 只是**别把它当成当初那件事的解药** —— 那件事在 codec 那边。
_dc_est = [None]


def adc_to_i16(buf, ch=0):
    """麦克风那一块原始缓冲区 → 喂核的 int16，顺带去掉慢漂的直流。"""
    u = buf.reshape(-1, 2)[:, ch].astype(np.float64)
    m = float(u.mean())
    _dc_est[0] = m if _dc_est[0] is None else _dc_est[0] + 0.05 * (m - _dc_est[0])
    v = (u - _dc_est[0]) / 256.0
    return np.clip(np.rint(v), -32768.0, 32767.0).astype(np.int16)


def i16_to_i32_stereo(x16):
    v = x16.astype(np.int32) << 8
    out = np.empty(v.size * 2, dtype=np.int32)
    out[0::2] = v
    out[1::2] = v
    return out


def rms(x):
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


# ---------------- 起板子 ----------------
hr("0. 加载 overlay、音频、和那条自编的音频通路")
_HERE = os.path.dirname(os.path.abspath(__file__))


def _find(name, extra=()):
    for c in (os.path.join(os.getcwd(), name),
              os.path.join(_HERE, name)) + tuple(extra):
        if os.path.isfile(c):
            return os.path.abspath(c)
    return None


BIT = _find("fir.bit", (os.path.join(_HERE, "..", "overlay", "fir.bit"),))
if BIT is None:
    raise SystemExit("找不到 fir.bit")

SO = _find("libaudio_stream.so")
if SO is None:
    raise SystemExit(
        "找不到 libaudio_stream.so —— 先把音频通路编出来：\n"
        "  sh %s/build_audio_stream.sh\n"
        "（板上自带 gcc，不用交叉编译。）" % _HERE)

ol = Overlay(BIT)
audio = ol.audio_codec_ctrl_0
audio.configure()
audio.set_volume(VOLUME)
print("   音量 %d（上限 62）"
      "%s" % (VOLUME,
              "；麦克风音源，没显式给 LIVE_VOLUME 所以用了保守的 40 —— "
              "耳机戴头上时这一圈容易啸叫" if (SRC == "mic"
                                          and os.environ.get("LIVE_VOLUME") is None)
              else "；耳机麦插着时给满会啸叫"))

k = FirMultiband(ol.fir_multiband_0.mmio)

ffi = audio._ffi
MMIO_LEN = audio.mmio.length
UIO = audio.uio_index
IIC = audio.iic_index

ffi.cdef("""
    void* stream_begin(unsigned int mmap_size, unsigned int volume,
                       int uio_index, int iic_index);
    int   duplex_block(void* h, unsigned int* in_buf,
                       unsigned int* out_buf, unsigned int nsamples);
    void  stream_end(void* h);
""")
lib = ffi.dlopen(SO)

h = lib.stream_begin(MMIO_LEN, VOLUME, UIO, IIC)
if h == ffi.NULL:
    raise SystemExit("stream_begin 失败了（看板上打印的错）")

print("   用到的是：%s" % BIT)
print("   音频通路：%s" % SO)
print("   uio=%s iic=%s mmio.length=0x%x" % (UIO, IIC, MMIO_LEN))

block_sec = L / FS
print("   每块 %d 个采样 = %.2f ms；%d 个槽位 → 延迟约 %.0f ms"
      % (L, block_sec * 1000, NBUF, NBUF * block_sec * 1000))
print("   实时预算 %.2f µs/采样；核实测 0.317 µs/采样（余量 %.0f 倍）"
      % (1e6 / FS, (1e6 / FS) / 0.317))

# ---------------- 缓冲 ----------------
slots = []
for _ in range(NBUF):
    abuf_in = np.zeros(L * 2, dtype=np.int32)
    abuf_out = np.zeros(L * 2, dtype=np.int32)
    slots.append((
        ffi.cast("unsigned int*", ffi.from_buffer(abuf_in)),
        ffi.cast("unsigned int*", ffi.from_buffer(abuf_out)),
        abuf_in, abuf_out,
        allocate(shape=(L,), dtype=np.int16),
        allocate(shape=(L,), dtype=np.int16),
    ))
# slots[i] = (aptr_in, aptr_out, abuf_in, abuf_out, cin, cout)
print("   %d 个槽位，共 %d KB DDR（核那两份）" % (NBUF, NBUF * 2 * L * 2 // 1024))

q1 = queue.Queue()      # io → dsp
q2 = queue.Queue()      # dsp → io
for i in range(NBUF):
    q2.put(i)           # 预填：开头这几块放的是静音（cout 初始为 0）

# ---------------- 音源 ----------------
src_wav = None
if SRC == "wav":
    p = _find("board_input_48k.wav")
    if p is None:
        raise SystemExit("LIVE_SRC=wav 但找不到 board_input_48k.wav")
    with wave.open(p, "rb") as f:
        nch = f.getnchannels()
        raw = f.readframes(f.getnframes())
    a = np.frombuffer(raw, dtype="<i2").astype(np.int16)
    src_wav = a[::nch] if nch > 1 else a
    audio.deselect_inputs()
    print("   音源：%s（%d 个采样，循环放）" % (p, src_wav.size))
else:
    audio.select_microphone()
    time.sleep(0.3)
    print("   音源：麦克风")


# ---------------- 两条线程 ----------------
def stage_io():
    """搬样。**这个函数体里除了那次 C 调用，什么都没有。**

    这一条线程上多写一行 Python，I2S 那边就有零点几毫秒没人来取 ——
    硬件 FIFO 只有几个采样的深度，超了就被后到的覆盖，直接丢音，
    而且**听不出来是哪一块丢的**，只是隔一会儿"咔"一下。

    实测这个代价：每块多 0.09 ms 的 Python（就是把收到的转成 int16 那一下
    `np.copyto`），8 秒就丢 8 块。所以格式转换全都挪到另一条线程上去了 ——
    那边每块只用 2.4 ms，余量大把。

    换句话说：**别往这个函数里加东西，要加就加到 stage_dsp。**
    """
    _pin(0, RT_IO)
    while not stop.is_set():
        i = qget(q2)                      # 一个**已经算好、也已经摆成 24 位**的槽
        p_in, p_out = slots[i][0], slots[i][1]
        try:
            t0 = time.perf_counter()
            got = lib.duplex_block(h, p_in, p_out, L)
            t_io.append(time.perf_counter() - t0)
            if got != L:
                raise RuntimeError(
                    "duplex 只搬了 %d/%d 个采样（I2S 那边等超时了）" % (got, L))
        except _Stop:
            return
        except Exception as e:                               # noqa: BLE001
            errors.append(("io", repr(e)))
            stop.set()
            return
        q1.put(i)


def stage_dsp():
    """过核，外加所有格式转换。**能挪的活都在这儿，别挪回 io 那边去。**

    这块每块花 2.4 ms（过核 1.0 + 转换 1.4），块长 10 ms —— 用了 24%。
    所以这里多干点活是免费的，而 io 那边多干一点就是丢音。
    """
    _pin(1, RT_DSP)
    first = True
    pos = 0
    while not stop.is_set():
        try:
            i = qget(q1)
        except _Stop:
            return
        _pi, _po, abuf_in, b_out, cin, cout = slots[i]

        # 这一轮收到的（24 位交织）转成核要的 int16。
        # 原来在 io 线程上，0.08 ms/块 —— 就是它让 8 秒丢了 8 块。
        try:
            if src_wav is None:
                np.copyto(cin, adc_to_i16(abuf_in))
                if DIAG and diag[0] < 4:
                    # 把**收到的原始数据**原样打出来。别靠推理猜表示法 ——
                    # 量一下 min/max/均值。均值远大于交流摆幅就说明有直流偏置。
                    diag[0] += 1
                    _f = np.asarray(abuf_in.reshape(-1, 2)[:, 0],
                                    dtype=np.int64)
                    events.append(
                        "[诊断] 原始 dtype=%s size=%d min=%d max=%d 均值=%.0f"
                        "（摆幅 %d）| 去直流后 rms=%.1f | 直流估计值=%.0f"
                        % (abuf_in.dtype, abuf_in.size, int(_f.min()),
                           int(_f.max()), _f.mean(), int(_f.max() - _f.min()),
                           rms(cin), _dc_est[0]))
            else:
                # wav 模式：放音那一半是真的，只是"收到什么"被 wav 顶掉
                if pos + L > src_wav.size:
                    pos = 0
                np.copyto(cin, src_wav[pos:pos + L])
                pos += L
        except Exception as e:                               # noqa: BLE001
            errors.append(("dsp-in", repr(e)))
            stop.set()
            return

        # 每块先做两件事：看一次旋钮（最多 20 Hz），再让**施加值**往目标走一步。
        # 走斜坡而不是直接跳：不跳变幅度就在耳机里听不到"啪"那一声。
        # 这一段在 dsp 线程上 —— io 那边一个字不碰（它零余量）。
        t_now = time.time()
        poll_tune(t_now)
        if ramp_left[0] > 0:
            for j in range(4):
                gain_cur[j] += (gain_dst[j] - gain_cur[j]) / ramp_left[0]
            ramp_left[0] -= 1
        gains_int = tuple(int(round(x)) for x in gain_cur)

        t_d = time.perf_counter()
        try:
            dt, _spins = k.run(cin, cout, L, reset=first,
                               thr=thr_cur[0], ratio=ratio_cur[0],
                               gain=gains_int, bypass=bypass_cur[0])
        except Exception as e:                               # noqa: BLE001
            errors.append(("dsp", repr(e)))
            stop.set()
            return
        t_dsp_wall.append(time.perf_counter() - t_d)
        first = False
        t_proc.append(dt)
        n_blocks[0] += 1

        # 出核的转成 24 位交织立体声，**提前摆进 b_out** —— io 那边一拿到这个槽
        # 就直接把 b_out 交给 C，中间不做任何转换。必须在 q2.put(i) 之前做完。
        t_p = time.perf_counter()
        np.copyto(b_out, i16_to_i32_stereo(np.asarray(cout, dtype=np.int16)))
        out_rms.append(rms(cout[::16].astype(np.float64)))
        in_rms.append(rms(cin[::16].astype(np.float64)))
        t_prep.append(time.perf_counter() - t_p)

        # 留一份给逐位自检。**从第一块就开始留** —— 离线的重算要从 reset
        # 那一刻起对齐，中间少一块就对不上了。cap_in/cap_out 必须同进同出。
        # applied_log 也在这里跟着留：**它记的是这一块当时用的那组参数**，
        # 所以"跑的过程中调过旋钮"不会让自检失效。
        if captured[0] < CAPTURE_MAX:
            applied_log.append((gains_int, thr_cur[0], ratio_cur[0], bypass_cur[0]))
            cap_in.append(np.array(cin, dtype=np.int16))
            cap_out.append(np.array(cout, dtype=np.int16))
            captured[0] += L
        q2.put(i)

        # 写状态文件放在 q2.put 之**后** —— io 那边等着要这个槽，
        # 先把它交出去，再干这种不着急的活。
        maybe_status(t_now)


# 线程切换间隔。原来是 0.0005（0.5 ms）——"块才 10 ms，调细点"。
# 但实测那一版每一段 Python 都贵得离谱（两行 numpy 要 1.6 ms），
# 怀疑是间隔太细：**每 0.5 ms 强制把 GIL 交给另一个核再要回来一趟**。
# 搬样那个 C 调用是持 GIL 释放的（cffi ABI 模式），本来就不靠这个间隔让路，
# 所以放到几毫秒反而更省。这里做成可调的，好扫一遍。
sys.setswitchinterval(float(os.environ.get("LIVE_SWITCH", "0.0005")))

# 实时优先级：搬样那条给高一点（它零余量），过核那条给低一点（它只占 10%）。
# 设 0 就关掉（比如怀疑它把系统卡住了的时候）。
RT_IO = int(os.environ.get("LIVE_RT_IO", "20"))
RT_DSP = int(os.environ.get("LIVE_RT_DSP", "10"))

if MMIO_LEN is None:
    raise SystemExit("audio 的 mmio 没有 length 属性？驱动变了")

hr("1. 开始（说句话，耳机里应该能听到处理过的实时声音）")

# 启动时会**把上次留下的旋钮文件当成用户预设直接吃进去**（这是"先拧好再启动"
# 那个用法要的）。但遗留值可能是上一趟调完没收的，这一趟就带着它跑 ——
# 实测踩过：遗留的 +16 dB 高频 + 音量 45 + 麦克风 +19 dB 输入增益 = 直接啸叫。
# 所以这里做成**看得见的**警告，让人知道现在到底按哪组值在跑。
_d0 = read_tune()
if _d0 is not None:
    try:
        _g0 = "/".join("%.1f" % float(x) for x in _d0["gain_db"])
    except (KeyError, TypeError, ValueError):
        _g0 = None
    if _g0:
        print("   ⚠️ %s 里有上次留下的旋钮值：%s dB —— **这一趟就按它跑**。"
              % (TUNE_PATH, _g0))
        print("      不想要就先删掉它再启动，或者在界面里把滑杆拨回去。")

print("   每段增益 %s dB（Q7.8 原始值 %s）"
      % ("/".join("%.1f" % x for x in GAIN_DB), list(gain_dst)))
print("   压缩门限 %.3f，压缩比 %.2f，直通=%s"
      % (THR_Q15 / 32768.0, RATIO_Q15 / 32768.0, BYPASS))
print("   调参：界面写 %s，状态写在 %s" % (TUNE_PATH, STATUS_PATH))
if SECONDS > 0:
    print("   跑 %.0f 秒（也可以 Ctrl-C 提前停）" % SECONDS)
else:
    print("   一直跑，Ctrl-C 停")
print()

clear_status()                       # 先删旧状态，免得界面读到上一趟的数字
ths = [threading.Thread(target=f, name=n, daemon=True)
       for f, n in ((stage_io, "io"), (stage_dsp, "dsp"))]
t_start[0] = time.time()             # 线程一开跑就开始计时（状态文件要用）
for t in ths:
    t.start()

try:
    while not stop.is_set():
        time.sleep(0.25)
        while events:                # 实时线程攒下来的话，在这里打印
            print(events.pop(0))
        if SECONDS > 0 and time.time() - t_start[0] >= SECONDS:
            break
except KeyboardInterrupt:
    print("\n   Ctrl-C —— 收工")
finally:
    stop.set()
    for t in ths:
        t.join(timeout=3.0)
    if any(t.is_alive() for t in ths):
        # 有线程卡住了就别去动音频通路（可能正卡在一次 duplex_block 里）
        print("   ⚠️ 有线程没停下来，跳过收尾（那一下静音不做了）")
    else:
        lib.stream_end(h)
for _e in events:
    print(_e)

# 主循环在这之前就停了，所以这个时刻就是"这次跑到哪儿为止"。
# 下面自检要把每一块重新过一遍核，收尾还要关音频通路 —— 那都是**跑完之后**的事，
# 不能算进"墙钟"。所以先把时刻冻住，最后那一帧状态用它。
t_end = time.time()
wall = t_end - t_start[0]

# ---------------- 2. 结果 ----------------
hr("2. 跑得怎么样")
if errors:
    print("   ⚠️ 有线程报错：")
    for who, e in errors:
        print("      %-8s %s" % (who, e))

n = n_blocks[0]
audio_sec = n * block_sec
print("   跑完 %d 块，%.2f 秒音频，墙钟 %.2f 秒" % (n, audio_sec, wall))

# 这一条才是"有没有丢音"：搬样那一半是按 I2S 的时钟走的，**它每块应该正好
# 花 L/48000 秒**。如果墙钟比音频长，多出来的那截就是**丢掉的麦克风采样**
# （不是"慢"，是根本没被取走）—— 这是量得出来的，听不听得出来另说。
if wall > 0:
    print("   实时倍率 %.2fx（>1 就是跟得上）" % (audio_sec / wall))
    lost = wall - audio_sec
    if lost > block_sec * 1.5:
        print("   ⚠️ 墙钟比音频长 %.2f 秒 ≈ 丢了 %d 块 —— 耳机里会听到断续。"
              % (lost, int(lost / block_sec)))
        print("      看下面①那一行：它超过 100%% 就是搬样没跟上。")
    else:
        print("   → 墙钟和音频长度基本一致，**没有成片丢音**。")
print()

budget_ms = block_sec * 1e3
print("   每一段各花多久（块长 %.0f ms 就是预算是多少）：" % budget_ms)
print("   %-14s %10s %10s %10s %8s" % ("段", "中位", "最大", "占预算", "次数"))
for name, ts in (("①搬样(io线程全部)", t_io),
                 ("②过核+转换", t_dsp_wall),
                 ("②里面 cout→24位", t_prep),
                 ("过核(k.run自报)", t_proc)):
    if not ts:
        continue
    a = np.array(ts) * 1e3
    print("   %-14s %7.2f ms %7.2f ms %8.0f%% %8d"
          % (name, np.median(a), a.max(), np.median(a) / budget_ms * 100, a.size))
print("   （①和②是**两个核同时**跑的，加起来超过 100% 没关系 ——")
print("     看的是各自有没有过 100%。超了的那一段就是卡顿的来源。）")
if t_proc:
    a = np.array(t_proc) * 1e3
    over = int(np.count_nonzero(a > budget_ms))
    if over:
        print("   ⚠️ 有 %d 块过核超过了预算。" % over)

print()
if in_rms:
    print("   进核的电平：中位 %.0f  峰 %.0f（int16 域，满量程 32767）"
          % (np.median(in_rms), max(in_rms)))
    if np.median(in_rms) < 200:
        print("   ⚠️ 几乎是静音 —— 麦克风没插、插错口，或者没说话。")
        print("      插着耳机麦还这样：把耳机拔掉再插回去，**一次不一定够** ——")
        print("      要拔到电平表跳起来为止（板上那颗自动耳机开关 U41 只在插头")
        print("      插入那一下做检测；实测连续拔插到第 35.5 秒才跳）。")
    if np.median(in_rms) > 20000:
        print("   ⚠️ 电平顶到满量程了 —— 多半是啸叫（耳机→空气→麦克风自激），")
        print("      或者输入被削顶。真啸叫就摘耳机、把音量调小重开。")
if out_rms:
    print("   出核的电平：中位 %.0f" % np.median(out_rms))

# ---------------- 3. 逐位自检 ----------------
hr("3. 逐位自检：这条流水线 == 原来那条路")
print("""   做法：把这次真跑出来的输入块和输出块各拼起来，再用**每一块当时实际用的
   那组参数**逐块重算，逐位比。

   对上了说明：分线程没有让块错位、reset 只给了一次、缓冲没有被踩，
   而且**跑的过程中调过旋钮也没算错**（所以"边拖滑杆边跑"这一趟照样能自检）。
   这是"能出声"之外唯一还算硬的判据 —— 好不好听只能靠耳朵。
""")

ok = None
if len(cap_in) >= 3:
    x_cat = np.concatenate(cap_in)
    y_cat = np.concatenate(cap_out)
    nb = len(cap_in)
    print("   拿这次真跑过的 %d 块（%d 个采样）重算" % (nb, x_cat.size))

    n_change = 0
    for j in range(1, nb):
        if applied_log[j][0] != applied_log[j - 1][0]:
            n_change += 1
    if n_change:
        print("   这一趟增益**变过 %d 次** —— 正是「边拖滑杆边跑」要验的那一下"
              % n_change)

    # 逐块重算。**不能再用 k.process(x_cat, gain=...) 一把梭** ——
    # 那个接口一次只接受一组增益，中途变过就对不上了。
    ref = np.empty(x_cat.size, dtype=np.int16)
    ib = allocate(shape=(L,), dtype=np.int16)
    ob = allocate(shape=(L,), dtype=np.int16)
    try:
        for j in range(nb):
            gj, thj, raj, byj = applied_log[j]
            np.copyto(ib, x_cat[j * L:(j + 1) * L])
            k.run(ib, ob, L, reset=(j == 0), thr=thj, ratio=raj,
                  gain=gj, bypass=byj)
            np.copyto(ref[j * L:(j + 1) * L], ob)
    finally:
        del ib, ob

    bad = np.flatnonzero(ref != y_cat)
    if bad.size == 0:
        ok = True
        print("   [OK ] %d / %d 个采样逐位相同" % (y_cat.size, y_cat.size))
    else:
        ok = False
        first_bad = int(bad[0])
        print("   [FAIL] %d / %d 个采样对不上，第一个在偏移 %d（第 %d 块第 %d 个）"
              % (bad.size, y_cat.size, first_bad,
                 first_bad // L, first_bad % L))
else:
    print("   跑的块数太少（%d），跳过自检。" % len(cap_in))

# 收尾：状态文件里记一笔"跑完了"+自检结论。界面靠 running=False 判断收工，
# 不是靠"数字不动了"—— 停了和卡住在这条上长得一模一样。
#
# 时刻用 t_end（主循环停下的那一刻），**不能用 time.time()**：
# 上面那段自检会把每一块重新过一遍核，几秒到几十秒都有可能，
# 用现在的时间算出来的墙钟会把这段也算进去 —— 界面上就会显示成
# "实时倍率 0.87x、丢了 60 块"，而脚本自己打印的是 0.99x、丢 4 块。
# 同一趟跑出两个数，看的人只会以为通路坏了。
try:
    publish_status(t_end, running=False, ok=ok)
except OSError:
    pass

hr("4. 说明")
print("""
   · **好不好听只能靠耳朵。** 这个脚本能证明的是：延迟线没错位、没成片丢音、
     电平正常。音质、压缩的手感、有没有咔声 —— 这些没有数字判据。

   · **边跑边拖滑杆**：另开板子自带的 Jupyter 网页，跑
     `board/notebooks/w5_tuning_ui.ipynb`（4 条滑杆 + 电平表）。
     它和本脚本之间只有两个 JSON 文件，界面关掉不影响这条音频通路。

   · 想 A/B 对照：LIVE_BYPASS=1 跑一遍（直通），再 LIVE_BYPASS=0 跑一遍
     （压缩），别的参数都一样。
""")
print("""   · 延迟想再小：先调 LIVE_L 再调 LIVE_NBUF。端到端延迟 ≈ NBUF×L/48000 + 2 ms。
     LIVE_L=240 NBUF=2 → 约 12 ms。但每块有约 63 µs 的固定开销，
     块越小摊在每采样上越重 —— 实测 L=8 时是 7.93 µs/采样，离预算 20.83
     还有 2.6 倍，所以往下还有空间。

   · **一个核在做搬样，另一个只用了 10%**：搬样那条是空转轮询（这个 IP
     没有 DMA，只有状态寄存器握手），它把那个核吃到 99%，而且**余量为零** ——
     它本身就是 I2S 时钟。另一个核上（过核 + 所有格式转换）只花 2.4 ms / 10 ms。

     所以：**要加处理（多分几段、加别的效果），加得进去，代价几乎为零**；
     要压延迟，只能把 LIVE_L / LIVE_NBUF 调小。想让 CPU 完全别参与搬样，
     只有给音频 IP 加 DMA、或者把核做成 PL 内闭环（排期计划里的「① 全流式」）——
     那是要重跑 Vivado 的，不是调参数能到的。
""")
