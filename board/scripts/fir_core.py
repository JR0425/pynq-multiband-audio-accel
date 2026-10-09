# FIR 多频段压缩核的 Python 驱动（板载运行）
#
# 核是「块进块出、自己去内存搬」的形式：给一块输入、一块输出和采样数，
# 它自己经 AXI4-Master 去 DDR 读、算完写回，完成时把状态位置起来。
# 这个类就是那套寄存器操作的一层薄封装。
#
# 寄存器偏移来自 HLS 生成的 ap_ctrl_hs 映射；生成头文件位于 HLS build 输出目录，
# 但不随本仓库快照提交。当前驱动常量和 report/hardware_interface_spec.md §二
# 使用同一组偏移。早期手排的 CTRL/STATUS/ID 表已经作废。
#
# 用法（板子上）：
#   from pynq import Overlay
#   from fir_core import FirMultiband
#   ol = Overlay("fir.bit")
#   k  = FirMultiband(ol.fir_multiband_0.mmio)
#   k.run(in_buf, out_buf, n)

import time

import numpy as np
from pynq import allocate


# Q7.8：1 个符号位 + 7 个整数位 + 8 个小数位。
#     1.0    = 256（不是 1，不是 32768 —— 写错这一个数，声音差 48 dB）
#     范围   = ±128  ->  ±42 dB
#     步进   = 1/256 ->  0.034 dB
GAIN_UNITY = 256
GAIN_MIN = -32768
GAIN_MAX = 32767


def db_to_gain(db):
    """分贝 -> Q7.8 原始整数。0 dB = 256。超出 ±42 dB 就削到量程边上。

    助听器的处方增益按听力损失给（轻度 20~30 / 中度 30~50 / 重度 50~60 dB），
    所以量程做到 +42 dB 是有意义的；再往上要靠总增益级，不在这一级。
    """
    v = int(round(GAIN_UNITY * (10.0 ** (db / 20.0))))
    return max(GAIN_MIN, min(GAIN_MAX, v))


def gain_to_db(raw):
    """Q7.8 原始整数 -> 分贝。0 或负数没法取对数，返回 None。"""
    if raw <= 0:
        return None
    return 20.0 * float(np.log10(raw / float(GAIN_UNITY)))


class FirMultiband:
    # ---- 寄存器偏移 ----
    AP_CTRL   = 0x00          # bit0 ap_start(读写) / bit1 ap_done(读后清) /
                              # bit2 ap_idle / bit3 ap_ready
    IN_ADDR   = (0x10, 0x14)  # 64 位：低 32 位、高 32 位
    OUT_ADDR  = (0x1c, 0x20)  # 同上
    LENGTH    = 0x28          # 本块采样数（32 位）
    DRC_THR   = 0x30          # 4 × int16，两个挤一个字：0x30=(0,1) 0x34=(2,3)
    DRC_RATIO = 0x38          # 同上
    RESET     = 0x40          # 非 0 = 清延迟线
    BYPASS    = 0x48          # 非 0 = 跳过压缩（直通检验用）
    BAND_GAIN = 0x50          # 4 × int16，每段独立增益，Q7.8（1.0 = 256）
                              # 2026-10-09 加。它加在签名**最后**，所以
                              # 0x30~0x48 一个都没挪位（已核对生成的
                              # xfir_multiband_hw.h、以及 overlay 的 fir.hwh：
                              # 只多了一条 Memory_band_gain / offset 80 / size 8）。
                              #
                              # ⚠️ 这个寄存器的**复位值是 0，不是单位增益**（.hwh 里
                              #    RESET_VALUE=0）。也就是说：**不写它就等于增益全 0，
                              #    输出整块静音**，不是"不变声"。所以任何绕过 run() 直接
                              #    摸寄存器的代码，必须自己在 ap_start 之前把它写成
                              #    256。核里不做这个兜底（寄存器默认值改不了）。
                              #    现在全仓库只有本文件直接写核的寄存器，这条不会
                              #    踩到别人 —— 上板自检里有一条就是"增益全 0 → 输出全 0"。

    AP_START = 1 << 0
    AP_DONE  = 1 << 1
    AP_IDLE  = 1 << 2
    AP_READY = 1 << 3

    N_TAPS      = 193
    N_BANDS     = 4
    GROUP_DELAY = (N_TAPS - 1) // 2          # 96 个采样
    # m_axi 的 -depth 是 8192，块别超过它
    MAX_LEN     = 8192

    # 出厂默认：阈值 0.1（Q1.15 → 3277），压缩比 0.7（Q1.15 → 22938）
    DEF_THR   = (3277,) * N_BANDS
    DEF_RATIO = (22938,) * N_BANDS
    # 增益默认**单位增益**（Q7.8 的 1.0 = 256，不是 1 也不是 32768）。
    # 给 256 时核的行为和加这一级之前逐位相同 —— 所以老脚本不传 gain
    # 也不会变声，csim 已经逐字节验过。
    DEF_GAIN  = (GAIN_UNITY,) * N_BANDS

    def __init__(self, mmio):
        self.mmio = mmio

    # ---------------- 小工具 ----------------

    def _write_addr(self, pair, addr):
        addr = int(addr)
        self.mmio.write(pair[0], addr & 0xFFFFFFFF)
        self.mmio.write(pair[1], (addr >> 32) & 0xFFFFFFFF)

    @staticmethod
    def _pack_pair(a, b):
        return (int(a) & 0xFFFF) | ((int(b) & 0xFFFF) << 16)

    @classmethod
    def _pack4(cls, vals):
        v = [int(x) & 0xFFFF for x in vals]
        return cls._pack_pair(v[0], v[1]), cls._pack_pair(v[2], v[3])

    # ---------------- 主流程 ----------------

    def run(self, in_buf, out_buf, length,
            thr=None, ratio=None, gain=None,
            reset=False, bypass=False, timeout=5.0):
        """处理一块。返回 (耗时秒数, 忙等轮数)。

        in_buf / out_buf 必须是 pynq.allocate 出来的 buffer（要有 physical_address）。
        length 是本块采样数，不要超过 MAX_LEN。
        reset=True 清延迟线（整段处理的第一块、或想重新开始时用）；
        分块连续处理时只在第一块给一次 reset。

        gain 是 4 段各自的**原始 Q7.8 整数**（1.0 = 256），None 就是单位增益。
        想按分贝给就先过 db_to_gain()：
            gain=[db_to_gain(12)] * 4        # 每段 +12 dB
            gain=[db_to_gain(-6), 0, 6, 12]  # 逐段不同
        """
        length = int(length)
        if length <= 0 or length > self.MAX_LEN:
            raise ValueError("length 得在 1..%d 之间，给的是 %d" % (self.MAX_LEN, length))
        thr = self.DEF_THR if thr is None else tuple(thr)
        ratio = self.DEF_RATIO if ratio is None else tuple(ratio)
        if gain is None:
            gain = self.DEF_GAIN
        else:
            gain = tuple(int(g) for g in gain)

        # 所有参数必须在写 ap_start 之前写完 —— HLS 是在 ap_start 那一刻锁存的
        self._write_addr(self.IN_ADDR, in_buf.physical_address + in_buf.offset)
        self._write_addr(self.OUT_ADDR, out_buf.physical_address + out_buf.offset)
        self.mmio.write(self.LENGTH, length)

        w0, w1 = self._pack4(thr)
        self.mmio.write(self.DRC_THR, w0)
        self.mmio.write(self.DRC_THR + 4, w1)
        w0, w1 = self._pack4(ratio)
        self.mmio.write(self.DRC_RATIO, w0)
        self.mmio.write(self.DRC_RATIO + 4, w1)
        w0, w1 = self._pack4(gain)
        self.mmio.write(self.BAND_GAIN, w0)
        self.mmio.write(self.BAND_GAIN + 4, w1)

        self.mmio.write(self.RESET, 1 if reset else 0)
        self.mmio.write(self.BYPASS, 1 if bypass else 0)

        # CPU 写的数要先刷出 cache，PL 才读得到
        _flush(in_buf)
        _flush(out_buf)

        t0 = time.time()
        self.mmio.write(self.AP_CTRL, self.AP_START)

        spins = 0
        while True:
            if self.mmio.read(self.AP_CTRL) & self.AP_DONE:
                break
            spins += 1
            if time.time() - t0 > timeout:
                raise TimeoutError("核 %g 秒没给 DONE，状态字 0x%08x"
                                   % (timeout, self.mmio.read(self.AP_CTRL)))
        dt = time.time() - t0

        # PL 写的数要丢掉 CPU 这边过期的 cache，才读得到
        _invalidate(out_buf)
        return dt, spins

    # ---------------- 方便调用的组合 ----------------

    def process(self, x, reset=False, bypass=False, thr=None, ratio=None,
                out=None, chunk=None, gain=None):
        """整段处理一个 numpy int16 数组，返回 (输出数组, 总耗时, 分块信息)。

        比 MAX_LEN 长就自动切块。**切块时只在第一块 reset**，
        否则块边界会炸出咔声（延迟线要跨块保持）。
        """
        x = np.asarray(x, dtype=np.int16).ravel()
        n = x.size
        out = np.empty(n, dtype=np.int16) if out is None else out
        chunk = self.MAX_LEN if chunk is None else int(chunk)

        in_buf = allocate(shape=(chunk,), dtype=np.int16)
        out_buf = allocate(shape=(chunk,), dtype=np.int16)
        try:
            total = 0.0
            blocks = []
            pos = 0
            first = True
            while pos < n:
                m = min(chunk, n - pos)
                # numpy -> buffer：用 [:m] 视图赋值，别直接 copy 整个 buffer
                np.copyto(in_buf[:m], x[pos:pos + m])
                dt, spins = self.run(in_buf, out_buf, m,
                                     thr=thr, ratio=ratio, gain=gain,
                                     reset=(reset and first), bypass=bypass)
                np.copyto(out[pos:pos + m], out_buf[:m])
                total += dt
                blocks.append((m, dt, spins))
                first = False
                pos += m
        finally:
            del in_buf, out_buf
        return out, total, blocks


# ---------------- cache 维护 ----------------
# PYNQ 2.6+ 的 PynqBuffer 自带 flush/invalidate；老版本没有就跳过（那说明
# 那套 buffer 本来就是不 cache 的）。

def _flush(buf):
    f = getattr(buf, "flush", None)
    if f is not None:
        f()


def _invalidate(buf):
    f = getattr(buf, "invalidate", None)
    if f is not None:
        f()
