"""codec 左右声道到底对不对称 —— 只读诊断 + 一次可逆对照。

数字侧已经证明左右耳拿到的样本逐位相同（PROGRESS §12.16），所以耳朵之间的
任何差别只能来自 codec 或模拟链路。

寄存器含义按 ADAU1761 数据手册 Rev. F（Table 78–83，p64–66；Rev. E 同编号在 p69–70）：

    0x401C  R22  Playback Mixer Left  (Mixer 3) Control 0   Table 78/79
                bit7 Reserved，bit6 MX3RM，bit5 MX3LM，bit4:1 MX3AUXG，bit0 MX3EN
    0x401D  R23  Playback Mixer Left  (Mixer 3) Control 1   Table 80/81
                bit7:4 MX3G2 / bit3:0 MX3G1（**旁路**增益，0x00 = Mute 默认，0x06 = 0 dB）
    0x401E  R24  Playback Mixer Right (Mixer 4) Control 0   Table 82/83
                位布局与 R22 完全相同：bit6 MX4RM，bit5 MX4LM，bit0 MX4EN
    0x401F  R25  Playback Mixer Right (Mixer 4) Control 1   （MX4G2 / MX4G1）
    0x4023  R29  Playback HP Left  volume  （bit5:0 音量码，bit1 参与静音，见下）
    0x4024  R30  Playback HP Right volume  （位布局与 R29 相同）
                —— 这两条的位名本次没有逐位核对，只用 2026-10-28 实测差分的
                   0xF3（0xE5 不响、0xF3 响）；T2 只负责 0x401C–0x401F。

所以 (0x401C, 0x401D) 不是"左/右一对"，是**同一个左混音器**的控制寄存器和
旁路增益寄存器。真正的左右一对是 (0x401C, 0x401E) 混音器和 (0x4023, 0x4024)
耳机音量。早期版本把 0x401C 的值镜像进 0x401D，等于把控制位写进了旁路增益位
—— 那是把录音通路直通到播放混音器（sidetone），会啸叫，而且测的东西完全变了。

另外一个容易读错的地方：0x401C 和 0x401E 的数值本来就该不同（都是 bit6 = 右
DAC、bit5 = 左 DAC 进混音器，所以左路取左 DAC = bit5、右路取右 DAC = bit6），
直接比数值会把"对称"误判成"不对称"。

还有第三点：0x401D / 0x401F 读回 **0x00 是正常的**（Table 81/83，旁路增益默认
Mute）。10-30 深夜把这两个 0x00 当成"右声道从没配过"的证据是误读 ——
右声道的通路在 0x401E，不在 0x401F。这两个寄存器只要不是 0，反而是故障
（话筒信号会串进耳机）。

    sudo python3 codec_lr_balance.py status     # 只读，给出对称性结论
    sudo python3 codec_lr_balance.py mirror     # 把 0x4023 镜像到 0x4024
    sudo python3 codec_lr_balance.py restore    # 写回脚本开头读到的原值
"""
import sys

sys.path.insert(0, "/home/xilinx/dma_exp")
import dma_audio_probe as P

HP_L, HP_R = 0x4023, 0x4024
MIX_L, MIX_R = 0x401C, 0x401E
BYP_L, BYP_R = 0x401D, 0x401F


def rd(reg):
    v = P.i2c_rd(reg)
    return None if v is None else v & 0xFF


def wr(reg, val):
    P.i2c_wr(reg, val)


def fmt(v):
    return "??" if v is None else "0x%02X" % v


def decode_mix(v):
    """把混音器控制寄存器拆成 (EN, AUXG 码, 左 DAC 解静音, 右 DAC 解静音)。"""
    if v is None:
        return None
    return (v & 0x01, (v >> 1) & 0x0F, (v >> 5) & 0x01, (v >> 6) & 0x01)


def show(tag):
    print("[%s]" % tag)
    vl, vr = rd(MIX_L), rd(MIX_R)
    bl, br = rd(BYP_L), rd(BYP_R)
    hl, hr = rd(HP_L), rd(HP_R)
    print("  MIX  L 0x%04X=%s   R 0x%04X=%s" % (MIX_L, fmt(vl), MIX_R, fmt(vr)))
    print("  BYP  L 0x%04X=%s   R 0x%04X=%s" % (BYP_L, fmt(bl), BYP_R, fmt(br)))
    print("  HP   L 0x%04X=%s   R 0x%04X=%s" % (HP_L, fmt(hl), HP_R, fmt(hr)))

    dl, dr = decode_mix(vl), decode_mix(vr)
    if dl and dr:
        print("  L: EN=%d AUXG=%X dacL=%d dacR=%d" % dl)
        print("  R: EN=%d AUXG=%X dacL=%d dacR=%d" % dr)
        # 左混音器取左 DAC（bit5），右混音器取右 DAC（bit6），这就是对称
        ok = (dl[0] == dr[0] and dl[1] == dr[1]
              and dl[2] == 1 and dl[3] == 0
              and dr[2] == 0 and dr[3] == 1)
        print("  symmetric by route: %s" % ("YES" if ok else "NO"))
    if bl is not None and br is not None:
        print("  bypass gains equal: %s (0x00 = mute, both default)" %
              ("YES" if bl == br else "NO"))
    sys.stdout.flush()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    orig = {r: rd(r) for r in (HP_L, HP_R)}
    show("now")
    if mode == "status":
        return
    if mode == "mirror":
        if orig[HP_L] is None:
            print("  read 0x%04X failed" % HP_L)
            return
        wr(HP_R, orig[HP_L])
        print("  write 0x%04X <- 0x%02X" % (HP_R, orig[HP_L]))
    elif mode == "restore":
        for r, v in orig.items():
            if v is not None:
                wr(r, v)
                print("  write 0x%04X <- 0x%02X" % (r, v))
    else:
        print("  unknown mode %s (status | mirror | restore)" % mode)
        return
    show("after")


if __name__ == "__main__":
    main()
