"""pl_mpo_tone_probe —— 不需要耳朵的 B6（MPO 限幅）+ B4（测试音）验收。

判据全部落在寄存器上，靠"测试音驱动 MPO 计数器"这条链取证：

  [1] TONE_SEL / TONE_AMP 写 -> 读回
  [2] 不动作：THR = 满量程时 MPO_PEAK 等于测试音幅度（证明测试音真的进了输出）
  [3] 限幅起作用：THR 压到 1000 后 MPO_PEAK = th + (amp - th)/4（膝部 1/4 斜率）
  [4] 幅度寄存器真的在控制：amp 翻倍，MPO_PEAK 跟着变
  [5] MPO_CNT 按帧率增长（≈48.8 k/s），关掉测试音后停住
  [6] tone_sel 0..3 各跑一遍，不报错、幅度不变（频率差异要耳朵或 PC 麦克风判）

板上 print 只能 ASCII。
"""
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")

BIT = "/home/xilinx/dma_exp/fir_pl.bit"

BANK0 = 0x100
OFF_MPO_TH = 0x48
OFF_MPO_REL = 0x4C
OFF_MPO_CNT = 0x50
OFF_MPO_PEAK = 0x54
OFF_TONE_SEL = 0x58
OFF_TONE_AMP = 0x5C
OFF_GAIN = 0x00

CTRL_RUN = 1 << 0
CTRL_DRC = 1 << 1
CTRL_FLUSH = 1 << 2
CTRL_TONE = 1 << 3

FS = 48828.0
GAIN_0DB = 256


def find_ip(ol, prefix):
    for name, d in ol.ip_dict.items():
        if name.startswith(prefix) and d.get("phys_addr") is not None:
            return name, d["phys_addr"]
    return None, None


CTRL_MMIO = None


def flush():
    """打一拍 CTRL_FLUSH（自清位），把 MPO_CNT / MPO_PEAK 清零。"""
    CTRL_MMIO.write(0, CTRL_RUN | CTRL_DRC | CTRL_FLUSH)
    time.sleep(0.05)


def sc(v):
    """ctrl 走 gpio_ctrl（0x43C00000），不在 fir_pl_regs 窗口里。"""
    CTRL_MMIO.write(0, v)


def main():
    global CTRL_MMIO
    from pynq import Overlay, MMIO

    ol = Overlay(BIT)
    time.sleep(0.3)

    name, base = find_ip(ol, "fir_pl_regs")
    if base is None:
        print("!! no fir_pl_regs in this bitstream -- rebuild first")
        return 1
    cname, cbase = find_ip(ol, "gpio_ctrl")
    if cbase is None:
        print("!! no gpio_ctrl in this bitstream")
        return 1
    print("[0] %s @ 0x%08X   %s @ 0x%08X" % (name, base, cname, cbase))
    r = MMIO(base, 0x1000)
    CTRL_MMIO = MMIO(cbase, 0x1000)
    bad = 0

    def chk(tag, cond, detail=""):
        nonlocal bad
        if not cond:
            bad += 1
        print("    %-46s %s %s" % (tag, "OK" if cond else "FAIL", detail))

    # 前端要有采样节拍，MPO 才有更新；增益归 0 免得多余放大
    sc(CTRL_RUN | CTRL_DRC)
    for i in range(4):
        r.write(BANK0 + OFF_GAIN + 4 * i, GAIN_0DB)
    time.sleep(0.2)

    print("[1] TONE / MPO 寄存器写读")
    dflt_th = r.read(OFF_MPO_TH) & 0xFFFF
    dflt_rel = r.read(OFF_MPO_REL)
    dflt_sel = r.read(OFF_TONE_SEL) & 0x3
    dflt_amp = r.read(OFF_TONE_AMP) & 0xFFFF
    chk("出厂 MPO_TH=32767 TONE_SEL=0 TONE_AMP=0",
        dflt_th == 32767 and dflt_sel == 0 and dflt_amp == 0,
        "th=%d sel=%d amp=%d rel=%d" % (dflt_th, dflt_sel, dflt_amp, dflt_rel))
    ok_sel = True
    for s in range(4):
        r.write(OFF_TONE_SEL, s)
        ok_sel = ok_sel and ((r.read(OFF_TONE_SEL) & 0x3) == s)
    r.write(OFF_TONE_AMP, 12345)
    ok_amp = (r.read(OFF_TONE_AMP) & 0xFFFF) == 12345
    r.write(OFF_TONE_SEL, 0)
    chk("TONE_SEL 0..3 回读 / TONE_AMP 回读", ok_sel and ok_amp)

    print("[2] 测试音进输出：THR=满量程时不限幅，PEAK 应等于幅度")
    AMP1 = 8000
    r.write(OFF_MPO_TH, 32767)
    r.write(OFF_TONE_SEL, 0)
    r.write(OFF_TONE_AMP, AMP1)
    sc(CTRL_RUN | CTRL_DRC | CTRL_FLUSH)
    time.sleep(0.02)
    sc(CTRL_RUN | CTRL_DRC | CTRL_TONE)
    time.sleep(0.3)
    peak1 = r.read(OFF_MPO_PEAK) & 0xFFFF
    chk("PEAK == TONE_AMP(%d)" % AMP1, abs(peak1 - AMP1) <= 2, "peak=%d" % peak1)

    print("[3] 限幅膝部：THR=1000 时 PEAK = 1000 + (amp-1000)/4")
    TH = 1000
    r.write(OFF_MPO_TH, TH)
    flush()
    sc(CTRL_RUN | CTRL_DRC | CTRL_TONE)
    time.sleep(0.3)
    peak2 = r.read(OFF_MPO_PEAK) & 0xFFFF
    exp2 = TH + (AMP1 - TH) // 4
    chk("PEAK == %d" % exp2, abs(peak2 - exp2) <= 2, "peak=%d" % peak2)

    print("[4] 幅度寄存器真的在控制输出：amp 翻倍，PEAK 跟着变")
    AMP2 = 16000
    r.write(OFF_TONE_AMP, AMP2)
    flush()
    sc(CTRL_RUN | CTRL_DRC | CTRL_TONE)
    time.sleep(0.3)
    peak3 = r.read(OFF_MPO_PEAK) & 0xFFFF
    exp3 = TH + (AMP2 - TH) // 4
    chk("PEAK == %d" % exp3, abs(peak3 - exp3) <= 2,
        "peak=%d (amp %d -> %d)" % (peak3, AMP1, AMP2))

    print("[5] MPO_CNT 按帧率增长，关掉测试音后停住")
    r.write(OFF_MPO_TH, TH)
    flush()
    sc(CTRL_RUN | CTRL_DRC | CTRL_TONE)
    time.sleep(0.1)
    c0 = r.read(OFF_MPO_CNT)
    time.sleep(0.5)
    c1 = r.read(OFF_MPO_CNT)
    rate = (c1 - c0) / 0.5
    chk("被限幅采样率 ≈ %.0f /s" % FS, abs(rate - FS) < FS * 0.05, "实测 %.0f /s" % rate)

    r.write(OFF_MPO_TH, 32767)
    flush()
    sc(CTRL_RUN | CTRL_DRC)     # 关测试音，回到话筒
    time.sleep(0.3)
    c2 = r.read(OFF_MPO_CNT)
    c3 = r.read(OFF_MPO_CNT)
    time.sleep(0.4)
    c4 = r.read(OFF_MPO_CNT)
    chk("关音后 CNT 停止增长", c4 == c3, "cnt=%d -> %d" % (c3, c4))

    print("[6] tone_sel 0..3 各跑一遍（频率差异要耳朵 / PC 麦克风判）")
    r.write(OFF_MPO_TH, TH)
    peaks = []
    for s in range(4):
        r.write(OFF_TONE_SEL, s)
        flush()
        sc(CTRL_RUN | CTRL_DRC | CTRL_TONE)
        time.sleep(0.25)
        peaks.append(r.read(OFF_MPO_PEAK) & 0xFFFF)
    chk("四段幅度一致（方波换频率不改幅度）",
        max(peaks) - min(peaks) <= 2, "peaks=%s" % peaks)

    # 收尾：关测试音、恢复出厂
    sc(CTRL_RUN | CTRL_DRC)
    r.write(OFF_MPO_TH, 32767)
    r.write(OFF_TONE_AMP, 0)
    r.write(OFF_TONE_SEL, 0)
    for i in range(4):
        r.write(BANK0 + OFF_GAIN + 4 * i, GAIN_0DB)
    time.sleep(0.05)
    print("[7] restored: TONE off, MPO_TH 32767, GAIN 0 dB")

    print("\n%s" % ("MPO TONE PROBE PASS" if bad == 0 else "MPO TONE PROBE FAIL (%d)" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
