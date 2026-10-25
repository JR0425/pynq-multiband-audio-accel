"""把同一份 in_q15.mem 过一遍核，结果存下来 —— 用来对拍两块位流

DMA 版的判据之一是"输出和现有通路逐位相同"。两块位流里放的是同一个 v25 核、
用同一套寄存器，所以理论上一定相同；但"理论上"不是证据，这个脚本就是证据：
同一份输入、同一个驱动、两次分别加载 fir.bit 和 fir_dma_core.bit，逐字节比。

分两次进程跑，不在一块里连加两块位流 —— PYNQ 的 Overlay 下载要求 FPGA manager
空闲，前一块的 UIO 还开着的时候灌新位流会卡住。

用法（板上，要 root，两块 bit/hwh 和本文件放同一个目录）：

    echo xilinx | sudo -S env XILINX_XRT=/usr \
        /usr/local/share/pynq-venv/bin/python3 core_dump.py \
        --bit fir.bit --mem in_q15.mem --out /tmp/core_fir.npy
    ... --bit fir_dma_core.bit --out /tmp/core_dma.npy
    ... --compare /tmp/core_fir.npy /tmp/core_dma.npy
"""

import argparse

import numpy as np

from pynq import Overlay


def load_mem(path):
    """in_q15.mem：一行一个 16 位十六进制数。按补码解释成 int16。"""
    vals = [int(t, 16) for t in open(path).read().split()]
    return np.array(vals, dtype=np.uint16).view(np.int16)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default="fir.bit")
    ap.add_argument("--mem", default="in_q15.mem")
    ap.add_argument("--out")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    args = ap.parse_args()

    if args.compare:
        a = np.load(args.compare[0])
        b = np.load(args.compare[1])
        if a.shape != b.shape:
            print("FAIL 长度不同：%s %s" % (a.shape, b.shape))
            return 1
        bad = np.count_nonzero(a != b)
        print("%s vs %s：%d 个采样，错配 %d 个"
              % (args.compare[0], args.compare[1], a.size, bad))
        if bad:
            idx = np.flatnonzero(a != b)[:10]
            print("  前 10 处：" + "、".join(
                "%d: %d vs %d" % (i, a[i], b[i]) for i in idx))
            return 1
        print("逐位相同")
        return 0

    if not args.out:
        print("要么给 --out，要么给 --compare")
        return 1

    from fir_core import FirMultiband

    x = load_mem(args.mem)
    print("输入 %d 个采样，峰值 %d" % (x.size, int(np.abs(x.astype(np.int32)).max())))

    ol = Overlay(args.bit)
    if "fir_multiband_0" not in ol.ip_dict:
        print("ERROR: %s 里没有 fir_multiband_0" % args.bit)
        return 1
    print("核 @ %s" % hex(ol.ip_dict["fir_multiband_0"]["phys_addr"]))
    k = FirMultiband(ol.fir_multiband_0.mmio)

    # 用和验配网页一样的默认参数（阈值 0.1、压缩比 0.7、四段单位增益），
    # 这样两块位流比的是同一条路径
    y, dt, blocks = k.process(x, reset=True, bypass=False)
    np.save(args.out, y)
    print("跑完 %.4f 秒（%d 块），结果存到 %s" % (dt, len(blocks), args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
