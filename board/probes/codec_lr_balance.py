"""把 codec 的左右音量寄存器对齐，做一次可逆对照。

背景：数字侧已经证明左右耳拿到的样本逐位相同（PROGRESS §12.16），所以耳朵之间的
任何差别只能来自 codec 或模拟链路。实测这一组是**不对称**的：

    0x401C = 0x21   0x401D = 0x00     耳机左 / 耳机右 音量
    0x401E = 0x41   0x401F = 0x00     线路左 / 线路右 音量
    0x4023 = 0xF3   0x4024 = 0xF3     播放混音器左 / 右   ← 这一组是对的

`codec_unmute()` 只写了左边，右边四个寄存器从没写过，停在复位值 0x00。
PROGRESS §0.0.7 那张表里，这一族寄存器的 0x00 记的是「关」、0x41 是「开」。

这个脚本只做一件事：把左边的值镜像到右边，然后读回来确认。

    sudo python3 codec_lr_balance.py status     # 只看，不改
    sudo python3 codec_lr_balance.py mirror     # 镜像左边到右边
    sudo python3 codec_lr_balance.py restore    # 恢复成 0x00

镜像之后请用户听一下：右耳是不是变响了 / 左右平衡是不是变了 / 啪声有没有变化。
判断完跑 restore 就能退回原样。
"""
import sys

sys.path.insert(0, "/home/xilinx/dma_exp")
import dma_audio_probe as P

PAIRS = [(0x401C, 0x401D), (0x401E, 0x401F)]


def show(tag):
    print("[%s]" % tag)
    for l, r in PAIRS:
        vl, vr = P.i2c_rd(l), P.i2c_rd(r)
        print("   0x%04X = %s   0x%04X = %s"
              % (l, "??" if vl is None else "0x%02X" % vl,
                 r, "??" if vr is None else "0x%02X" % vr))
    sys.stdout.flush()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    show("现在")
    if mode == "status":
        return
    if mode == "mirror":
        for l, r in PAIRS:
            vl = P.i2c_rd(l)
            if vl is None:
                print("   读 0x%04X 失败，跳过" % l)
                continue
            P.i2c_wr(r, vl)
            print("   写 0x%04X <- 0x%02X" % (r, vl))
    elif mode == "restore":
        for _l, r in PAIRS:
            P.i2c_wr(r, 0x00)
            print("   写 0x%04X <- 0x00" % r)
    show("之后")


if __name__ == "__main__":
    main()
