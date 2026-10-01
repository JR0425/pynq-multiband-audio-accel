# FIR 多频段压缩核的 Python 驱动（板载运行）
#
# 核是「块进块出、自己去内存搬」的形式：给一块输入、一块输出和采样数，
# 它自己经 AXI4-Master 去 DDR 读、算完写回，完成时把状态位置起来。
# 这个类就是那套寄存器操作的一层薄封装。
#
# 寄存器偏移的**唯一来源**是 HLS 生成的头文件
#   build/hls/synth_proj/solution1/impl/misc/drivers/fir_multiband_v1_0/src/xfir_multiband_hw.h
# 它和 report/hardware_interface_spec.md §二 那张「设计意图」表**不一样** ——
# HLS 自动生成的控制口是 ap_ctrl 风格（AP_CTRL/AP_DONE 那套），
# 不是当初设计的 CTRL/STATUS/ID 那套。以这里为准，spec 那份要改。
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
            thr=None, ratio=None, reset=False, bypass=False, timeout=5.0):
        """处理一块。返回 (耗时秒数, 忙等轮数)。

        in_buf / out_buf 必须是 pynq.allocate 出来的 buffer（要有 physical_address）。
        length 是本块采样数，不要超过 MAX_LEN。
        reset=True 清延迟线（整段处理的第一块、或想重新开始时用）；
        分块连续处理时只在第一块给一次 reset。
        """
        length = int(length)
        if length <= 0 or length > self.MAX_LEN:
            raise ValueError("length 得在 1..%d 之间，给的是 %d" % (self.MAX_LEN, length))
        thr = self.DEF_THR if thr is None else tuple(thr)
        ratio = self.DEF_RATIO if ratio is None else tuple(ratio)

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
                out=None, chunk=None):
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
                                     thr=thr, ratio=ratio,
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
