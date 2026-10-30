"""并排读 bank0(左)/bank1(右) 全部可写参数，看“右耳跟随左耳”有没有真的落到寄存器。

    sudo /usr/local/share/pynq-venv/bin/python3 bankdiff.py
"""
import mmap
import os

BASE = 0x43C40000
GRP = [("GAIN", 0x00), ("THR", 0x10), ("RATIO", 0x20),
       ("ATK", 0x30), ("REL", 0x40), ("RAMP_DIV", 0x70)]

fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=BASE)
mv = memoryview(m).cast("I")


def rd(off):
    return mv[off >> 2]


print("ctrl(0x43C00004)=0x%08x  id=0x%08x" % (rd(0x04), rd(0x00)))
bad = 0
for name, base in GRP:
    n = 1 if name == "RAMP_DIV" else 4
    for k in range(n):
        a = rd(0x100 + base + k * 4)
        b = rd(0x200 + base + k * 4)
        flag = "" if a == b else "   <-- 不同"
        if a != b:
            bad += 1
        if name == "GAIN":
            print("%-9s seg%d  左 %6d (0x%04x)  右 %6d (0x%04x)  %.2f dB%s"
                  % (name, k, a & 0xFFFF, a & 0xFFFF, b & 0xFFFF, b & 0xFFFF,
                     (a & 0xFFFF) / 256.0, flag))
        else:
            print("%-9s seg%d  左 0x%08x  右 0x%08x%s" % (name, k, a, b, flag))
print("不同项 %d/25" % bad)
del mv
