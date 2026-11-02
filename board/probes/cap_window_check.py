#!/usr/bin/env python3
"""抓取窗口（0x78/0x7C）上板自检 —— 2026-10-30 夜。

背景：抓取缓冲原来是 axi_bram_ctrl + 0x43C60000，板上实测**读回来恒为 0x8**
（写进得去：波形确实抓得到；读通路不通；波表 0x43C50000 同样症状）。
现在 BRAM 的 A 口接在 fir_pl_regs 上，用寄存器窗口读：

    写 0x78 = 下标（0..4095）
    读 0x7C = 该字（低 16 位 = 进核 fe_din，高 16 位 = 出核 mpo_l）

本脚本验证四件事：

  [1] 窗口能读：1024 点读一遍，不是全 0、确实有幅度（测试音是方波，只有 ±两个值）
  [2] 快照稳定：同一秒内连读两次，指针不动时数值应当一致
  [3] 位置对应：tone 打开（1500 Hz）时，出核那一路的零穿越频率要对得上
  [4] 不动音频：读窗口期间 STAT0 的 ovf/und 不涨

用法（板上）：

    XILINX_XRT=/usr python3 cap_window_check.py
    XILINX_XRT=/usr python3 cap_window_check.py --hz 750 --amp -20
"""
import argparse
import os
import sys
import time

os.environ.setdefault("XILINX_XRT", "/usr")

from pynq import MMIO  # noqa: E402

REGS_BASE = 0x43C40000
CTRL_BASE = 0x43C00000

OFF_CTRL = 0x00           # 走 gpio_ctrl（0x43C00000）
OFF_STAT0 = 0x08          # ovf[15:0] und[31:16]
OFF_TONE_SEL = 0x58
OFF_TONE_AMP = 0x5C
OFF_SCOPE_CTL = 0x64
OFF_SCOPE_WR = 0x6C
OFF_CAP_IDX = 0x78
OFF_CAP_DATA = 0x7C

CTRL_RUN = 1 << 0
CTRL_DRC = 1 << 1
CTRL_FLUSH = 1 << 2
CTRL_TONE = 1 << 3

FS = 48828.0
N = 1024          # 读口对字节地址解释，下标要 ×4；1024 点 = 21 ms


def read_window(regs):
    """冻 → 按下标步进 4 读 1024 点 → 解冻。返回 (in[], out[], ptr)。"""
    regs.write(OFF_SCOPE_CTL, 1)
    time.sleep(0.002)
    ptr = regs.read(OFF_SCOPE_WR)
    words = []
    for i in range(N):
        regs.write(OFF_CAP_IDX, (i * 4) & 0xFFF)
        words.append(regs.read(OFF_CAP_DATA))
    regs.write(OFF_SCOPE_CTL, 0)
    rot = (((ptr >> 2) & 0x3FF) + 1) & 0x3FF
    words = words[rot:] + words[:rot]
    ins = [((x & 0xFFFF) ^ 0x8000) - 0x8000 for x in words]
    outs = [(((x >> 16) & 0xFFFF) ^ 0x8000) - 0x8000 for x in words]
    return ins, outs, ptr


def zero_cross_hz(x):
    m = sum(x) / float(len(x)) if x else 0.0
    y = [v - m for v in x]
    n = sum(1 for i in range(1, len(y)) if (y[i - 1] < 0) != (y[i] < 0))
    return n / 2.0 / (len(x) / FS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hz", type=float, default=1500.0, help="测试音频率（落到最近的四段）")
    ap.add_argument("--amp", type=float, default=-20.0, help="测试音幅度 dBFS")
    ap.add_argument("--keep", type=int, default=0, help="1 = 跑完不关测试音")
    a = ap.parse_args()

    regs = MMIO(REGS_BASE, 0x1000)
    ctrl = MMIO(CTRL_BASE, 0x1000)

    bad = 0
    say = print

    say("== 0. 前置：通路开着、抓取没冻、清一次计数器")
    ctrl.write(OFF_CTRL, CTRL_RUN | CTRL_DRC | CTRL_FLUSH)
    time.sleep(0.05)
    ctrl.write(OFF_CTRL, CTRL_RUN | CTRL_DRC)
    time.sleep(0.3)
    st0 = regs.read(OFF_STAT0)
    ovf0, und0 = st0 & 0xFFFF, st0 >> 16
    say("   ctrl=0x%02X  scope_ctl=%d  ovf=%d und=%d"
        % (ctrl.read(OFF_CTRL), regs.read(OFF_SCOPE_CTL), ovf0, und0))

    say("== 1. 打开测试音 %g Hz / %g dBFS" % (a.hz, a.amp))
    band = min(range(4), key=lambda i: abs([250, 750, 1500, 3000][i] - a.hz))
    regs.write(OFF_TONE_SEL, band)
    amp = int(round(32768.0 * (10.0 ** (a.amp / 20.0))))
    regs.write(OFF_TONE_AMP, max(0, min(32767, amp)))
    ctrl.write(OFF_CTRL, CTRL_RUN | CTRL_DRC | CTRL_TONE)
    time.sleep(0.6)

    say("== 2. 读一遍窗口（1024 点 = 21 ms）")
    t0 = time.time()
    ins, outs, ptr = read_window(regs)
    dt = (time.time() - t0) * 1000.0
    uniq = len(set(outs))
    say("   耗时 %.1f ms  ptr=%d  out 不同取值 %d 个  out 峰值 %d  in 峰值 %d"
        % (dt, ptr, uniq, max(abs(v) for v in outs), max(abs(v) for v in ins)))
    # 测试音是方波，只有 ±两个取值 —— 不能用"不同取值个数"当判据
    ok = uniq >= 2 and max(abs(v) for v in outs) > 300
    bad += 0 if ok else 1
    say("   [1] 窗口有数据（不是全 0 / 不是定值）  %s" % ("OK" if ok else "FAIL"))

    say("== 3. 频率对得上")
    f = zero_cross_hz(outs)
    want = [250, 750, 1500, 3000][band]
    ok = abs(f - want) <= max(15.0, want * 0.05)
    bad += 0 if ok else 1
    say("   [2] 零穿越法测得 %.1f Hz（写入 %d Hz）  %s"
        % (f, want, "OK" if ok else "FAIL"))

    say("== 4. 快照稳定（冻结期间连读两次）")
    regs.write(OFF_SCOPE_CTL, 1)
    time.sleep(0.002)
    ptr1 = regs.read(OFF_SCOPE_WR) & 0xFFF
    a1 = []
    for i in range(256):
        regs.write(OFF_CAP_IDX, i)
        a1.append(regs.read(OFF_CAP_DATA))
    time.sleep(0.02)
    ptr2 = regs.read(OFF_SCOPE_WR) & 0xFFF
    a2 = []
    for i in range(256):
        regs.write(OFF_CAP_IDX, i)
        a2.append(regs.read(OFF_CAP_DATA))
    regs.write(OFF_SCOPE_CTL, 0)
    ok = (ptr1 == ptr2) and (a1 == a2)
    bad += 0 if ok else 1
    say("   [3] 冻结期间 ptr 不动(%d/%d) 数据一致  %s"
        % (ptr1, ptr2, "OK" if ok else "FAIL"))

    say("== 5. 读窗口不打扰音频（ovf/und 不涨）")
    time.sleep(0.5)
    st1 = regs.read(OFF_STAT0)
    ovf1, und1 = st1 & 0xFFFF, st1 >> 16
    ok = (ovf1 == ovf0) and (und1 == und0)
    # 判据是「读窗口期间不增长」，不是累计值为 0：前面的探针停过播放，
    # 本探针 flush 后重开输出时会有一次启动欠载（2026-11-02 实测 und0=1），
    # 拿绝对值 0 当门槛会把这个瞬态误判成读窗口的错。
    bad += 0 if ok else 1
    say("   [4] ovf %d→%d  und %d→%d  %s"
        % (ovf0, ovf1, und0, und1, "OK" if ok else "FAIL"))

    if not a.keep:
        ctrl.write(OFF_CTRL, CTRL_RUN | CTRL_DRC)
        regs.write(OFF_TONE_AMP, 0)
        say("   测试音已关")

    say("")
    say("CAP WINDOW %s" % ("PASS" if bad == 0 else "FAIL (%d)" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
