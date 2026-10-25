"""fir_dma_core overlay：v25 核插进 PS 的循环里

链路：

    codec → 自写 I2S → S2MM → DDR(录音块) ─ PS ─→ DDR(播放块) → MM2S → I2S → codec
                                              └→ 调 v25 核 ┘

核是「自己去内存搬」的 AXI4 主端，不是流设备，所以只能由 PS 一块块调它 ——
这就是"插进 PS 的循环"的字面意思。PS 每块只做三件事：把录音块换算成 int16、
调一次核、把结果换算回流格式。数据搬运全在 DMA 和核自己的 master 上。

两处格式换算（写错就是静音或者爆音，不报错）：
    流里的字：24 位有符号采样放在**高 24 位**，`word >> 8` 才是采样值
    核吃的：  int16
    所以进核前 `x >> 16`，出核后 `y << 16`。
    左右声道**不拆**：核按一条数字流处理，交织着送进去和出货版一样
    （FIR 对每个采样独立，不关心它属于哪个声道）。

用法（板上，要 root，bit/hwh 要和本文件放同一个目录）：

    echo xilinx | sudo -S env XILINX_XRT=/usr chrt -f 60 \\
        /usr/local/share/pynq-venv/bin/python3 dma_fir_loop.py --secs 60

    # 只量延迟、不要 60 秒：
    ... dma_fir_loop.py --secs 10

`chrt -f 60` 不是可选的：板子上有个 `ksoftirqd-rt-refresh.service` 把 ksoftirqd
和十几个 kworker 提成了 SCHED_FIFO 50（当初查死机时留的），普通优先级的进程会被
它们一口气挤开 10～30 ms。实测不套 chrt 时 60 秒断流 60～650 次，套上之后 0 次。
本线程每块都 sleep，ksoftirqd 照样拿得到 CPU，不会重演「实时线程永不阻塞把
ksoftirqd 饿死」那条路。
"""

import argparse
import os
import signal
import sys
import time

import numpy as np
from pynq import allocate

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dma_audio_probe as P                       # noqa: E402
from fir_core import FirMultiband, db_to_gain     # noqa: E402
from fir_tune import read_tune, write_status, clear_status   # noqa: E402

FS = P.FS


# ---------------------------------------------------------------- 验配网页那条链路
# 出货的 fir_live.py 自带一份 poll_tune/publish_status，但那是出货路径，不去动它
# —— 这里按 fir_tune.py 那套文件协议重写一小份，网页看到的旋钮和电平表和以前
# 一样：
#
#   /dev/shm/fir_tune.json     网页写 → 这里读（要拧的旋钮）
#   /dev/shm/fir_status.json   这里写 → 网页读（当前状态 + 电平）
#
# 旋钮每 0.05 s 看一次（20 Hz，跟手就够了）；状态每 0.1 s 写一次。
# 写状态在 /dev/shm 上是 23 µs 级的，对 4.9 ms 一块不影响。

GAIN_LIMIT_DB = 41.9      # Q7.8 能表示到 ±42 dB，留一点边
POLL_SEC = 0.05
# 给网页写状态的间隔。能用环境变量关掉是为了做 A/B：300 秒那趟里每 100 ms
# 出现一次 4.85 ms 的 `stat` 卡顿，正好是这个间隔，得先排除它。
STATUS_SEC = float(os.environ.get("DMA_STATUS_SEC", "0.1"))
RAMP_BLOCKS = 4           # 新增益几块走到位：一步跳到位会"咔"


def db_to_q15(db):
    return max(0, min(32767, int(round(32768.0 * (10.0 ** (db / 20.0))))))


def _rms(a):
    # 先转 float64 再平方：int16 直接平方会溢出，读出来是个看着挺大的假数
    return float(np.sqrt(np.mean(np.asarray(a, dtype=np.float64) ** 2)))


class Tuner:
    """网页旋钮 → 每块交给核的那组参数。

    核的四个寄存器每次 ap_start 都重新锁存，所以调参只要改下一次 run() 传的
    值，不用碰 PL。增益单独做斜坡：网页上拖滑块是一次跳好几 dB 的，直接跳
    会在耳机里"咔"一声，分 RAMP_BLOCKS 块走过去就听不见了。
    """

    def __init__(self, bypass=False):
        self.gain_db = [0.0] * 4
        self.gain_dst = [FirMultiband.DEF_GAIN[0]] * 4
        self.gain_cur = [float(FirMultiband.DEF_GAIN[0])] * 4
        self.ramp_left = 0
        self.thr = list(FirMultiband.DEF_THR)       # 核吃的是 Q15 原始值
        self.ratio = list(FirMultiband.DEF_RATIO)
        self.atk = [0.0] * 4                        # 毫秒，0 = 不启用
        self.rel = [0.0] * 4
        self.bypass = bool(bypass)
        self._last = None
        self._status_last = None
        self._t_poll = 0.0
        self._t_status = 0.0
        self._t_start = None
        self._in_hist = []
        self._out_hist = []

    # ---- 读旋钮 ----
    @staticmethod
    def _clamped(vals, lo, hi):
        """四个数 → 削到 [lo, hi]；有一个不是数就返回 None（保留旧值）。

        NaN 要在这里就挡住：min()/max() 碰到 NaN 不返回 NaN，而是原样留下
        另一个数（比较永远为假），所以"先削再用 x==x 查 NaN"会让一个 NaN
        变成满量程增益 —— 耳机里就是一声巨响。
        """
        try:
            v = [float(x) for x in vals]
        except (TypeError, ValueError):
            return None
        if not all(x == x for x in v):
            return None
        return [max(lo, min(hi, x)) for x in v]

    def poll(self, now):
        """看一次旋钮文件，返回这一轮改了什么（给主循环打印，这里不 print）。"""
        if now - self._t_poll < POLL_SEC:
            return None
        self._t_poll = now
        d = read_tune()
        if d is None or d == self._last:
            return None
        self._last = d
        what = []

        g = d.get("gain_db")
        if isinstance(g, (list, tuple)) and len(g) == 4:
            v = self._clamped(g, -GAIN_LIMIT_DB, GAIN_LIMIT_DB)
            if v is not None:
                self.gain_db = v
                self.gain_dst = [db_to_gain(x) for x in v]
                self.ramp_left = RAMP_BLOCKS
                what.append("增益 " + "/".join("%.1f" % x for x in v) + " dB")

        t = d.get("thr_db")
        if isinstance(t, (list, tuple)) and len(t) == 4:
            v = self._clamped(t, -999.0, 999.0)      # 真正的量程由 db_to_q15 卡
            if v is not None:
                self.thr = [db_to_q15(x) for x in v]
                what.append("阈值 " + "/".join("%.3f" % (x / 32768.0) for x in self.thr))

        r = d.get("ratio")
        if isinstance(r, (list, tuple)) and len(r) == 4:
            v = self._clamped(r, 0.0, 1.0)
            if v is not None:
                self.ratio = [int(round(x * 32768.0)) for x in v]
                what.append("压缩比 " + "/".join("%.2f" % x for x in v))

        a = d.get("attack_ms")
        if isinstance(a, (list, tuple)) and len(a) == 4:
            v = self._clamped(a, 0.0, 2000.0)
            if v is not None:
                self.atk = v
                what.append("启动 " + "/".join("%.1f" % x for x in v) + " ms")

        rl = d.get("release_ms")
        if isinstance(rl, (list, tuple)) and len(rl) == 4:
            v = self._clamped(rl, 0.0, 2000.0)
            if v is not None:
                self.rel = v
                what.append("释放 " + "/".join("%.1f" % x for x in v) + " ms")

        if "bypass" in d:
            self.bypass = bool(d["bypass"])
            what.append("直通=%s" % self.bypass)

        return what or None

    # ---- 给核的那组数 ----
    def params(self):
        if self.ramp_left > 0:
            for j in range(4):
                self.gain_cur[j] += (self.gain_dst[j] - self.gain_cur[j]) / self.ramp_left
            self.ramp_left -= 1
        return dict(thr=tuple(self.thr), ratio=tuple(self.ratio),
                    gain=tuple(int(round(x)) for x in self.gain_cur),
                    attack=tuple(self.atk), release=tuple(self.rel),
                    bypass=self.bypass)

    # ---- 写状态给网页 ----
    def status_due(self, now):
        return now - self._t_status >= STATUS_SEC

    def publish(self, now, n, blk_sec, in_rms, out_rms, lat_ms, err=None,
                l=None, nbuf=None, io_ms=None, dsp_ms=None):
        if now - self._t_status < STATUS_SEC:
            return
        self._t_status = now
        if self._t_start is None:
            self._t_start = now
        self._in_hist = (self._in_hist + [in_rms])[-10:]
        self._out_hist = (self._out_hist + [out_rms])[-10:]
        audio = n * blk_sec
        wall = now - self._t_start
        lost = int(max(0.0, wall - audio) / blk_sec)
        st = {
            "t": now,
            "n": n,
            "pid": os.getpid(),
            "running": True,
            "ok": None,
            "gain_db": list(self.gain_db),
            "gain_applied_db": [20.0 * float(np.log10(max(x, 1e-9) / 256.0))
                                for x in self.gain_cur],
            "bypass": self.bypass,
            "attack_ms": list(self.atk),
            "release_ms": list(self.rel),
            "in_rms": float(np.sqrt(np.mean(np.square(self._in_hist)))),
            "out_rms": float(np.sqrt(np.mean(np.square(self._out_hist)))),
            "rt": (audio / wall) if wall > 0 else 0.0,
            "lost_blocks": lost,
            "lat_ms": round(lat_ms, 2),
            "error": err,
        }
        if l is not None:
            st["l"] = int(l)
            st["nbuf"] = int(nbuf or 0)
        if io_ms is not None:
            st["io_ms"] = float(io_ms)
        if dsp_ms is not None:
            st["dsp_ms"] = float(dsp_ms)
        if self._status_last is not None:
            self._status_last.update(st)
            st = self._status_last
        else:
            self._status_last = st
        try:
            write_status(st)
        except OSError:
            pass          # 写不出去不该把音频带下水

    def finish(self):
        """收到停止信号后把最后一个状态改成 running=False，网页才会收起"正在跑"。"""
        st = dict(self._status_last or {})
        st.update({"t": time.time(), "pid": os.getpid(),
                   "running": False, "ok": True, "error": None})
        try:
            write_status(st)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default="fir_dma_core.bit")
    ap.add_argument("--hwh", default="fir_dma_core.hwh")
    ap.add_argument("--frames", type=int, default=240,
                    help="一个块的立体声帧数。默认 240（4.9 ms）：块长直接进"
                         "端到端延迟，480 帧时一块就要 9.8 ms，还没算积压")
    ap.add_argument("--secs", type=float, default=60.0)
    ap.add_argument("--live", action="store_true",
                    help="网页演示用：一直跑到收到 SIGINT，不打印每块的表，"
                         "状态文件里把 l/nbuf/io_ms/dsp_ms 也写给网页")
    ap.add_argument("--bypass", action="store_true",
                    help="关掉压缩（直通）。核对「过核」这一步有没有接错时用")
    ap.add_argument("--tx-low", type=int, default=576,
                    help="播放 FIFO 水位上限（字）。默认 576（5.9 ms）：实测单块 "
                         "PS 时间偶尔到 4.7～4.9 ms（核 DONE 那一下），480（4.4 ms "
                         "余量）时 120 秒丢过 12 个采样，576 时 120 秒 0/0。给 0 就不控制")
    ap.add_argument("--prof", action="store_true",
                    help="分段落记 CPU 时间（process_time 差值），找谁在空转时用")
    args = ap.parse_args()

    P.BITFILE = args.bit
    P.HWHFILE = args.hwh

    # 循环里每块都会建几个临时对象，分代 GC 偶尔停下来扫一遍（d 段那边实测
    # 有过 10.95 ms 的离群点）。这一停对整条链路是致命的：录音 FIFO 只进不出
    # 的那几毫秒会变成**永久的**积压（进出一对一，水平是个中性平衡），之后
    # 整段都背着那几毫秒。缓冲区全是预分配的，临时对象靠引用计数就回收了，
    # 关掉 GC 不影响内存。
    import gc
    gc.disable()

    dma, ctrl, stat = P.load()
    ol = _overlay()
    if "fir_multiband_0" not in ol.ip_dict:
        print("ERROR: overlay 里没有 fir_multiband_0 —— 加载的是回环版 fir_dma.bit？"
              "带核的是 fir_dma_core.bit")
        return 1
    fir = FirMultiband(ol.fir_multiband_0.mmio)
    print("  核  fir_multiband_0  @%s" % hex(ol.ip_dict["fir_multiband_0"]["phys_addr"]))

    return live(dma, ctrl, stat, fir, args.frames, args.secs, args.bypass,
                args.tx_low, args.prof, live_mode=args.live)


# ---------------------------------------------------------------- overlay 取句柄
# dma_audio_probe.load() 只返回 DMA 和两颗 gpio，核的句柄要自己再拿一次。
# pynq 默认 overlay 是单例，重复构造不会重新下载位流。

_OL = None


def _overlay():
    global _OL
    if _OL is None:
        from pynq import Overlay
        _OL = Overlay(P.BITFILE)
    return _OL


# ---------------------------------------------------------------- 主循环

def live(dma, ctrl, stat, fir, frames, secs, bypass, tx_low=480, prof=False,
         live_mode=False):
    print("\n== 整条实时链路（过核）话筒 → DDR → 核 → DDR → 耳机 ==")

    nw = frames * 2
    NBUF = 4                     # 比纯回环多一格：中间多了一步调核的时间
    cap  = [allocate(shape=(nw,), dtype=np.int32) for _ in range(NBUF)]
    ply  = [allocate(shape=(nw,), dtype=np.int32) for _ in range(NBUF)]
    fin  = [allocate(shape=(nw,), dtype=np.int16) for _ in range(NBUF)]
    fout = [allocate(shape=(nw,), dtype=np.int16) for _ in range(NBUF)]
    for b in ply:
        b[:] = 0
        b.flush()

    # 和 dma_audio_probe.stage_d 一样：缓冲区先分配好再开流，allocate 的几十
    # 毫秒如果压在开流之后，录音 FIFO 会直接顶满、而且再也降不下来。
    P.codec_init()
    P.set_ctrl(ctrl, P.CTRL_FLUSH)
    time.sleep(0.02)
    bits = P.CTRL_RUN | P.blk_bits(frames)
    P.cap_start(dma, cap[0])              # flush 还压着就先挂上，省掉几毫秒
    P.set_ctrl(ctrl, bits | P.CTRL_CLR)
    time.sleep(0.002)
    P.set_ctrl(ctrl, bits)

    nblk = (1 << 30) if live_mode else int(secs * FS / frames)
    proc, pend, fir_ms = [], [], []
    psl = []                     # PS 真正花在这块上的时间（算嘴到耳用这一个）
    cap_seen, tx_seen = [], []
    ovf_d = und_d = 0
    ovf_idx, ovf_max = [], 0
    tuner = Tuner(bypass)
    clear_status()               # 不删的话网页会先把上一趟的数字当成现在的
    # 网页点「停止」发的是 SIGINT。主循环就在主线程里，用标志位退出 ——
    # 这样等最后一块放完、关流、把 running=False 写回去这几步还能照常跑。
    stop = [False]

    def _on_int(_signum, _frame):
        stop[0] = True

    signal.signal(signal.SIGINT, _on_int)
    blk_ms = frames / P.FS_ACT * 1000.0
    # 核的组延迟按**字**算：左右声道不拆，96 个字 = 48 帧
    gd_ms = FirMultiband.GROUP_DELAY / 2.0 / P.FS_ACT * 1000.0

    def to_int16(src, dst):
        # 流字的高 16 位就是 int16 的那个数（24 位采样去掉低 8 位），
        # 直接右移再赋值，别用 out=/casting= —— numpy 对 int32→int16 的
        # ufunc 输出要求 same_kind，会直接报错。
        #
        # `.view(np.ndarray)` 不是多余的：numpy 在 PynqBuffer（ndarray 子类）
        # 上跑 ufunc 会走子类那条路，实测 480 点要 283 µs，纯 ndarray 只要
        # 51 µs —— 每块两次换算就是 0.47 ms 白扔。view 出来的是同一块内存。
        dst.view(np.ndarray)[:] = src.view(np.ndarray) >> 16

    def to_stream(src, dst):
        # int16 先在 int32 里左移，再写回流格式；直接在 int16 里移会溢出
        dst.view(np.ndarray)[:] = src.view(np.ndarray).astype(np.int32) << 16

    # 先垫一块：这之前的 underrun 是"还没东西可放"，不算丢块
    # 放音交出去就走，下一块采完再确认上一块放完 —— 和 dma_audio_probe.stage_d
    # 同一个结构，原因见那里的注释（播放 FIFO 浅了之后，PS 干等着会让录音侧
    # 一直蓄着两块）。
    t0 = time.perf_counter()
    import resource
    _ru0 = resource.getrusage(resource.RUSAGE_SELF)
    P.cap_wait(dma, cap[0])               # 这一块在放开 flush 之前就挂好了
    lvl0, lvl1 = P.drain_cap(dma, stat, cap[3])
    if lvl0 != lvl1:
        print("  开局排空录音积压：%d → %d 字" % (lvl0, lvl1))
    P.cap_start(dma, cap[1])
    to_int16(cap[0], fin[0])
    fir.run(fin[0], fout[0], nw, reset=True, bypass=bypass)
    to_stream(fout[0], ply[0])
    P.play_start(dma, ply[0])             # 交出去，不等
    P.clear_cnt(ctrl, bits)
    st = P.read_stat(stat)
    prev_ovf, prev_und = st["ovf"], st["und"]

    print("  块号  交块(ms)  核(ms)   tx_lvl  cap_lvl  新增ovf 新增und")
    SKIP = 3          # 前两块是垫场，见 dma_audio_probe.stage_d 里那条
    pc = {}           # 分段 CPU 时间（--prof 才填）
    gw = {}           # 分段的**墙钟**时间（每块都记，只给离群块打印用）
    gmax = {}         # 分段墙钟的最大值（整段跑下来）

    def _acc(name, c0):
        if prof:
            pc[name] = pc.get(name, 0.0) + (time.process_time() - c0)

    def _w(name, c0):
        d = time.perf_counter() - c0
        gw[name] = d
        if d > gmax.get(name, 0.0):
            gmax[name] = d
        if d > 0.003 and not live_mode:
            # 打到墙上（perf_counter 是 CLOCK_MONOTONIC，跨进程同一个时基），
            # 好和另一颗核上的抖动探针按时刻对起来
            print("  [慢] %.3f  %-10s %.2f ms" % (time.perf_counter(), name, d * 1e3))

    ORIG = ["cap_wait", "cap_start", "to_int16", "poll", "fir",
            "to_stream", "mm2s_wait", "water", "stat", "pub", "play_start"]

    i = 1
    dbg_left = 8             # 只打前 8 块有丢字的，打完就闭嘴
    while i < nblk and not stop[0]:
        k = i % NBUF
        c = time.process_time()
        w = time.perf_counter()
        sr = P.cap_wait(dma, cap[k], timeout=max(2.0, 3 * frames / FS))
        _acc("cap_wait", c)
        _w("cap_wait", w)
        if not (sr & P.SR_IOC):
            P.check("S2MM 报完成", False, "块 %d DMASR=%s" % (i, P.sr_str(sr)))
            break
        t_b = time.perf_counter()

        if i == SKIP:
            P.clear_cnt(ctrl, bits)
            st = P.read_stat(stat)
            prev_ovf, prev_und = st["ovf"], st["und"]
            ovf_d = und_d = 0
            ovf_idx, ovf_max = [], 0
            proc.clear()
            pend.clear()
            psl.clear()
            fir_ms.clear()
            cap_seen.clear()
            tx_seen.clear()

        # 先把下一块录音挂上再去算/放 —— 录音 FIFO 才排得空
        c = time.process_time()
        w = time.perf_counter()
        P.cap_start(dma, cap[(i + 1) % NBUF])
        _acc("cap_start", c)
        _w("cap_start", w)

        # 顺序很重要（2026-10-25 深夜实测）：**先把这块算完，再等上一块放完**。
        # 反过来（等完放音再算）时，MM2S 早就腾空了，PS 还要花 2.8 ms 才算完、
        # 才交下一块，播放 FIFO 只有 1.6 ms 余量 —— 每块边界断一次流
        # （3000 块里 1727 次 underrun，正好是每块一次）。
        # 正确的做法是让 PS 这点活和 I2S 往外放的那 4.9 ms 重叠。
        c = time.process_time()
        w = time.perf_counter()
        to_int16(cap[k], fin[k])
        _acc("to_int16", c)
        _w("to_int16", w)
        # 只有垫场那一块 reset；这里再 reset 会把延迟线清掉，块边界炸出咔声
        w = time.perf_counter()
        ev = tuner.poll(t_b)
        _w("poll", w)
        if ev:
            print("   [调参] " + "；".join(ev))
        c = time.process_time()
        w = time.perf_counter()
        dt, _ = fir.run(fin[k], fout[k], nw, reset=False, **tuner.params())
        _acc("fir", c)
        _w("fir", w)
        c = time.process_time()
        w = time.perf_counter()
        to_stream(fout[k], ply[k])
        _acc("to_stream", c)
        _w("to_stream", w)

        t_w = time.perf_counter()
        c = time.process_time()
        w = time.perf_counter()
        P.mm2s_wait(dma)                  # 上一块；正常情况下它早就放完了
        _acc("mm2s_wait", c)
        _w("mm2s_wait", w)
        c = time.process_time()
        w = time.perf_counter()
        if tx_low:
            # 水位控制：播放 FIFO 里超过一块就先歇一下再交下一块。
            # 一次算到位 —— 多出来的字数 ÷ I2S 每秒消耗的字数（2·FS）就是要等
            # 的时间，按这个睡一觉就行。每 50 µs 探一次的写法实测每块花
            # 0.56 ms CPU，全花在轮询本身的固定开销上。
            lvl = P.read_stat(stat)["tx_lvl"]
            if lvl > tx_low:
                time.sleep(min((lvl - tx_low) / (2.0 * P.FS_ACT), 0.002))
        _acc("water", c)
        _w("water", w)

        t_p = time.perf_counter()         # 交给 MM2S 的时刻

        c = time.process_time()
        w = time.perf_counter()
        st = P.read_stat(stat)
        d_ovf = (st["ovf"] - prev_ovf) & 0xFFFF
        d_und = (st["und"] - prev_und) & 0xFFFF
        ovf_d += d_ovf
        und_d += d_und
        if d_ovf:
            ovf_idx.append(i)
            ovf_max = max(ovf_max, d_ovf)
        prev_ovf, prev_und = st["ovf"], st["und"]

        proc.append((t_p - t_b) * 1000.0)
        pend.append((t_p - t_w) * 1000.0)
        fir_ms.append(dt * 1000.0)
        cap_seen.append(st["cap_lvl"])
        tx_seen.append(st["tx_lvl"])
        _acc("stat", c)
        _w("stat", w)

        # 给网页一个数字延迟的实时估值（不含声学段）：一块 + 核计算
        # + 核组延迟 + 此刻的播放积压。
        # 电平表 (rms) 每 0.1 s 才算一次 —— 这块板子上一次 480 点的
        # numpy 换算要 0.35 ms，每块算两遍就是 0.7 ms，白扔掉一大块余量。
        c = time.process_time()
        w = time.perf_counter()
        if tuner.status_due(t_p):
            tuner.publish(t_p, i, frames / P.FS_ACT,
                          _rms(fin[k]), _rms(fout[k]),
                          blk_ms + dt * 1000.0 + gd_ms
                          + st["tx_lvl"] / 2.0 / P.FS_ACT * 1000.0,
                          l=frames, nbuf=NBUF,
                          io_ms=float(np.median(proc[-64:])),
                          dsp_ms=float(np.median(fir_ms[-64:])))
        _acc("pub", c)
        _w("pub", w)

        c = time.process_time()
        w = time.perf_counter()
        P.play_start(dma, ply[k])         # 交出去就走
        _acc("play_start", c)
        _w("play_start", w)
        # PS 真正花在这块上的时间（不含等放音、水位控制和核本身）
        psl.append(((time.perf_counter() - t_b) - (t_p - t_w)) * 1000.0)

        # 离群块：把这一块的墙钟分段打出来。只打印不常发生的那些，
        # 平时的开销就是每个分段两次 perf_counter。
        if proc[-1] > 5.0 and not live_mode:
            print("  [离群] 块 %d 交块用了 %.1f ms：" % (i, proc[-1])
                  + "  ".join("%s %.2f" % (n, gw[n] * 1e3) for n in ORIG if n in gw))
        # 一开始丢字的那几块，分段全打出来 —— 300 秒那趟垮的时候只看得到
        # 累计计数，看不出是哪个分段先慢下来的。
        elif (d_ovf or d_und) and dbg_left > 0 and not live_mode:
            dbg_left -= 1
            print("  [首丢] 块 %d 交块 %.2f ms  tx %d cap %d："
                  % (i, proc[-1], st["tx_lvl"], st["cap_lvl"])
                  + "  ".join("%s %.2f" % (n, gw[n] * 1e3) for n in ORIG if n in gw))

        if not live_mode and (i % max(1, nblk // 15) == 0 or d_ovf or d_und):
            print("  %6d  %8.2f  %6.3f  %7d  %7d  %8d %7d"
                  % (i, proc[-1], fir_ms[-1], st["tx_lvl"], st["cap_lvl"],
                     d_ovf, d_und))
        i += 1

    P.mm2s_wait(dma, timeout=2.0)         # 等最后一块真的放完
    total = time.perf_counter() - t0
    P.set_ctrl(ctrl, 0)
    tuner.finish()

    if not proc:
        P.check("跑起来了", False, "一块都没跑完")
        return 1

    f = np.array(fir_ms)
    pr = np.array(proc)
    pw = np.array(pend)
    print("\n  跑了 %.1f s / %d 块（按 48.83 kHz 算理论 %.1f s）"
          % (total, i, i * blk_ms / 1000.0))
    print("  采集完 → 交给 MM2S：mean %.2f ms  max %.2f ms"
          "（其中等上一块放完 mean %.2f ms）"
          % (pr.mean(), pr.max(), pw.mean()))
    print("  其中核自己：mean %.3f ms  max %.3f ms（%d 个字/块）"
          % (f.mean(), f.max(), nw))
    print("  一块 = %.2f ms" % blk_ms)
    import resource
    ru = resource.getrusage(resource.RUSAGE_SELF)
    cpu = ru.ru_utime + ru.ru_stime
    print("  进程 CPU：user %.2f + sys %.2f = %.2f s（跑 %.1f s，"
          "约一个核的 %.0f%%）"
          % (ru.ru_utime, ru.ru_stime, cpu, total,
             cpu / max(total, 1e-9) * 100.0))
    # 上面那个数含了启动（import PyNQ、加载 overlay、分配 buffer，约 6 秒），
    # 看不出循环本身吃多少。这个才是循环内的。
    loop_cpu = cpu - (_ru0.ru_utime + _ru0.ru_stime)
    print("  其中循环内：%.2f s / 跑 %.1f s，约一个核的 %.0f%%"
          % (loop_cpu, total, loop_cpu / max(total, 1e-9) * 100.0))
    if prof and pc:
        print("  ---- 分段 CPU（每块毫秒）----")
        for k2 in sorted(pc, key=lambda x: -pc[x]):
            print("    %-12s %7.3f ms/块" % (k2, pc[k2] * 1000.0 / max(i, 1)))
    if gmax:
        # 每一段在整个跑程里最长的一次。卡顿落在哪一段，一眼就能看出来 ——
        # 这是找"每隔几十秒卡十来毫秒"的唯一线索（均值会被 12000 块稀释掉）。
        print("  ---- 分段墙钟最长一次（ms）----")
        for k2 in sorted(gmax, key=lambda x: -gmax[x]):
            print("    %-12s %7.2f" % (k2, gmax[k2] * 1000.0))
    cap_ms = float(np.mean(cap_seen)) / 2.0 / P.FS_ACT * 1000.0 if cap_seen else 0.0
    tx_ms = float(np.mean(tx_seen)) / 2.0 / P.FS_ACT * 1000.0 if tx_seen else 0.0
    print("  录音 FIFO 积压 %.1f ms ｜ 播放 FIFO 积压 %.1f ms"
          % (cap_ms, tx_ms))
    # 块内位置那两项（等这一块填满、在这一块里排第几）前后抵消，合起来正好
    # 是一个块周期。交块那一项要先减掉等放音的时间，否则和播放积压重复。
    ps_ms = pr.mean() - pw.mean()
    if psl:
        ps_ms = float(np.mean(psl))
    print("  嘴到耳 ≈ 一块 %.1f + 录音 %.1f + PS %.1f + 核计算 %.1f"
          " + 核组延迟 %.1f + 播放 %.1f + 声学 1.23 ≈ %.1f ms"
          % (blk_ms, cap_ms, ps_ms, f.mean(), gd_ms, tx_ms,
             blk_ms + cap_ms + ps_ms + f.mean() + gd_ms + tx_ms + 1.23))
    print("  累计 overrun=%d  underrun=%d" % (ovf_d, und_d))
    if ovf_idx:
        shown = ovf_idx[:20]
        print("  丢块出现在第 %s%s 块（共 %d 块有丢，单块最多丢 %d 个采样）"
              % ("、".join("%d" % v for v in shown),
                 " …" if len(ovf_idx) > len(shown) else "",
                 len(ovf_idx), ovf_max))

    P.check("没有丢块（overrun=0）", ovf_d == 0, "%d" % ovf_d)
    P.check("没有断流（underrun=0）", und_d == 0, "%d" % und_d)
    print("\n%s" % ("全部通过" if P.ok_all else "有失败项，看上面"))
    return 0 if P.ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
