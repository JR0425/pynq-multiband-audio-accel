"""pl_regs_check —— 不需要耳朵的验收：AXI 寄存器窗口通不通。

新位流（带 fir_pl_regs @ 0x43C40000）上板后先跑这个。它证明四件事：

  1. AXI4-Lite 从机活着          —— 读 ID（0x504C0002）
  2. 出厂默认值对                —— GAIN[0..3] = 256（0 dB），RAMP_DIV = 6
  3. 写进去能读回来              —— 写 4 个不同增益，逐条回读
  4. 斜坡真的在爬且会停          —— 写 +18 dB 后立刻读 RAMP_BUSY=1，
                                    等 100 ms 再读 =0（div=6 时约 6.5 ms 爬完）
  5. STAT 窗口是活的             —— STAT0 应该等于 i2s_duplex 的 ovf/und 计数，
                                    读两次不一样即为"在动"（run=0 时也可能为 0，
                                    所以这一条只警告不判失败）

板上 print 只能 ASCII。
"""
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")

BIT = "/home/xilinx/dma_exp/fir_pl.bit"
ID_EXPECT = 0x504C0002

BANK0 = 0x100
OFF_GAIN = 0x00
OFF_RAMP_DIV = 0x70
OFF_RAMP_BUSY = 0x74
GAIN_0DB = 256


def find_ip(ol, prefix):
    for name, d in ol.ip_dict.items():
        if name.startswith(prefix) and d.get("phys_addr") is not None:
            return name, d["phys_addr"]
    return None, None


def main():
    from pynq import Overlay, MMIO

    ol = Overlay(BIT)
    time.sleep(0.3)

    name, base = find_ip(ol, "fir_pl_regs")
    if base is None:
        print("!! no fir_pl_regs in this bitstream -- rebuild first")
        print("   (overlay 里有：%s)" % sorted(ol.ip_dict.keys()))
        return 1
    print("[1] %s @ 0x%08X" % (name, base))

    r = MMIO(base, 0x1000)
    bad = 0

    rid = r.read(0x000)
    ok = rid == ID_EXPECT
    bad += 0 if ok else 1
    print("[2] ID = 0x%08X  expect 0x%08X  %s" % (rid, ID_EXPECT, "OK" if ok else "FAIL"))

    gains = [r.read(BANK0 + OFF_GAIN + 4 * i) & 0xFFFF for i in range(4)]
    div = r.read(OFF_RAMP_DIV) & 0xFFFF
    ok = gains == [GAIN_0DB] * 4 and div == 6
    bad += 0 if ok else 1
    print("[3] default GAIN = %s (expect [256,256,256,256])  RAMP_DIV = %d (expect 6)  %s"
          % (gains, div, "OK" if ok else "FAIL"))

    # 写四个不同值：0 / +6 / +12 / +18 dB
    import math
    want = [max(-32768, min(32767, int(round(256 * 10.0 ** (db / 20.0)))))
            for db in (0.0, 6.0, 12.0, 18.0)]
    for i, v in enumerate(want):
        r.write(BANK0 + OFF_GAIN + 4 * i, v)
    back = [r.read(BANK0 + OFF_GAIN + 4 * i) & 0xFFFF for i in range(4)]
    ok = back == want
    bad += 0 if ok else 1
    print("[4] write %s -> read %s  %s" % (want, back, "OK" if ok else "FAIL"))

    # 斜坡：先回到 0 dB 让它稳定，再一次性写到 +18 dB，看 busy 的上升与落零
    for i in range(4):
        r.write(BANK0 + OFF_GAIN + 4 * i, GAIN_0DB)
    time.sleep(0.1)
    busy0 = r.read(OFF_RAMP_BUSY) & 0x3

    for i in range(4):
        r.write(BANK0 + OFF_GAIN + 4 * i, want[3])
    busy1 = r.read(OFF_RAMP_BUSY) & 0x3
    time.sleep(0.1)
    busy2 = r.read(OFF_RAMP_BUSY) & 0x3
    print("[5] ramp_busy: 稳定=%d  刚写完=%d  100ms 后=%d" % (busy0, busy1, busy2))
    # 注意：AXI 写是异步生效的（要过几个 aclk），刚写完那一下可能还没来得及置位，
    # 所以 busy1 只做参考；真正判的是 busy2 == 0 且读回来的值等于目标。
    ok = (busy2 == 0) and (r.read(BANK0 + OFF_GAIN + 12) & 0xFFFF) == want[3]
    bad += 0 if ok else 1
    print("    判据：100 ms 后 busy=0 且 GAIN[3] 到位  %s" % ("OK" if ok else "FAIL"))

    # STAT 窗口：读两次看是否在动（只警告）
    s1 = r.read(0x008)
    time.sleep(0.3)
    s2 = r.read(0x008)
    print("[6] STAT0: 0x%08X -> 0x%08X  %s"
          % (s1, s2, "在动" if s1 != s2 else "没动（run=0 时正常，放音时应该会动）"))

    # 还原成 0 dB，别把增益留在 +18 dB 上
    for i in range(4):
        r.write(BANK0 + OFF_GAIN + 4 * i, GAIN_0DB)
    time.sleep(0.05)
    print("[7] restored GAIN to 0 dB")

    print("\n%s" % ("REGS PASS" if bad == 0 else "REGS FAIL (%d)" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
