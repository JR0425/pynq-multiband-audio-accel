"""看 codec 的 DAC 到底有没有收到数据 —— 读只读区里会跟着信号动的那几个寄存器。

背景：PL 内部一切正常（`tog` 在涨、自收解出 ±2^21），codec 的**可写**寄存器
两边逐字节相同，可耳机就是不响。剩下的可能性只剩"DAC 根本没收到数据"和
"DAC 收到了但模拟侧不出声"两种，而后者用同一份寄存器解释不通。

ADAU1761 的 0x40E0 附近有一批只读回读寄存器，其中有些会跟着电平变。
本脚本不猜哪几个是电平表，直接：

  1. 静默时把 0x4000~0x40FF 连扫三遍，**自己找出会动的寄存器**；
  2. 对这几个寄存器，分别在"放音"和"静默"两种状态下快速采样，
     比较它们的波动范围。

判据：
  放音时波动明显 > 静默时  →  DAC 收到了数据，问题在模拟/耳机侧
  两者差不多              →  DAC 没收到数据，问题在 I2S 到 codec 这一段

用法（板上，root，cwd 在 /home/xilinx/dma_exp）：
    ... dac_meter.py --mode ship     # 出货 fir.bit，用它自己的播放通路
    ... dac_meter.py --mode dbg      # i2sdbg.bit，内置音源
"""

import argparse
import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, "/home/xilinx/dma_exp")

SWEEP = range(0x4000, 0x4100)


def sweep(P):
    return {r: P.i2c_rd(r) for r in SWEEP}


def live_regs(P, rounds=3, gap=0.25):
    """连扫几遍，返回取到过多个不同值的寄存器。"""
    hist = {}
    for i in range(rounds):
        s = sweep(P)
        for r, v in s.items():
            if v is None:
                continue
            hist.setdefault(r, set()).add(v)
        time.sleep(gap)
    return sorted(r for r, vs in hist.items() if len(vs) > 1)


def sample(P, regs, secs):
    """对指定寄存器快速采样，返回 {reg: [值...]}"""
    out = {r: [] for r in regs}
    t_end = time.time() + secs
    while time.time() < t_end:
        for r in regs:
            out[r].append(P.i2c_rd(r))
    return out


def show(tag, data):
    print("\n  --- %s ---" % tag)
    for r in sorted(data):
        vals = [v for v in data[r] if v is not None]
        if not vals:
            continue
        a = np.array(vals, dtype=np.int64)
        rng = int(a.max() - a.min())
        nuniq = len(set(vals))
        print("    0x%04X  min=0x%02X max=0x%02X 跨度=%-3d 取值种数=%-3d 采样 %d 次"
              % (r, a.min(), a.max(), rng, nuniq, len(vals)))


# ---------------------------------------------------------------- 两种模式

def run_ship(P, secs_on, secs_off):
    import hearing_tone as HT
    from pynq import Overlay

    ol = Overlay("/home/xilinx/fir.bit")
    a = ol.audio_codec_ctrl_0
    a.configure()

    # 先让 codec 真在放音，否则扫出来的是"没在放音"的样子
    HT._write_tone("/dev/shm/dacm.wav", 440.0, -6.0, 6.0)
    a.load("/dev/shm/dacm.wav")

    print("  扫静默时的活动寄存器…")
    live = live_regs(P)
    print("  会动的寄存器 %d 个：%s" % (len(live), " ".join("0x%04X" % r for r in live)))

    idle = sample(P, live, secs_off)

    th = threading.Thread(target=a.play)
    th.start()
    time.sleep(0.3)
    tone = sample(P, live, secs_on)
    th.join(timeout=10)
    return live, idle, tone


def run_dbg(P, secs_on, secs_off):
    from pynq import MMIO

    P.BITFILE = "i2sdbg.bit"
    P.HWHFILE = "i2sdbg.hwh"
    dma, ctrl, stat = P.load()
    P.codec_init()
    GPIO_DBG = 0x43C30000
    dbg = MMIO(GPIO_DBG, 0x1000)

    P.set_ctrl(ctrl, 0)                      # 静默
    time.sleep(0.2)
    print("  扫静默时的活动寄存器…")
    live = live_regs(P)
    print("  会动的寄存器 %d 个：%s" % (len(live), " ".join("0x%04X" % r for r in live)))

    idle = sample(P, live, secs_off)

    P.set_ctrl(ctrl, (1 << 0) | (1 << 3))     # run | tone_en
    time.sleep(0.2)
    tone = sample(P, live, secs_on)
    d0 = dbg.read(0x00)
    print("  tone 期间 dbg0=0x%08X（run=%d tone=%d）"
          % (d0, (d0 >> 3) & 1, (d0 >> 1) & 1))
    P.set_ctrl(ctrl, 0)
    return live, idle, tone


def run_pl(P, secs_on, secs_off):
    """全 PL 直连：run=1 是"话筒 -> 前端 -> 耳机"，run=0 是数字静音。

    两边用的是同一批电平表寄存器，所以这组对比能直接回答
    "耳机端到底有没有信号"——不需要耳朵，也不受 PC 麦克风 AEC 影响。
    """
    from pynq import Overlay, MMIO

    ol = Overlay("/home/xilinx/dma_exp/fir_pl.bit")
    P.codec_init(1)
    c = MMIO(0x43C00000, 0x1000)
    c.write(0, 0x07); time.sleep(0.02)      # run|drc|flush
    c.write(0, 0x103); time.sleep(0.005)    # 松 flush，清计数器
    c.write(0, 0x03); time.sleep(0.3)       # run|drc

    print("  扫 run=1 时的活动寄存器…")
    live = live_regs(P)
    print("  会动的寄存器 %d 个：%s" % (len(live), " ".join("0x%04X" % r for r in live)))

    on = sample(P, live, secs_on)
    c.write(0, 0)
    time.sleep(0.2)
    off = sample(P, live, secs_off)
    return live, off, on


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="ship", choices=["ship", "dbg", "pl"])
    ap.add_argument("--on", type=float, default=2.0)
    ap.add_argument("--off", type=float, default=2.0)
    args = ap.parse_args()

    os.environ.setdefault("XILINX_XRT", "/usr")
    import dma_audio_probe as P

    import gc
    gc.disable()

    fn = {"ship": run_ship, "dbg": run_dbg, "pl": run_pl}[args.mode]
    live, idle, tone = fn(P, args.on, args.off)

    show("静默", idle)
    show("run=1（话筒走全链路）" if args.mode == "pl" else "放音 440 Hz（-6 dBFS）", tone)

    print("\n  怎么读：放音时跨度明显大于静默 = DAC 收到数据；两者一样 = 没收到")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
