"""i2sdbg.bit 的板上读数：内置音源 + 自收 + 翻转计数。

为什么要有这一步：仿真过了、引脚逐脚和出货版一致、codec 也停在被证实"听到过
声音"的那份寄存器上，耳机里就是没声。所以不再推断，直接读三个读数：

    tog        sdata_o 引脚上翻了多少次。不动 = G18 上根本没有波形
    pl / pr    把 sdata_o 在 FPGA 内部接回一个和 iis_deser 同构的接收器解出来的字
               （ctrl[3]=1 时应该恒等于 0x200000 / 0xE00000 交替的 ±2^21 方波）
    pframe     自收器数到的帧数

ctrl = 0x9 = run | tone_en。不碰 DMA、不碰话筒。

用法（板上 root）：
    echo xilinx | sudo -S /usr/local/share/pynq-venv/bin/python3 dbg_tone.py
    ... dbg_tone.py --bursts 3 --on 8 --off 5
"""
import argparse
import os
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")
import dma_audio_probe as P  # noqa: E402

GPIO_DBG  = 0x43C30000
GPIO_DBG2 = 0x43C40000

CTRL_TONE = 1 << 3


def s24(v):
    v &= 0xFFFFFF
    return v - 0x1000000 if v & 0x800000 else v


def read_dbg(dbg, dbg2):
    d0 = dbg.read(0x00)     # gpio_io_i  —— dbg0
    d1 = dbg.read(0x08)     # gpio2_io_i —— dbg1
    d2 = dbg2.read(0x00)    # dbg2
    return {
        "pl": s24(d0 >> 8),
        "pframe": (d0 >> 4) & 0xF,
        "run": (d0 >> 3) & 1,
        "loop": (d0 >> 2) & 1,
        "tone": (d0 >> 1) & 1,
        "sdata": d0 & 1,
        "pr": s24(d1 >> 8),
        "tog": (d2 >> 16) & 0xFFFF,
        "wr": d2 & 0xFFFF,
        "raw0": d0, "raw1": d1, "raw2": d2,
    }


def show(tag, d, prev=None):
    line = ("  [%s] L=%8d R=%8d pframe=%X tog=%5d wr=%5d sdata=%d"
            % (tag, d["pl"], d["pr"], d["pframe"], d["tog"], d["wr"], d["sdata"]))
    if prev is not None:
        dt = (d["tog"] - prev["tog"]) & 0xFFFF
        dw = (d["wr"] - prev["wr"]) & 0xFFFF
        line += "   Δtog=%d Δwr=%d" % (dt, dw)
    print(line)
    print("        raw: dbg0=0x%08X dbg1=0x%08X dbg2=0x%08X"
          % (d["raw0"], d["raw1"], d["raw2"]))
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default="i2sdbg.bit")
    ap.add_argument("--bursts", type=int, default=3)
    ap.add_argument("--on", type=float, default=8.0)
    ap.add_argument("--off", type=float, default=5.0)
    ap.add_argument("--no-codec", action="store_true",
                    help="不重配 codec（沿用上一次的状态）")
    a = ap.parse_args()

    P.BITFILE = a.bit
    P.HWHFILE = a.bit.replace(".bit", ".hwh")
    if not os.path.exists(P.BITFILE):
        print("ERROR: 板上没有 %s" % P.BITFILE)
        sys.exit(1)

    print("== 下载 %s ==" % a.bit)
    dma, ctrl, stat = P.load()

    if not a.no_codec:
        print("== 配 codec（libaudio + unmute）==")
        P.codec_init()

    dbg  = P.MMIO(GPIO_DBG, 0x1000)
    dbg2 = P.MMIO(GPIO_DBG2, 0x1000)
    print("  dbg  GPIO 0x%08X / 0x%08X" % (GPIO_DBG, GPIO_DBG2))

    P.set_ctrl(ctrl, 0)
    time.sleep(0.05)

    print("== 静默基线（ctrl=0）==")
    d0 = read_dbg(dbg, dbg2)
    show("静默", d0)
    time.sleep(1.0)
    d1v = read_dbg(dbg, dbg2)
    show("1 秒后", d1v, d0)

    print("== 内置音源：±2^21 方波 ≈440 Hz，响/停交替 ==")
    bits = P.CTRL_RUN | CTRL_TONE
    prev = d1v
    t0 = time.time()
    for b in range(a.bursts):
        P.set_ctrl(ctrl, 0)
        time.sleep(0.05)
        P.set_ctrl(ctrl, bits)
        print("  >>> 第 %d 段：响 %.0f 秒（%4.1f s）"
              % (b + 1, a.on, time.time() - t0))
        sys.stdout.flush()
        t_end = time.time() + a.on
        last = time.time()
        while time.time() < t_end:
            time.sleep(0.5)
            if time.time() - last >= 2.0:
                last = time.time()
                d = read_dbg(dbg, dbg2)
                show("响", d, prev)
                prev = d
        P.set_ctrl(ctrl, 0)
        print("  --- 停 %.0f 秒（%4.1f s）" % (a.off, time.time() - t0))
        sys.stdout.flush()
        time.sleep(a.off)
        d = read_dbg(dbg, dbg2)
        show("停", d, prev)
        prev = d

    P.set_ctrl(ctrl, 0)
    print("  结束，ctrl=0（已静音）")

    print("== 判读 ==")
    print("  Δtog≈0 且 L/R 一直是 0  → sdata_o 引脚上没有波形（PL 内部问题）")
    print("  Δtog 在涨但耳机没声    → 波形出去了，问题在 codec/耳机这一侧")
    print("  L/R 只在 ±2097152 跳   → 自收解对了，串行器的数据是对的")


main()
