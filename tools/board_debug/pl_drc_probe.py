"""pl_drc_probe —— 批 2 验收：DRC 四参数在板上真的驱动了通路。

不需要耳朵。做法是用话筒当输入源（不依赖 PS 补数、也不依赖 WAV 播放进程），
读银行里的 ENV / GR，然后改 THR / ATK / REL 看读数怎么动。

三条判据：
  1. 默认出厂值：THR 419456 / RATIO 2936064 / ATK 17408 / REL 896
  2. 把 THR 压到 1000（远低于任何环境噪声）后，GR 必须离开 0，
     且 GR / (ENV-THR) 应≈ RATIO(0.7) —— 说明 thr 和 ratio 都进了数据通路
  3. 把 ATK/REL 写 0（无记忆压缩）后仍然有 GR，且 ENV 会跟着瞬时幅度跳
     （WDRC 下 ENV 是平滑的，跳不起来）。只打印对比，不做数值断言。

注意：板上 print 只能 ASCII。
"""
import os
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")

BIT = "/home/xilinx/dma_exp/fir_pl.bit"
BASE_CTRL = 0x43C00000
BASE_REGS = 0x43C40000

CTRL_RUN = 0x01
CTRL_DRC = 0x02
CTRL_FLUSH = 0x04
CTRL_CLR = 0x100

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


def wr4(r, off, vals):
    for i, v in enumerate(vals):
        r.write(BANK0 + off + 4 * i, v & 0xFFFFFFFF)


def rd4(r, off):
    return [r.read(BANK0 + off + 4 * i) for i in range(4)]


def read_obs(r):
    """和 fir_pl_live.read_obs 一样的口径：peak_in/out + 四段 env/gr。"""
    peak_in = r.read(0x040) & 0xFFFF
    peak_out = r.read(0x044) & 0xFFFF
    env = [r.read(BANK0 + OFF_ENV + 4 * i) & 0xFFFFFF for i in range(4)]
    gr = [r.read(BANK0 + OFF_GR + 4 * i) & 0xFFFFFF for i in range(4)]
    return peak_in, peak_out, env, gr


def snap(r):
    env = [v & 0xFFFFFF for v in rd4(r, OFF_ENV)]
    gr = [v & 0xFFFFFF for v in rd4(r, OFF_GR)]
    return env, gr


def watch(r, secs, label):
    print("-- %s --" % label)
    t_end = time.time() + secs
    peak_gr = [0, 0, 0, 0]
    nz = 0
    rows = 0
    while time.time() < t_end:
        env, gr = snap(r)
        for i in range(4):
            if gr[i] > peak_gr[i]:
                peak_gr[i] = gr[i]
        if max(gr) > 0:
            nz += 1
        if rows < 6:
            print("   env=%s gr=%s" % (env, gr))
        rows += 1
        time.sleep(0.25)
    print("   peak gr = %s   （GR 非零的快照 %d/%d）" % (peak_gr, nz, rows))
    return peak_gr, nz, rows


def main():
    if not os.path.exists(BIT):
        print("ERROR: no %s" % BIT)
        return 1
    from pynq import Overlay, MMIO

    ol = Overlay(BIT)
    time.sleep(0.2)

    name, base = find_ip(ol, "fir_pl_regs")
    if base is None:
        print("!! no fir_pl_regs -- wrong bitstream")
        return 1
    r = MMIO(base, 0x1000)

    cn, cb = find_ip(ol, "gpio_ctrl")
    if cb is None:
        print("!! no gpio_ctrl")
        return 1
    ctrl = MMIO(cb, 0x1000)

    from dma_audio_probe import codec_init
    print("== codec_init ==")
    codec_init()

    bad = 0
    thr = [v for v in rd4(r, OFF_THR)]
    rat = [v for v in rd4(r, OFF_RATIO)]
    atk = [v for v in rd4(r, OFF_ATK)]
    rel = [v for v in rd4(r, OFF_REL)]
    ok = (thr == [D_THR] * 4 and rat == [D_RATIO] * 4
          and atk == [D_ATK] * 4 and rel == [D_REL] * 4)
    bad += 0 if ok else 1
    print("[1] factory THR=%s RATIO=%s ATK=%s REL=%s  %s"
          % (thr, rat, atk, rel, "OK" if ok else "FAIL"))

    # flush + run，别让 FIFO 带着旧数据进测试
    b0 = CTRL_RUN | CTRL_DRC
    ctrl.write(0, b0 | CTRL_FLUSH)
    time.sleep(0.02)
    ctrl.write(0, b0 | CTRL_CLR)
    time.sleep(0.005)
    ctrl.write(0, b0)
    print("== ctrl=0x%02X ==" % b0)

    # 增益给 0 dB：这一段只测 DRC，不要被增益干扰
    wr4(r, OFF_GAIN, [256] * 4)
    time.sleep(0.1)

    # 先量一下当前电平：话筒通路本身很轻（历史上只有 −75 dBFS），
    # 门限直接写死 1000 会比信号还高，GR 永远不动 —— 那是判据写错，不是 RTL 坏。
    levels = []
    for _ in range(24):
        env, _g = snap(r)
        levels.append(max(env))
        time.sleep(0.08)
    levels.sort()
    level = levels[len(levels) // 2]
    print("   当前四段包络（取中位）= %d  （满量程 = %d）" % (level, 1 << 22))

    watch(r, 2.0, "(a) factory params: THR=419456, expect GR = 0")

    if level < 8:
        print("!! 包络几乎为 0：话筒没信号（耳机没插紧/没在动），后面两条判据不作数")
        wr4(r, OFF_THR, [D_THR] * 4)
        wr4(r, OFF_ATK, [D_ATK] * 4)
        wr4(r, OFF_REL, [D_REL] * 4)
        ctrl.write(0, 0)
        print("")
        print("DRC PROBE %s" % ("PASS(partial)" if bad == 0 else "FAIL (%d)" % bad))
        return 0 if bad == 0 else 1

    thr_test = max(1, level // 3)
    print("   把 THR 压到 %d（信号的 1/3），GR 必须离开 0" % thr_test)
    wr4(r, OFF_THR, [thr_test] * 4)
    time.sleep(0.6)
    peak_a, nz_a, rows_a = watch(r, 3.0, "(b) THR lowered: GR must go > 0")

    env, gr = snap(r)
    ratio_seen = []
    for i in range(4):
        d = env[i] - thr_test
        if d > 0 and gr[i] > 0:
            ratio_seen.append(round(float(gr[i]) / d, 3))
    print("   实测 GR/(ENV-THR) = %s （期望 ≈ 0.7 = RATIO）" % ratio_seen)

    ok = peak_a and max(peak_a) > 0 and nz_a > rows_a // 2
    bad += 0 if ok else 1
    print("[2] THR 生效  %s" % ("OK" if ok else "FAIL（GR 一直是 0，thr/ratio 没进通路）"))
    if ratio_seen:
        m = sum(ratio_seen) / len(ratio_seen)
        ok = 0.4 < m < 1.2
        bad += 0 if ok else 1
        print("[3] RATIO 生效  平均 %.3f  %s" % (m, "OK" if ok else "FAIL（偏离 0.7 太多）"))

    wr4(r, OFF_ATK, [0] * 4)
    wr4(r, OFF_REL, [0] * 4)
    time.sleep(0.5)
    peak_b, nz_b, rows_b = watch(r, 3.0, "(c) ATK=REL=0 (memoryless): GR should still react")
    ok = peak_b and max(peak_b) > 0
    bad += 0 if ok else 1
    print("[4] 无记忆模式仍工作  %s" % ("OK" if ok else "FAIL"))

    # 还原
    wr4(r, OFF_THR, [D_THR] * 4)
    wr4(r, OFF_ATK, [D_ATK] * 4)
    wr4(r, OFF_REL, [D_REL] * 4)
    ctrl.write(0, 0)
    print("[5] restored THR/ATK/REL, ctrl=0")

    print("")
    print("DRC PROBE %s" % ("PASS" if bad == 0 else "FAIL (%d)" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
