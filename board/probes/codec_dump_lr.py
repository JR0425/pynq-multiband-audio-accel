"""只读：把 ADAU1761 的播放相关寄存器导出来，重点看左右两路有没有不对称。

数字侧已经证明左右耳的样本**完全相同**，所以"左耳更响"只可能来自 codec 配置
或模拟链路。名字按数据手册 Rev. F 第 48-50 页的寄存器表。

注意 0x401C / 0x401E 不能直接比数值：左混音器解静音的是 bit5，右混音器是
bit6，数值本来就该不同。它们单列在 ASYM 表里。

只读，不重灌位流、不改任何寄存器。

    echo xilinx | sudo -S python3 codec_dump_lr.py
"""
import sys

sys.path.insert(0, "/home/xilinx/dma_exp")
import dma_audio_probe as P

# 左右成对、位定义相同，数值不同就是嫌疑
PAIRS = [
    (0x401A, 0x401B, "Left/Right digital vol"),
    (0x401D, 0x401F, "Play mixer L1/R1 (bypass gain)"),
    (0x4020, 0x4021, "Play L/R mixer left/right"),
    (0x4023, 0x4024, "Play HP left/right vol"),
    (0x4025, 0x4026, "Line out left/right vol"),
    (0x400A, 0x400C, "Rec mixer left0/right0"),
    (0x400B, 0x400D, "Rec mixer left1/right1"),
    (0x400E, 0x400F, "Diff input vol left/right"),
]

# 左右各有一套、但解静音位在不同 bit 上，要按位解释而不是比数值
ASYM = [
    (0x401C, 0x401E, "Play mixer L0/R0 (bit5 vs bit6)"),
]

SINGLE = [
    (0x4008, "Dig mic / jack detect"),
    (0x4009, "Rec power mgmt"),
    (0x4010, "Record mic bias"),
    (0x4011, "ALC 0"),
    (0x4029, "Playback power mgmt"),
    (0x402A, "DAC control 0"),
    (0x402B, "DAC control 1 (LDAVOL)"),
    (0x402C, "DAC control 2 (RDAVOL)"),
]


def rd(reg):
    try:
        return P.i2c_rd(reg)
    except Exception:
        return None


def fmt(v):
    return "??" if v is None else "0x%02x" % v


print("=== 成对寄存器（左右不一致就是嫌疑）===")
bad = 0
for l, r, name in PAIRS:
    vl, vr = rd(l), rd(r)
    flag = ""
    if vl is not None and vr is not None and vl != vr:
        flag = "   <-- differ"
        bad += 1
    print("  %-32s 0x%04x=%s  0x%04x=%s%s" % (name, l, fmt(vl), r, fmt(vr), flag))

print("\n=== 左右各一套、位定义不同（比数值没意义，看解静音位）===")
for l, r, name in ASYM:
    vl, vr = rd(l), rd(r)
    ok = ""
    if vl is not None and vr is not None:
        # 左混音器吃左 DAC（bit5），右混音器吃右 DAC（bit6）
        ok = "   route_symmetric=%s" % (
            "YES" if ((vl >> 5) & 1) and ((vr >> 6) & 1) else "NO")
    print("  %-32s 0x%04x=%s  0x%04x=%s%s" % (name, l, fmt(vl), r, fmt(vr), ok))

print("\n=== 单寄存器 ===")
for reg, name in SINGLE:
    print("  0x%04x %-26s %s" % (reg, name, fmt(rd(reg))))

print("\n成对寄存器里左右不同的：%d 组" % bad)
