# fir_pl.bit 的验收探针：整条链在 PL 里跑，PS 只读状态。
#
#   I2S 收 → 前端 → 压缩 → I2S 发
#
# 这里不写任何采样数据，只是：
#   1. 配 codec
#   2. flush 一次、放行
#   3. 每秒读一次 stat0/stat1/stat2，看计数器和水位
#
# 判据：
#   stat0 的 ovf（录音 FIFO 溢出）/ und（播放 FIFO 断流）必须一直是 0
#   stat2 的 oovf（输出 FIFO 溢出）必须一直是 0
#   stat1 的 tx 水位应该稳稳停在一个值（TX_TARGET 附近），不随时间爬
#
# 用法（板上，root）：
#   python3 pl_direct_probe.py --secs 60
#   python3 pl_direct_probe.py --secs 60 --drc 1     # 走压缩通路
#   python3 pl_direct_probe.py --secs 60 --bit /home/xilinx/dma_exp/fir_pl_tx64.bit

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pynq import Overlay, MMIO  # noqa: E402

from dma_audio_probe import codec_init, FS_ACT  # noqa: E402

BASE_CTRL  = 0x43C00000
BASE_STAT  = 0x43C10000
BASE_STAT2 = 0x43C20000
BASE_DBG   = 0x43C30000

CTRL_RUN   = 1 << 0
CTRL_DRC   = 1 << 1
CTRL_FLUSH = 1 << 2
CTRL_CLR   = 1 << 8

# 核的组延迟 96 字 = 48 帧；声学延迟实测 1.23 ms（PLAN 里那次数出来的）
GROUP_DELAY_MS = 96.0 / (2.0 * FS_ACT) * 1e3
ACOUSTIC_MS = 1.23


def arg(name, default):
    if name in sys.argv:
        return sys.argv[sys.argv.index(name) + 1]
    return default


def mmio_at(ol, base, size=0x1000):
    for k, v in ol.ip_dict.items():
        if v.get("phys_addr") == base:
            return getattr(ol, k).mmio
    return MMIO(base, size)


def dbg_at(ol, base):
    """调试口只有带 dbg0/dbg1 的那几版位流才有。

    不能像上面那样"找不到就裸读" —— 读未映射的 AXI 地址会把 CPU 卡死
    （板上 0x43C00014 那次就是这么挂的），所以没有就返回 None。
    """
    for k, v in ol.ip_dict.items():
        if v.get("phys_addr") == base:
            return getattr(ol, k).mmio
    return None


def rd_stat(stat, stat2, dbg=None):
    s0 = stat.read(0x00)
    s1 = stat.read(0x08)
    s2 = stat2.read(0x00)
    d = {
        "ovf":     s0 & 0xFFFF,
        "und":     (s0 >> 16) & 0xFFFF,
        "tx":      s1 & 0xFFFF,
        "cap":     (s1 >> 16) & 0xFFFF,
        "oovf":    s2 & 0xFFFF,
        "tx_peak": (s2 >> 16) & 0xFFFF,
        "din": 0, "dout": 0, "snap": 0,
    }
    if dbg is not None:
        d0 = dbg.read(0x00)
        d["din"]  = d0 & 0xFFFF            # 喂进前端的字数
        d["dout"] = (d0 >> 16) & 0xFFFF    # 前端吐出的字数
        d["snap"] = dbg.read(0x08)
    return d


def snap_text(v):
    """dbg1 的位定义（见 fir_pl_direct.v）。"""
    return ("ph=%d rst=%d rst@din=%d out_ever=%d room=%d tready=%d tvalid=%d ofifo=%d"
            % (v & 3, (v >> 2) & 1, (v >> 3) & 1, (v >> 4) & 1, (v >> 5) & 1,
               (v >> 6) & 1, (v >> 7) & 1, (v >> 8) & 0x3F))


def main():
    secs = float(arg("--secs", "60"))
    drc = arg("--drc", "1") not in ("0", "no", "false")
    bit = arg("--bit", "/home/xilinx/dma_exp/fir_pl.bit")

    if not os.path.exists(bit):
        print("ERROR: 找不到 %s" % bit)
        sys.exit(1)

    ol = Overlay(bit)
    print("  里面的 IP：", sorted(ol.ip_dict.keys()))

    ctrl  = mmio_at(ol, BASE_CTRL)
    stat  = mmio_at(ol, BASE_STAT)
    stat2 = mmio_at(ol, BASE_STAT2)
    dbg = dbg_at(ol, BASE_DBG)
    if dbg is None:
        print("  （这块位流没有调试口，只读状态寄存器）")

    print("== 配 codec ==")
    codec_init()

    # flush 一次：两侧 FIFO 清空、前端复位。**flush 和 run 必须同时给** ——
    # 先 flush 成 run=0 再放行的话，这中间录音 FIFO 会把这段时间的声音全收下，
    # 放行后这段积压永久加在延迟上（块模式那个"中性平衡"的坑就是这个）。
    base = CTRL_RUN | (CTRL_DRC if drc else 0)
    ctrl.write(0, base | CTRL_FLUSH)
    time.sleep(0.02)
    ctrl.write(0, base | CTRL_CLR)   # 松开 flush，顺手清计数器
    time.sleep(0.005)
    ctrl.write(0, base)
    print("== 放行：ctrl=0x%02X（run=1 drc_en=%d）==" % (base, 1 if drc else 0))

    t0 = time.time()
    tick = t0
    first = None
    tx_lo, tx_hi = 1 << 30, -1
    cap_lo, cap_hi = 1 << 30, -1
    tx_sum = cap_sum = 0.0
    n = 0
    prev = rd_stat(stat, stat2, dbg)
    print("   时间   ovf   und   tx   cap  oovf  peak    din   dout   (每秒)")
    while time.time() - t0 < secs:
        time.sleep(0.05)
        d = rd_stat(stat, stat2, dbg)
        tx_lo, tx_hi = min(tx_lo, d["tx"]), max(tx_hi, d["tx"])
        cap_lo, cap_hi = min(cap_lo, d["cap"]), max(cap_hi, d["cap"])
        tx_sum += d["tx"]
        cap_sum += d["cap"]
        n += 1
        if first is None:
            first = d
        if time.time() - tick >= 1.0:
            tick += 1.0
            din_d  = (d["din"]  - prev["din"])  & 0xFFFF
            dout_d = (d["dout"] - prev["dout"]) & 0xFFFF
            print("  %5.1f  %5d %5d  %4d  %4d  %4d  %4d  %5d  %5d"
                  % (time.time() - t0, d["ovf"], d["und"], d["tx"], d["cap"],
                     d["oovf"], d["tx_peak"], din_d, dout_d))
            print("         %s" % snap_text(d["snap"]))
        prev = d

    ctrl.write(0, 0)
    d = prev

    print()
    print("跑了 %.1f s，采样 %d 次" % (secs, n))
    print("  录音 FIFO 溢出 ovf = %d   播放 FIFO 断流 und = %d   输出 FIFO 溢出 oovf = %d"
          % (d["ovf"], d["und"], d["oovf"]))
    print("  tx 水位 min/mean/max = %d / %.0f / %d 字（%.2f / %.2f / %.2f ms）"
          % (tx_lo, tx_sum / max(1, n), tx_hi,
             tx_lo * 1e3 / (2 * FS_ACT), tx_sum / max(1, n) * 1e3 / (2 * FS_ACT),
             tx_hi * 1e3 / (2 * FS_ACT)))
    print("  cap 水位 min/mean/max = %d / %.0f / %d 字（%.2f / %.2f / %.2f ms）"
          % (cap_lo, cap_sum / max(1, n), cap_hi,
             cap_lo * 1e3 / (2 * FS_ACT), cap_sum / max(1, n) * 1e3 / (2 * FS_ACT),
             cap_hi * 1e3 / (2 * FS_ACT)))
    print("  播放 FIFO 见过的最深水位 peak = %d 字（%.2f ms）"
          % (d["tx_peak"], d["tx_peak"] * 1e3 / (2 * FS_ACT)))

    lat = (d["tx"] * 1e3 / (2 * FS_ACT) + 32 * 1e3 / (2 * FS_ACT)
           + GROUP_DELAY_MS + ACOUSTIC_MS)
    print("  嘴到耳 ≈ 播放水位 %.2f + 输出 FIFO 最坏 %.2f + 组延迟 %.2f + 声学 %.2f"
          " ≈ %.2f ms"
          % (d["tx"] * 1e3 / (2 * FS_ACT), 32 * 1e3 / (2 * FS_ACT),
             GROUP_DELAY_MS, ACOUSTIC_MS, lat))

    ok = True
    ok &= (d["ovf"] == 0)
    ok &= (d["und"] == 0)
    ok &= (d["oovf"] == 0)
    if d["ovf"] or d["und"]:
        ok = False
    print("\n%s" % ("[OK] 没有丢字、没有断流" if ok else "[FAIL] 有丢字或断流，看上面"))

    # 收尾：停住 PL，别让耳机一直有声音
    ctrl.write(0, 0)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
