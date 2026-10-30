"""读 PL 的「啪」计数器和播放指针，看在静默/播放两种情况下会不会动。

POP_CNT 的判据在 RTL 里：|fe_din - fe_din[前一拍]| 超过阈值就 +1，取的是
**进增益之前**的输入（播放时就是 ply_sample）。所以
  - 播放环被读空（读到没写过的旧字）→ 阶梯跳变 → POP_CNT 会涨
  - POP_CNT 一直不动而耳朵还听到啪 → 啪不在 fe_din 这一侧

    sudo /usr/local/share/pynq-venv/bin/python3 popcnt.py 30
"""
import mmap
import os
import sys
import time

BASE = 0x43C40000
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0

fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=BASE)
mv = memoryview(m).cast("I")


def rd(off):
    return mv[off >> 2]


def one(tag):
    print("%-8s ply_rd=%-10d ply_ctl=0x%x  POP_CNT=%-6d POP_MAX=%-6d  peak_in=%d"
          % (tag, rd(0x1C), rd(0x24) & 0xFFFF, rd(0x80), rd(0x84), rd(0x40)))


one("起始")
t0 = time.time()
last = rd(0x80)
while time.time() - t0 < secs:
    time.sleep(2.0)
    c = rd(0x80)
    print("  %5.1fs  POP_CNT=%d  (+%d)" % (time.time() - t0, c, (c - last) & 0xFFFFFFFF))
    last = c
one("结束")
del mv
