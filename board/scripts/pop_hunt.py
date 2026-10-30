"""抓「一边突然响一下」：100 Hz 盯峰值表 + 计数器，见尖峰就把抓取缓冲那段波形存下来。

    sudo python3 pop_hunt.py -t 300          # 盯 5 分钟
    sudo python3 pop_hunt.py -t 300 --thr 3000

为什么要这样抓：
  · peak_in/peak_out 是每 768 采样（16 ms）一个窗的 |max|，10 ms 读一次正好不漏窗；
  · 判据用「比最近 5 秒的典型值高一大截」，不看绝对值 —— 底噪本来就有几百；
  · 尖峰出现的当下立刻冻抓取缓冲，那 84 ms 里就含着刚响的那一下；
  · 进侧（peak_in）是共用的单声道输入，出侧（peak_out）只有左耳 ——
    如果两边都不动而人听到了，说明响在右耳的数字链路之后或模拟侧。

输出：
  控制台每秒一行；事件行以 POP 开头；
  /home/xilinx/dma_exp/pop_hunt.csv  每行 t,pin,pout,ovf,und,oovf,mpocnt,mpopeak
  /home/xilinx/dma_exp/pop_<t>.json  出事的那个 84 ms 窗口（in / out 各 4096 点）
"""

import argparse
import json
import os
import sys
import time

from pynq import MMIO

REGS_BASE = 0x43C40000
CAP_BASE = 0x43C60000

OFF_STAT0 = 0x08
OFF_STAT2 = 0x10
OFF_PLY_UND = 0x20
OFF_PEAK_IN = 0x40
OFF_PEAK_OUT = 0x44
OFF_MPO_CNT = 0x50
OFF_MPO_PEAK = 0x54
OFF_SCOPE_CTL = 0x64
OFF_SCOPE_WR = 0x6C
OFF_CAP_IDX = 0x78                 # 写：抓取缓冲读下标
OFF_CAP_DATA = 0x7C                # 读：该下标的字

OUT_DIR = "/home/xilinx/dma_exp"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-t", "--secs", type=float, default=180.0)
    ap.add_argument("--thr", type=int, default=4000,
                    help="峰值的绝对下限（int16），低于它的一律不算事件")
    ap.add_argument("--ratio", type=float, default=6.0,
                    help="相对最近典型值的倍数")
    ap.add_argument("--period", type=float, default=0.010)
    ap.add_argument("--cap", type=int, default=0,
                    help="1 = 出事时把 84 ms 抓取窗口存下来（走 0x78/0x7C 窗口读）。"
                         "需要 2026-10-30 夜之后的位流（抓取缓冲已接到 fir_pl_regs），"
                         "老位流上 0x78/0x7C 是空的，读出来全 0 —— 默认关。")
    a = ap.parse_args()

    regs = MMIO(REGS_BASE, 0x1000)
    cap = True if a.cap else None      # 只作开关；读数据走 regs 的 0x78/0x7C
    csv_path = os.path.join(OUT_DIR, "pop_hunt.csv")
    f = open(csv_path, "w", buffering=1)

    hist = []          # 最近 500 个窗的 peak_out
    t0 = time.time()
    nxt_report = t0 + 1.0
    sec_max = 0
    sec_max_in = 0
    events = 0

    print("盯 %s .. 现在起别说话、别碰桌子；听到响就记下大概几点" % (
        time.strftime("%H:%M:%S", time.localtime(t0 + a.secs))))

    while time.time() - t0 < a.secs:
        now = time.time()
        pin = regs.read(OFF_PEAK_IN) & 0xFFFF
        pout = regs.read(OFF_PEAK_OUT) & 0xFFFF
        stat0 = regs.read(OFF_STAT0)
        ovf, und = stat0 & 0xFFFF, (stat0 >> 16) & 0xFFFF
        oovf = regs.read(OFF_STAT2) & 0xFFFF
        mpocnt = regs.read(OFF_MPO_CNT)
        mpopeak = regs.read(OFF_MPO_PEAK) & 0xFFFF

        f.write("%.3f,%d,%d,%d,%d,%d,%d,%d\n" % (now - t0, pin, pout, ovf, und, oovf, mpocnt, mpopeak))

        sec_max = max(sec_max, pout)
        sec_max_in = max(sec_max_in, pin)

        base = sorted(hist)[len(hist) // 2] if len(hist) >= 50 else 0
        if pout >= a.thr and pout >= a.ratio * max(base, 1):
            events += 1
            stamp = time.strftime("%H%M%S")
            w = grab_window(regs, cap) if cap is not None else {"in": [], "out": [], "fs": 48828.0}
            path = os.path.join(OUT_DIR, "pop_%s_%d.json" % (stamp, events))
            w.update({"t": now - t0, "clock": time.strftime("%H:%M:%S"),
                      "peak_in": pin, "peak_out": pout, "base": base,
                      "ovf": ovf, "und": und, "oovf": oovf,
                      "mpo_cnt": mpocnt, "mpo_peak": mpopeak})
            try:
                with open(path, "w") as g:
                    json.dump(w, g)
            except Exception as ex:
                print("写窗口失败 %s" % ex)
            print("POP %s  t=%.1fs  out=%d (典型 %d)  in=%d  ovf=%d und=%d oovf=%d  -> %s"
                  % (w["clock"], now - t0, pout, base, pin, ovf, und, oovf, path))

        hist.append(pout)
        if len(hist) > 500:
            del hist[0]

        if now >= nxt_report:
            print("  %s  %6.1fs  本秒峰 out=%5d in=%5d  ovf=%d und=%d oovf=%d 事件=%d"
                  % (time.strftime("%H:%M:%S"), now - t0, sec_max, sec_max_in,
                     ovf, und, oovf, events))
            sec_max = 0
            sec_max_in = 0
            nxt_report += 1.0
        time.sleep(a.period)

    f.close()
    print("结束，事件 %d 个；CSV 在 %s" % (events, csv_path))


def grab_window(regs, cap):
    """冻 → 逐字读 4096 个 → 解冻。

    2026-10-30 夜：不再从 0x43C60000 整块读。那片 AXI BRAM 的读通路断的
    （读回恒 0x8），现在 BRAM 的 A 口接在 fir_pl_regs 上：写 0x78 选下标、
    读 0x7C 取字。冻结期间环形缓冲不变，4096 个点大约读 10 ms。
    """
    import array
    regs.write(OFF_SCOPE_CTL, 1)
    time.sleep(0.002)
    ptr = regs.read(OFF_SCOPE_WR) & 0xFFF
    words = []
    for i in range(4096):
        regs.write(OFF_CAP_IDX, i)
        words.append(regs.read(OFF_CAP_DATA))
    regs.write(OFF_SCOPE_CTL, 0)
    v = array.array("I", words)
    rot = ptr % 4096
    v = v[rot:] + v[:rot]
    return {
        "fs": 48828.0,
        "in": [((x & 0xFFFF) ^ 0x8000) - 0x8000 for x in v],
        "out": [(((x >> 16) & 0xFFFF) ^ 0x8000) - 0x8000 for x in v],
    }


if __name__ == "__main__":
    sys.exit(main())
