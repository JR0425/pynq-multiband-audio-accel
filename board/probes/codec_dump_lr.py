"""只读：把 ADAU1761 的播放相关寄存器导出来，重点看左右两路有没有不对称。

数字侧已经证明左右耳的样本**完全相同**，所以"左耳更响"只可能来自 codec 配置
或模拟链路。这一组寄存器里凡是成对的（L/R），值不一样就是嫌疑。

只读，不重灌位流、不改任何寄存器。

    echo xilinx | sudo -S python3 codec_dump_lr.py
"""
import sys

sys.path.insert(0, "/home/xilinx/dma_exp")
import dma_audio_probe as P

# 成对的左右寄存器；名字按 ADAU1761 数据手册
PAIRS = [
    (0x401C, 0x401D, "耳机左/右 DAC 音量"),
    (0x401E, 0x401F, "线路输出左/右音量"),
    (0x4023, 0x4024, "播放混音器左/右 0"),
    (0x4026, 0x4027, "播放混音器左/右 1"),
    (0x4005, 0x4006, "录音混音器左/右"),
    (0x400F, 0x400E, "麦克风增益 右/左"),
]

SINGLE = [
    (0x4008, "录音功率"),
    (0x4009, "播放功率"),
    (0x400A, "播放功率? "),
    (0x400B, "播放功率? "),
    (0x4013, "播放激活"),
    (0x4016, "播放采样率"),
    (0x4020, "单声道输出音量"),
    (0x4029, "播放 L/R 混音器"),
    (0x402A, "播放 L/R 混音器 1"),
    (0x402B, "播放 L/R 混音器 2"),
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
        flag = "   <-- 左右不同"
        bad += 1
    print("  %-22s 0x%04x=%s  0x%04x=%s%s" % (name, l, fmt(vl), r, fmt(vr), flag))

print("\n=== 单寄存器 ===")
for reg, name in SINGLE:
    print("  0x%04x %-20s %s" % (reg, name, fmt(rd(reg))))

print("\n左右不同的成对寄存器：%d 组" % bad)
