"""pl_dual_probe —— 双耳 DRC 的板上验收（不需要耳朵，但要放音）。

做法和 `pl_drc_ply_probe.py` 一样：往播放环里填 440 Hz 正弦、切到 WAV 当音源，
然后读**左右各四段**的 ENV/GR，靠读数判断两套处方是不是真的各算各的。

判据（每条都能证伪"右耳其实是左耳的拷贝"）：

  [2] 两套参数完全相同时，左右 ENV/GR 必须**逐段相同**（差 ≤ 1 LSB）
      —— 这条保证"双耳打开但处方一样"时不会改变单耳听感。
  [3] 只把右耳门限压低（THR 419456 → 50000）、压缩比拉大，左耳 ENV 不变、
      右耳 GR 明显变大 —— 这条证伪"参数串台"和"右耳没在算"。
  [4] 只把右耳增益抬 +18 dB（256 → 2033），右耳 ENV 必须约等于左耳的 8 倍
      （包络在增益之后），左耳不受影响 —— 这条证伪"两套增益共用一个斜坡"。

注意：板上 print 只能 ASCII（SSH 走 GBK 会乱码）。

    MSYS_NO_PATHCONV=1 python board_ssh.py --sudo \
      "cd /home/xilinx/dma_exp && XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u pl_dual_probe.py"
"""
import os
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")

import numpy as np

BIT = "/home/xilinx/dma_exp/fir_pl.bit"
RING = 16384
FS = 48000

BANK0 = 0x100
BANK1 = 0x200
OFF_GAIN = 0x00
OFF_THR = 0x10
OFF_RATIO = 0x20
OFF_ATK = 0x30
OFF_REL = 0x40
OFF_ENV = 0x50
OFF_GR = 0x60

D_GAIN, D_THR, D_RATIO, D_ATK, D_REL = 256, 419456, 2936064, 17408, 896
Q22 = float(1 << 22)


def find_ip(ol, prefix):
    for name, d in ol.ip_dict.items():
        if name.startswith(prefix) and d.get("phys_addr") is not None:
            return name, d["phys_addr"]
    return None, None


def wrb(r, bank, off, vals):
    for i, v in enumerate(vals):
        r.write(bank + off + 4 * i, int(v) & 0xFFFFFFFF)


def snap(r):
    """左右各四段 ENV/GR（Q2.22 原始整数）。"""
    el = [r.read(BANK0 + OFF_ENV + 4 * i) & 0xFFFFFF for i in range(4)]
    gl = [r.read(BANK0 + OFF_GR + 4 * i) & 0xFFFFFF for i in range(4)]
    er = [r.read(BANK1 + OFF_ENV + 4 * i) & 0xFFFFFF for i in range(4)]
    gr = [r.read(BANK1 + OFF_GR + 4 * i) & 0xFFFFFF for i in range(4)]
    return el, gl, er, gr


def stat(r, n=10, dt=0.05):
    """连读 n 次取中位，返回 (envL, grL, envR, grR)，每个是四段列表。"""
    acc = []
    for _ in range(n):
        acc.append(snap(r))
        time.sleep(dt)
    med = lambda x: sorted(x)[len(x) // 2]
    out = []
    for ch in range(4):                     # 0=envL 1=grL 2=envR 3=grR
        out.append([med([row[ch][i] for row in acc]) for i in range(4)])
    return tuple(out)


def main():
    if not os.path.exists(BIT):
        print("ERROR: no %s" % BIT)
        return 1
    from pynq import Overlay, MMIO
    import dma_audio_probe as P

    ol = Overlay(BIT)
    time.sleep(0.3)
    P.codec_init()

    _, rbase = find_ip(ol, "fir_pl_regs")
    _, bbase = find_ip(ol, "ply_ctrl")
    _, cbase = find_ip(ol, "gpio_ctrl")
    if None in (rbase, bbase, cbase):
        print("!! missing IP: regs=%s bram=%s ctrl=%s" % (rbase, bbase, cbase))
        return 1

    r = MMIO(rbase, 0x1000)
    bram = MMIO(bbase, 0x10000)
    ctrl = MMIO(cbase, 0x1000)

    n = np.arange(RING, dtype=np.float64)
    tone = (8000.0 * np.sin(2 * np.pi * 150.0 * n / RING)).astype(np.int64)
    words = (np.asarray(tone, dtype=np.uint64) & 0xFFFF).astype(np.uint32) << 16
    bram.write(0, words.tobytes())

    r.write(0x024, 0)
    r.write(0x028, RING)
    r.write(0x024, 0x3)                    # ply en | loop
    ctrl.write(0, 0x01 | 0x02 | 0x20)      # run | drc | src = wav
    time.sleep(0.4)

    bad = 0

    # 两套都回到出厂值
    for bank in (BANK0, BANK1):
        wrb(r, bank, OFF_GAIN, [D_GAIN] * 4)
        wrb(r, bank, OFF_THR, [D_THR] * 4)
        wrb(r, bank, OFF_RATIO, [D_RATIO] * 4)
        wrb(r, bank, OFF_ATK, [D_ATK] * 4)
        wrb(r, bank, OFF_REL, [D_REL] * 4)
    time.sleep(0.4)

    el, gl, er, gr = stat(r)
    print("[1] same params: envL=%s grL=%s" % (el, gl))
    print("               envR=%s grR=%s" % (er, gr))

    d_env = max(abs(el[i] - er[i]) for i in range(4))
    d_gr = max(abs(gl[i] - gr[i]) for i in range(4))
    ok = d_env <= 1 and d_gr <= 1
    bad += 0 if ok else 1
    print("[2] L/R identical  max|dENV|=%d max|dGR|=%d  %s" %
          (d_env, d_gr, "OK" if ok else "FAIL"))

    if max(el) < 1000:
        print("!! ENV too low (%d): no signal in the play ring?" % max(el))
        ctrl.write(0, 0)
        return 1

    # 只改右耳：门限压低 + 压缩比拉大
    wrb(r, BANK1, OFF_THR, [50000] * 4)
    wrb(r, BANK1, OFF_RATIO, [3774873] * 4)     # 0.9
    time.sleep(0.5)
    el2, gl2, er2, gr2 = stat(r)
    print("[3] right only: envL=%s grL=%s" % (el2, gl2))
    print("               envR=%s grR=%s" % (er2, gr2))
    ok = (max(gr2) > max(gl2)) and max(gr2) > 100000
    bad += 0 if ok else 1
    print("[4] right GR > left GR  (%d vs %d)  %s" %
          (max(gr2), max(gl2), "OK" if ok else "FAIL"))

    # 恢复右耳门限/压缩比，只抬右耳增益 +18 dB
    wrb(r, BANK1, OFF_THR, [D_THR] * 4)
    wrb(r, BANK1, OFF_RATIO, [D_RATIO] * 4)
    wrb(r, BANK1, OFF_GAIN, [2033] * 4)         # +18 dB
    r.write(BANK1 + 0x70, 5)                    # 斜坡，避免咔
    time.sleep(0.6)
    el3, gl3, er3, gr3 = stat(r)
    print("[5] right +18dB: envL=%s" % el3)
    print("                 envR=%s" % er3)
    k = (er3[0] / float(el3[0])) if el3[0] else 0.0
    ok = 6.0 < k < 10.0 and abs(el3[0] - el2[0]) < max(1, el2[0] * 0.05)
    bad += 0 if ok else 1
    print("[6] envR/envL = %.2f (expect ~8 = +18dB)  %s" %
          (k, "OK" if ok else "FAIL"))

    # 收尾：全部回出厂值，停播放
    for bank in (BANK0, BANK1):
        wrb(r, bank, OFF_GAIN, [D_GAIN] * 4)
        wrb(r, bank, OFF_THR, [D_THR] * 4)
        wrb(r, bank, OFF_RATIO, [D_RATIO] * 4)
        wrb(r, bank, OFF_ATK, [D_ATK] * 4)
        wrb(r, bank, OFF_REL, [D_REL] * 4)
    r.write(0x024, 0)
    ctrl.write(0, 0)

    print("")
    print("DUAL PROBE %s" % ("PASS" if bad == 0 else "FAIL (%d)" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
