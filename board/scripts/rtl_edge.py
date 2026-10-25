"""fir_rtl 外壳的越界/非法块长守卫（接在 rtl_probe.py 之后跑的补充项）

外壳在 S_IDLE 认到 start 时会先检查块长：
    n == 0 或 n > MAXN(8192)  -> 置 overflow、直接 done，不碰 BRAM
这块逻辑在正常跑块时走不到，单独验一次，免得哪天真传错 n 却静默出垃圾。

用法（板子上，要 root，和 fir_rtl.bit / fir_rtl.hwh 放同一目录）：

    echo xilinx | sudo -S env XILINX_XRT=/usr \
      /usr/local/share/pynq-venv/bin/python3 rtl_edge.py
"""

import sys
import time

import numpy as np
from pynq import MMIO, Overlay

BRAM_IN_BASE = 0x43C00000
GPIO_CTRL = 0x43C10000
GPIO_STAT = 0x43C20000
REG_CTRL = 0x00
REG_STAT_LO = 0x00
REG_STAT_HI = 0x08

ok_all = True


def check(name, passed, detail=""):
    global ok_all
    ok_all = ok_all and passed
    print("  [%s] %s%s" % ("OK  " if passed else "FAIL", name,
                           ("  " + detail) if detail else ""))


def main():
    ol = Overlay("fir_rtl.bit")
    mm_in = MMIO(BRAM_IN_BASE, 0x8000)
    mm_ctrl = MMIO(GPIO_CTRL, 0x1000)
    mm_stat = MMIO(GPIO_STAT, 0x1000)

    # 先往缓冲里塞一个可识别的图样，越界那两轮不该动它
    mm_in.array[:64] = np.arange(64, dtype=np.uint32) | np.uint32(0x5A5A0000)

    def run(n, label):
        mm_ctrl.write(REG_CTRL, 0)
        time.sleep(0.001)
        mm_ctrl.write(REG_CTRL, (n & 0xFFFF) | (1 << 16))
        mm_ctrl.write(REG_CTRL, n & 0xFFFF)
        deadline = time.perf_counter() + 2.0
        hi = 0
        while time.perf_counter() < deadline:
            hi = mm_stat.read(REG_STAT_HI)
            if hi & (1 << 16):
                break
            time.sleep(0.0002)
        done = bool(hi & (1 << 16))
        busy = bool(hi & (1 << 17))
        ovf = bool(hi & (1 << 18))
        n_out = hi & 0xFFFF
        cycles = mm_stat.read(REG_STAT_LO)
        print("  [info] %s n=%d  done=%d busy=%d overflow=%d n_out=%d cycles=%d"
              % (label, n, done, busy, ovf, n_out, cycles))
        check("%s 立刻 done（不挂死）" % label, done)
        check("%s overflow 置起" % label, ovf)
        check("%s n_out = 0（没写回）" % label, n_out == 0)
        return done, ovf, n_out

    print("=== 1. 块长 0 ===")
    run(0, "n=0")

    print("\n=== 2. 块长 8193（超过 MAXN=8192）===")
    run(8193, "n=8193")

    print("\n=== 3. 缓冲没被越界那两轮动过 ===")
    a0 = int(mm_in.array[0]) & 0xFFFF
    a1 = int(mm_in.array[63]) & 0xFFFF
    check("首字还是图样", a0 == 0, "读到 %d" % a0)
    check("第 63 字还是图样", a1 == 63, "读到 %d" % a1)

    print("\n=== 4. 守卫之后还能正常跑一块 ===")
    x = np.zeros(1000, dtype=np.int32)
    x[500] = 1234
    mm_in.array[:1000] = (x & 0xFFFF).astype(np.uint32)
    mm_ctrl.write(REG_CTRL, 0)
    time.sleep(0.001)
    mm_ctrl.write(REG_CTRL, 1000 | (1 << 16))
    mm_ctrl.write(REG_CTRL, 1000)
    deadline = time.perf_counter() + 2.0
    hi = 0
    while time.perf_counter() < deadline:
        hi = mm_stat.read(REG_STAT_HI)
        if hi & (1 << 16):
            break
        time.sleep(0.0002)
    check("正常块 done", bool(hi & (1 << 16)), "stat_hi=0x%08X" % hi)
    check("正常块无 overflow", not (hi & (1 << 18)), "stat_hi=0x%08X" % hi)
    check("正常块 n_out=1000", (hi & 0xFFFF) == 1000, "n_out=%d" % (hi & 0xFFFF))
    mm_out = MMIO(0x43C08000, 0x8000)
    # 前端群延迟 96 拍：输入第 500 点的冲激应出现在输出第 596 点。
    # 滤波器组是完美重建的，给 ±1 的定点取整余量。
    v = int(mm_out.array[596]) & 0xFFFF
    check("冲激搬到第 596 点", abs(v - 1234) <= 1, "输出[596]=%d" % v)

    print("\n=== 结论 ===")
    print("  全过。" if ok_all else "  有 FAIL。")
    sys.exit(0 if ok_all else 1)


main()
