#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pynq_overlay_skeleton —— 自研 AXI-Lite IP 接 PYNQ 时的最小可用骨架。

适用场景
--------
手上有三样东西：一颗自己综合出来的位流 ``xxx.bit``、同一次构建导出的 ``xxx.hwh``、
以及一份"寄存器表"（哪个偏移是什么、读写权限）。缺的是把它们接到一起的那层胶水：
加载 overlay、确认 bit/hwh 是配套的、按名字读写寄存器、从抓取缓冲里取一段波形。
这三件事每个自研 IP 的题目都要重做一遍，且每次都踩同样的坑。

怎么用
------
1. 改 ``REGISTERS``：把表换成你的（名字 → 组内偏移, 权限）。组内偏移是相对 bank 起点的，
   **不要填绝对地址** —— 见下面「踩过的坑」第 1 条。
2. 改 ``BANK0_BASE`` / ``IP_NAME`` / ``CAPTURE_IDX`` / ``CAPTURE_DATA``。
3. 板上跑：
       python pynq_overlay_skeleton.py --bit /home/xilinx/x/fir_pl.bit \
                                        --hwh /home/xilinx/x/fir_pl.hwh --probe
   ``--probe`` 做四件事：校验 bit/hwh 配套 → 加载 → 逐个可写寄存器写读回 → 抓一段窗口验数据在动。
4. 不接板子也能先验逻辑：
       python pynq_overlay_skeleton.py --selftest      # 纯标准库，不需要 pynq / numpy

已验证的效果
------------
- ``--selftest`` 在本仓库（Windows + Python 3.9/3.13，无 pynq）上通过：覆盖 md5 配套校验、
  写读回、bank 相对偏移换算（``0x70`` → ``0x170``）、抓取窗口自动递增与"取两次读相同的最长连续段"。
- 寄存器访问器与抓取窗口的写法直接来自本仓库上板脚本，``--probe`` 的四步对应
  ``board/probes/board_accept.sh`` 里的"寄存器窗口 + 抓取窗口"两项判据。

什么时候别用
------------
- 走的是 PYNQ 的 ``DefaultIP`` 且只有 AXI4-Stream、没有 AXI-Lite 寄存器（那用 DMA 模板）。
- 需要位域语义（读清零 / 写一触发 / 自增地址）：本骨架只分 ``rw`` / ``ro`` / ``wo`` / ``rr``，
  复杂语义会静默读错，得自己写。``register_map_stub.py`` 生成的东西也是这个局限。
- 要的是**性能**而不是**通路**：逐字 MMIO 读 1024 点会比 DMA 慢两个量级，抓长窗口请换 DMA。

踩过的坑（都写进了下面的代码）
------------------------------
1. **组内偏移与绝对地址混用。** 双耳两套 bank 时，``+0x70`` 是 bank 内相对偏移，
   bank0 的绝对地址是 ``0x170`` 不是 ``0x070``。写 ``0x070`` 会读到恒 0，
   很容易误判成"寄存器没实现"（本仓库真踩过）。
2. **``axi_bram_ctrl`` 读回恒 ``0x8``。** 根因是 ``SINGLE_PORT_BRAM`` 填 0 时读口走了
   没人接的 B 口（生成的 wrapper 里能读到 ``bram_rddata_b(...1'b1,1'b0,1'b0,1'b0)``）。
   所以抓取缓冲走"写下标 / 读数据"的逐字窗口，不直接读 BRAM 控制器。
3. **冻结-读取-解冻会自己造台阶。** ``scope_snap=1`` 只停写、音频照跑，恢复后中间那段
   样点根本没进缓冲，每个周期都在缓冲里留一道相位跳变 —— 看起来像硬件丢样，其实是读法。
   所以 ``read_stable()`` 不冻结，改成连读几次取"相同的最长连续段"。
4. **bit 与 hwh 必须同一次构建。** 配错的一对通常不报错，只是寄存器全读到 0 或读到上一版
   布局的地址上。``verify_pairing()`` 用 md5 清单卡这一条。

License: MIT
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

# ---------------------------------------------------------------- 改这里
IP_NAME = "fir_pl_0"          # overlay 里 AXI-Lite IP 的实例名（hwh 里能查到）
BANK0_BASE = 0x100            # bank0 在 IP 地址空间里的起点
BANK1_BASE = 0x200            # 双耳两套 bank；只有一套就填 None
CAPTURE_IDX = 0x78            # 抓取窗口：写下标
CAPTURE_DATA = 0x7C           # 抓取窗口：读数据
CAPTURE_DEPTH = 1024          # 缓冲深度（超过就会回绕）

# 名字 -> (组内偏移, 权限)；权限 rw / ro / wo / rr（rr = 写进去读回来应相同，用于自检）
REGISTERS = {
    "GAIN0": (0x00, "rw"), "GAIN1": (0x04, "rw"),
    "GAIN2": (0x08, "rw"), "GAIN3": (0x0C, "rw"),
    "THR0": (0x10, "rw"), "THR1": (0x14, "rw"),
    "THR2": (0x18, "rw"), "THR3": (0x1C, "rw"),
    "ENV0": (0x50, "ro"), "ENV1": (0x54, "ro"),
    "GR0": (0x60, "ro"), "GR1": (0x64, "ro"),
    "RAMP_DIV": (0x70, "rw"), "RAMP_BUSY": (0x74, "ro"),
}
ID_REG = None                 # 有识别码寄存器就填名字，例如 "ID"；--probe 会先读它


# ---------------------------------------------------------------- 小工具
def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_pairing(bit_path, hwh_path, manifest=None, max_gap_s=600):
    """确认 .bit 与 .hwh 是同一次构建的产物。返回 (ok, 说明列表)。

    三级判据，从强到弱：
      ① manifest 里记了 md5（``{"bit_md5": ..., "hwh_md5": ...}``）→ 逐项比；
      ② 没有 manifest → 只比文件是否都在、非空、修改时间是否落在 max_gap_s 内；
      ③ 时间差超阈值 → 报 warn（不拦，因为 copy/解压都会改 mtime）。
    """
    msgs = []
    for p in (bit_path, hwh_path):
        if not os.path.isfile(p):
            return False, ["缺文件：%s" % p]
        if os.path.getsize(p) == 0:
            return False, ["空文件：%s" % p]
    b_md5, h_md5 = md5_of(bit_path), md5_of(hwh_path)
    msgs.append("bit md5 = %s" % b_md5)
    msgs.append("hwh md5 = %s" % h_md5)

    if manifest:
        try:
            with open(manifest, "r") as f:
                m = json.load(f)
        except Exception as e:
            return False, msgs + ["manifest 读不了：%s" % e]
        want_b, want_h = m.get("bit_md5"), m.get("hwh_md5")
        if want_b and want_b != b_md5:
            return False, msgs + ["bit 与 manifest 不符（期望 %s）" % want_b]
        if want_h and want_h != h_md5:
            return False, msgs + ["hwh 与 manifest 不符（期望 %s）" % want_h]
        msgs.append("manifest 比对通过")
        return True, msgs

    gap = abs(os.path.getmtime(bit_path) - os.path.getmtime(hwh_path))
    msgs.append("无 manifest，按 mtime 差 %.0f s 粗判" % gap)
    if gap > max_gap_s:
        msgs.append("WARN 两者 mtime 差超过 %d s，很可能不是同一次构建" % max_gap_s)
    return True, msgs


def bank_addr(base, rel):
    """组内偏移 → 绝对地址。这是第 1 条坑的唯一正确算法。"""
    return base + rel


# ---------------------------------------------------------------- 寄存器访问器
class RegMap(object):
    """按名字读写 AXI-Lite 寄存器。mmio 只要求有 read(off) / write(off, val)。"""

    def __init__(self, mmio, base=BANK0_BASE, regs=None):
        self.mmio = mmio
        self.base = base
        self.regs = dict(regs or REGISTERS)

    def addr(self, name):
        if name not in self.regs:
            raise KeyError("没有这个寄存器：%s" % name)
        return bank_addr(self.base, self.regs[name][0])

    def read(self, name):
        return self.mmio.read(self.addr(name))

    def write(self, name, val):
        if self.regs[name][1] in ("ro", "rr_ro"):
            raise ValueError("%s 是只读寄存器" % name)
        self.mmio.write(self.addr(name), int(val))

    def roundtrip(self, names=None, probe_val=0x5A5A):
        """对每个可写寄存器写一个探针值再读回。返回 [(名字, 期望, 实际, 是否一致)]。"""
        out = []
        for name, (_, perm) in sorted(self.regs.items()):
            if names is not None and name not in names:
                continue
            if perm not in ("rw", "rr"):
                continue
            self.write(name, probe_val)
            got = self.read(name)
            out.append((name, probe_val, got, got == probe_val))
        return out


# ---------------------------------------------------------------- 抓取窗口
class CaptureWindow(object):
    """逐字窗口读法：写下标 → 读数据。不要直接读 axi_bram_ctrl（坑 2）。"""

    def __init__(self, mmio, idx_off=CAPTURE_IDX, data_off=CAPTURE_DATA,
                 depth=CAPTURE_DEPTH):
        self.mmio = mmio
        self.idx, self.data, self.depth = idx_off, data_off, depth

    def read(self, n, start=0):
        n = max(0, min(n, self.depth))
        out = []
        for i in range(n):
            self.mmio.write(self.idx, (start + i) % self.depth)
            out.append(self.mmio.read(self.data))
        return out

    def read_stable(self, n, tries=4):
        """连读 tries 次，返回所有结果里"最长相同连续段"。不冻结（坑 3）。

        冻结-读取-解冻会在缓冲里留下相位跳变，看起来像丢样；不冻结则读到的是
        滚动缓冲区，两次读之间会漂移 —— 所以取公共段，而不是直接信任某一次。
        """
        reads = [self.read(n) for _ in range(max(1, tries))]
        ref = reads[0]
        best_lo, best_hi = 0, len(ref) - 1
        for other in reads[1:]:
            lo, hi = _longest_common_run(ref, other)
            # 取所有两两公共段里最保守的那个窗口
            best_lo = max(best_lo, lo)
            best_hi = min(best_hi, hi)
            if best_hi < best_lo:
                return []
        return ref[best_lo:best_hi + 1]


def _longest_common_run(a, b):
    """两段等长序列里最长的逐位相同连续段，返回 (lo, hi)（闭区间，空则 (0, -1)）。"""
    n = min(len(a), len(b))
    best = (0, -1)
    lo = None
    for i in range(n):
        if a[i] == b[i]:
            if lo is None:
                lo = i
            if i - lo > best[1] - best[0]:
                best = (lo, i)
        else:
            lo = None
    return best


def looks_alive(samples, min_unique=4):
    """判"数据在动"：不同值个数够多，且不是常数/恒零。"""
    return len(samples) > 8 and len(set(samples)) >= min_unique


# ---------------------------------------------------------------- 板上通路
def probe(bit_path, hwh_path, manifest=None, ip_name=IP_NAME):
    """四步上板自检。返回进程退出码：0 全过，非 0 有挂。"""
    fails = 0

    def chk(desc, cond, detail=""):
        nonlocal fails
        print("  %-42s %s %s" % (desc, "OK" if cond else "FAIL", detail))
        if not cond:
            fails += 1

    print("== 1. bit / hwh 配套 ==")
    ok, msgs = verify_pairing(bit_path, hwh_path, manifest)
    for m in msgs:
        print("   " + m)
    chk("配套校验", ok)
    if not ok:
        return 1

    print("== 2. 加载 overlay ==")
    from pynq import Overlay                     # 只在板上需要
    ol = Overlay(bit_path)
    chk("overlay 加载完成", True)
    ip = getattr(ol, ip_name)
    regs = RegMap(ip.mmio)

    if ID_REG:
        print("== 3. 识别码 ==")
        ident = regs.read(ID_REG)
        chk("识别码非 0/非全 1", ident not in (0, 0xFFFFFFFF), hex(ident))

    print("== 4. 寄存器写读回 ==")
    for name, want, got, same in regs.roundtrip():
        chk(name, same, "写 %#x 读 %#x" % (want, got))

    print("== 5. 抓取窗口 ==")
    win = CaptureWindow(ip.mmio)
    seg = win.read_stable(256, tries=3)
    chk("窗口能读到数据", len(seg) >= 64, "公共段 %d 点" % len(seg))
    chk("数据在动（不是常数）", looks_alive(seg), "不同值 %d 个" % len(set(seg)))
    return 0 if fails == 0 else 2


# ---------------------------------------------------------------- 离线自检
class FakeMmio(object):
    """替身：模拟一块带抓取缓冲的 AXI-Lite 地址空间，供 --selftest 用。"""

    def __init__(self, depth=64):
        self.mem = {}
        self.depth = depth
        self.buf = [(i * 37 + 11) & 0xFFFF for i in range(depth)]
        self.writes = 0

    def read(self, off):
        if off == CAPTURE_DATA:
            return self.buf[self.mem.get(CAPTURE_IDX, 0) % self.depth]
        return self.mem.get(off, 0)

    def write(self, off, val):
        self.writes += 1
        self.mem[off] = int(val) & 0xFFFFFFFF


def selftest():
    fails = 0

    def chk(desc, cond, detail=""):
        nonlocal fails
        print("  %-44s %s %s" % (desc, "OK" if cond else "FAIL", detail))
        if not cond:
            fails += 1

    print("== 1. bank 相对偏移（坑 1）==")
    chk("bank(0x100, 0x70) == 0x170", bank_addr(BANK0_BASE, 0x70) == 0x170,
        hex(bank_addr(BANK0_BASE, 0x70)))
    chk("bank(0x100, 0x70) != 0x070", bank_addr(BANK0_BASE, 0x70) != 0x070)

    print("== 2. 寄存器访问器 ==")
    m = FakeMmio()
    r = RegMap(m)
    r.write("GAIN0", 0x1234)
    chk("写后读回一致", r.read("GAIN0") == 0x1234, hex(r.read("GAIN0")))
    chk("写到了 0x100 不是 0x000", m.mem.get(0x100) == 0x1234, hex(m.mem.get(0x100, 0)))
    try:
        r.write("ENV0", 1)
        chk("只读寄存器拒绝写", False)
    except ValueError:
        chk("只读寄存器拒绝写", True)
    try:
        r.read("NOPE")
        chk("未知寄存器抛 KeyError", False)
    except KeyError:
        chk("未知寄存器抛 KeyError", True)

    print("== 3. roundtrip ==")
    res = r.roundtrip(names=["GAIN1", "THR2"], probe_val=0x3C3C)
    chk("两个可写寄存器都一致", len(res) == 2 and all(x[3] for x in res), res)

    print("== 4. 抓取窗口（坑 2 / 坑 3）==")
    win = CaptureWindow(m, depth=64)
    seg = win.read(32)
    chk("按写到缓冲里的顺序读出", seg == m.buf[:32], seg[:4])
    chk("回绕：从 depth-2 读 4 点", win.read(4, start=62) == [m.buf[62], m.buf[63], m.buf[0], m.buf[1]])
    chk("不冻结也能取公共段", len(win.read_stable(16, tries=3)) == 16)

    print("== 5. 公共段算法 ==")
    chk("全同 -> 全长", _longest_common_run([1, 2, 3], [1, 2, 3]) == (0, 2))
    chk("中间一致 -> 中间那段", _longest_common_run([9, 2, 3, 9], [8, 2, 3, 7]) == (1, 2))
    chk("全不同 -> 空", _longest_common_run([1, 1], [2, 2]) == (0, -1))

    print("== 6. 活数据判据 ==")
    chk("常数序列判死", not looks_alive([5] * 32))
    chk("恒零判死", not looks_alive([0] * 32))
    chk("真实序列判活", looks_alive(m.buf[:32]))

    print("== 7. bit/hwh 配套校验（坑 4）==")
    import tempfile
    d = tempfile.mkdtemp(prefix="pynq_skel_")
    b = os.path.join(d, "a.bit"); h = os.path.join(d, "a.hwh"); mf = os.path.join(d, "m.json")
    with open(b, "wb") as f:
        f.write(b"\x00bit")
    with open(h, "w") as f:
        f.write("<hwh/>")
    ok, _ = verify_pairing(b, h)
    chk("无 manifest 时靠 mtime 放行", ok)
    with open(mf, "w") as f:
        json.dump({"bit_md5": md5_of(b), "hwh_md5": md5_of(h)}, f)
    ok, _ = verify_pairing(b, h, mf)
    chk("manifest 一致时通过", ok)
    with open(mf, "w") as f:
        json.dump({"bit_md5": "0" * 32, "hwh_md5": md5_of(h)}, f)
    ok, _ = verify_pairing(b, h, mf)
    chk("manifest 不符时拦下", not ok)
    ok, _ = verify_pairing(b, os.path.join(d, "nope.hwh"))
    chk("缺 hwh 时拦下", not ok)

    print("\n" + ("SELFTEST PASS" if fails == 0 else "SELFTEST FAIL (%d)" % fails))
    return 0 if fails == 0 else 1


# ---------------------------------------------------------------- 入口
def main(argv=None):
    ap = argparse.ArgumentParser(description="PYNQ 自研 AXI-Lite IP 的最小可用骨架")
    ap.add_argument("--selftest", action="store_true", help="纯标准库离线自检，不接板子")
    ap.add_argument("--bit", help="位流路径")
    ap.add_argument("--hwh", help="同一次构建导出的 hwh 路径")
    ap.add_argument("--manifest", help='带 bit_md5 / hwh_md5 的 json')
    ap.add_argument("--ip", default=IP_NAME, help="overlay 里的 IP 实例名")
    ap.add_argument("--probe", action="store_true", help="上板四步自检")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if a.probe:
        if not (a.bit and a.hwh):
            ap.error("--probe 需要 --bit 与 --hwh")
        return probe(a.bit, a.hwh, a.manifest, a.ip)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
