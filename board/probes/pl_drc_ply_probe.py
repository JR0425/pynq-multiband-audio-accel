"""pl_drc_ply_probe —— 用 WAV 波表当音源的 DRC 验收（不需要话筒、不需要耳朵）。

`pl_drc_probe.py` 靠话筒，午夜环境噪声只有 −100 dBFS 时量不出东西。
这个脚本改成：往播放环里填一段 440 Hz 正弦（环长 16384 采样正好 150 个周期，
回绕处不产生咔哒），把源切到 WAV，然后读 ENV/GR 看压缩器动不动。

判据：
  1. 出厂参数下（THR = 419456 ≈ −20 dBFS，信号约 0.24 FS）GR 必须 > 0，
     且 GR/(ENV-THR) ≈ RATIO = 0.7
  2. 把 THR 抬到满量程附近，GR 必须回到 0（阈值真的在比）
  3. ATK=REL=0（无记忆压缩）时仍然压，数值和 WDRC 不同但同量级

注意：板上 print 只能 ASCII。
"""
import os
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")

import numpy as np

BIT = "/home/xilinx/dma_exp/fir_pl.bit"
BRAM_BASE = 0x43C50000
RING = 16384
FS = 48000

BANK0 = 0x100
OFF_GAIN = 0x00
OFF_THR = 0x10
OFF_RATIO = 0x20
OFF_ATK = 0x30
OFF_REL = 0x40
OFF_ENV = 0x50
OFF_GR = 0x60

D_THR, D_RATIO, D_ATK, D_REL = 419456, 2936064, 17408, 896


def find_ip(ol, prefix):
    for name, d in ol.ip_dict.items():
        if name.startswith(prefix) and d.get("phys_addr") is not None:
            return name, d["phys_addr"]
    return None, None
def find_bram(ol):
    """播放环的地址。

    2026-10-29 板上实测：`ply_ctrl` **不在 ip_dict 里**（BRAM 被 PYNQ 归到存储器，
    ip_dict 只有 fir_pl_regs_0 / gpio_* 这几个），所以原来按名字找会一直拿到 None。
    这里先试 ip_dict，再试 mem_dict，最后退回固定地址 —— 和已经在用的
    `ply_upload.py` 是同一个常量。
    """
    for name, d in ol.ip_dict.items():
        if name.startswith("ply") and d.get("phys_addr") is not None:
            return name, d["phys_addr"]
    for name, d in getattr(ol, "mem_dict", {}).items():
        if name.startswith("ply") and d.get("phys_addr") is not None:
            return name, d["phys_addr"]
    return "ply_bram(fixed)", 0x43C50000




def wr4(r, off, vals):
    for i, v in enumerate(vals):
        r.write(BANK0 + off + 4 * i, v & 0xFFFFFFFF)


def snap(r):
    env = [r.read(BANK0 + OFF_ENV + 4 * i) & 0xFFFFFF for i in range(4)]
    gr = [r.read(BANK0 + OFF_GR + 4 * i) & 0xFFFFFF for i in range(4)]
    return env, gr


def stat(r, n=8, dt=0.05):
    """连读 n 次，返回每个通道的 (env 中位, gr 中位, gr 最大)。"""
    e = [[], [], [], []]
    g = [[], [], [], []]
    for _ in range(n):
        env, gr = snap(r)
        for i in range(4):
            e[i].append(env[i])
            g[i].append(gr[i])
        time.sleep(dt)
    med = lambda x: sorted(x)[len(x) // 2]
    return ([med(e[i]) for i in range(4)], [med(g[i]) for i in range(4)],
            [max(g[i]) for i in range(4)])


def main():
    if not os.path.exists(BIT):
        print("ERROR: no %s" % BIT)
        return 1
    from pynq import Overlay, MMIO
    import dma_audio_probe as P

    ol = Overlay(BIT)
    time.sleep(0.3)
    P.codec_init()

    name, rbase = find_ip(ol, "fir_pl_regs")
    bname, bbase = find_bram(ol)
    cname, cbase = find_ip(ol, "gpio_ctrl")
    if rbase is None or bbase is None or cbase is None:
        print("!! missing IP: regs=%s bram=%s ctrl=%s" % (rbase, bbase, cbase))
        return 1
    print("[0] regs @ 0x%08X  bram @ 0x%08X  ctrl @ 0x%08X" % (rbase, bbase, cbase))

    r = MMIO(rbase, 0x1000)
    bram = MMIO(bbase, 0x10000)
    ctrl = MMIO(cbase, 0x1000)

    # 440 Hz 左右的正弦，16384 采样里正好 150 个周期（回绕不咔哒）
    n = np.arange(RING, dtype=np.float64)
    tone = (8000.0 * np.sin(2 * np.pi * 150.0 * n / RING)).astype(np.int64)
    words = (np.asarray(tone, dtype=np.uint64) & 0xFFFF).astype(np.uint32) << 16
    bram.write(0, words.tobytes())
    print("[1] tone written: 150 cycles / %d samples (%.1f Hz), amp 8000" %
          (RING, 150.0 * FS / RING))

    # 播放：先停，再设长度，再开
    r.write(0x024, 0)
    r.write(0x028, RING)
    r.write(0x024, 0x3)                       # en | loop
    ctrl.write(0, 0x01 | 0x02 | 0x20)         # run | drc | src=wav
    time.sleep(0.3)

    bad = 0
    env, gr, grmax = stat(r)
    print("[2] factory: env=%s gr=%s (max %s)" % (env, gr, grmax))
    ratio = []
    for i in range(4):
        d = env[i] - D_THR
        if d > 0 and gr[i] > 0:
            ratio.append(round(float(gr[i]) / d, 2))
    # gr_o 是**衰减量**：y = thr + (a-thr)·ratio  →  gr = (a-thr)·(1-ratio)。
    # 出厂 ratio=0.7 所以这里应该是 0.3，不是 0.7（2026-10-29 板上实测 0.30，
    # 原来按 0.7 判，白报了一次 FAIL）。
    print("    GR/(ENV-THR) = %s   expect ~%.2f (= 1-ratio)" % (ratio, 1.0 - D_RATIO / 4194304.0))
    ok = max(gr) > 0 and ratio and all(0.18 <= x <= 0.45 for x in ratio)
    bad += 0 if ok else 1
    print("[3] 出厂参数下压缩器工作  %s" % ("OK" if ok else "FAIL"))

    # 门限抬到 2.0 FS -> 不该再压
    wr4(r, OFF_THR, [8000000] * 4)
    time.sleep(0.2)
    env2, gr2, grmax2 = stat(r)
    ok = max(gr2) == 0
    bad += 0 if ok else 1
    print("[4] THR 抬到 2.0FS: gr=%s（应全 0）  %s" % (gr2, "OK" if ok else "FAIL"))
    wr4(r, OFF_THR, [D_THR] * 4)

    # 无记忆压缩
    wr4(r, OFF_ATK, [0] * 4)
    wr4(r, OFF_REL, [0] * 4)
    time.sleep(0.2)
    env3, gr3, grmax3 = stat(r)
    ratio3 = []
    for i in range(4):
        d = env3[i] - D_THR
        if d > 0 and gr3[i] > 0:
            ratio3.append(round(float(gr3[i]) / d, 2))
    ok = max(gr3) > 0
    bad += 0 if ok else 1
    print("[5] ATK=REL=0（无记忆）: env=%s gr=%s  ratio=%s  %s"
          % (env3, gr3, ratio3, "OK" if ok else "FAIL"))

    # 收尾：停播放、参数复原、通路关掉
    r.write(0x024, 0)
    ctrl.write(0, 0)
    wr4(r, OFF_ATK, [D_ATK] * 4)
    wr4(r, OFF_REL, [D_REL] * 4)
    wr4(r, OFF_THR, [D_THR] * 4)
    print("[6] stop: ply_ctl=0, ctrl=0, params restored")

    print("")
    print("DRC PLY PROBE %s" % ("PASS" if bad == 0 else "FAIL (%d)" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
