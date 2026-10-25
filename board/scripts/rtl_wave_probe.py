"""fir_rtl_dma_cap 板上量测：抓 AXI-Stream 的 valid 时序图（PLAN §11.14 §4 拔高档后半）

这一版位流在 DMA 和外壳之间串了两个直通监视器，把 tvalid / tready / tlast /
tdata 引成普通引脚，接到自研抓取器 fir_wave_cap。抓取器一个 100 MHz 周期记一
拍，连续记 1024 拍，分两块 1024×32 的 BRAM 给 PS 读（输入侧一块、输出侧一块）。

抓两次，各出一行图：

    head  trig_sel=0  触发点 = 块头第一个 in_valid&in_ready，能看到 3 拍一个采样的节流
    tail  trig_sel=1  触发点 = 块尾 in_last 那个握手，能看到 TLAST 和输出 FIFO 的收尾

注意群延迟不在拍数上：外壳每块开头复位延迟线，前 96 个输出采样是 0，
之后才是滤波值，进出个数相同、TLAST 几乎对齐。看图时别把"输出怎么这么早就来了"
当成接错 —— 这条探针会直接检查前 96 个输出是否为 0。

**这一版只出图不出数**：串了监视器之后组合路径多一级，报告里的拍数和吞吐一律
引不带抓取器的 fir_rtl_dma.bit。

为什么要自己写抓取器而不用 Vivado 的 ILA：见 src/rtl/fir_wave_cap.v 顶部。

用法（板子上，要 root，位流/hwh/黄金向量放同一目录）：

    echo xilinx | sudo -S env XILINX_XRT=/usr \
      /usr/local/share/pynq-venv/bin/python3 rtl_wave_probe.py

可选：
    --drc        走压缩路径
    --n 4800     块长（默认用黄金向量长度）
    --win 384    图上画多少拍（默认 384）
"""
import argparse
import os
import sys
import time

import numpy as np
from pynq import MMIO, Overlay, allocate

import rtl_dma_probe as dm

BITFILE = "fir_rtl_dma_cap.bit"
HWHFILE = "fir_rtl_dma_cap.hwh"

# 地址见 board/overlay/build_rtl_dma_cap.tcl
GPIO_CTRL = 0x43C10000
GPIO_STAT = 0x43C20000
GPIO_CAP  = 0x43C30000
BRAM_IN   = 0x43C00000
BRAM_OUT  = 0x43C08000

DEPTH = 1024

CTRL_DRC  = 0x1          # bit0：外壳的压缩使能
CTRL_ARM  = 0x2          # bit1：抓取器 arm
CTRL_TRIG_SHIFT = 2      # bit3:2：trig_sel

FCLK_HZ = 100e6

ok_all = True


def check(name, passed, detail=""):
    global ok_all
    ok_all = ok_all and passed
    print("  [%s] %s%s" % ("OK  " if passed else "FAIL", name,
                           ("  " + detail) if detail else ""))
    return passed


def load():
    for f in (BITFILE, HWHFILE):
        if not os.path.exists(f):
            print("ERROR: 找不到 %s（bit 和 hwh 要一起拷过来）" % f)
            sys.exit(1)

    ol = Overlay(BITFILE)
    print("  里面的 IP：", sorted(ol.ip_dict.keys()))

    dma = None
    for k, v in ol.ip_dict.items():
        if "dma" in v["type"].lower():
            dma = getattr(ol, k)
            break
    if dma is None:
        print("ERROR: overlay 里没有 DMA")
        sys.exit(1)
    print("  DMA 单次上限 %d 字节" % dma.buffer_max_size)

    mm = {}
    for base, key in ((GPIO_CTRL, "ctrl"), (GPIO_STAT, "stat"),
                      (GPIO_CAP, "cap")):
        hit = [v for v in ol.ip_dict.values() if v.get("phys_addr") == base]
        if hit:
            k = [k for k, v in ol.ip_dict.items() if v.get("phys_addr") == base][0]
            mm[key] = getattr(ol, k).mmio
        else:
            mm[key] = MMIO(base, 0x1000)
    mm["bram_in"] = MMIO(BRAM_IN, 0x1000)
    mm["bram_out"] = MMIO(BRAM_OUT, 0x1000)
    print("  抓取器缓冲 0x%08X / 0x%08X，状态 0x%08X" % (BRAM_IN, BRAM_OUT, GPIO_CAP))
    return dma, mm


def capture(dma, mm, x, trig_sel, drc=False, timeout=2.0):
    """arm → 跑一块 → 读回 1024 拍。返回 (in_words, out_words)。"""
    ctrl = mm["ctrl"]
    n = len(x)

    inbuf = allocate(shape=(n,), dtype=np.int32)
    outbuf = allocate(shape=(n,), dtype=np.int32)
    inbuf[:] = x.astype(np.int32)
    outbuf[:] = 0
    inbuf.flush()
    outbuf.flush()

    word = (CTRL_DRC if drc else 0) | (trig_sel << CTRL_TRIG_SHIFT)
    # 先拉低再拉高：抓取器只认 arm 的上升沿
    ctrl.write(0, word)
    time.sleep(100e-6)
    ctrl.write(0, word | CTRL_ARM)

    # 和 rtl_dma_probe.run_dma 一样的顺序，但不能用它 —— 它会把 ctrl 整字重写，
    # 把 arm 清掉（外壳的 drc_en 和抓取器的 arm 在同一个 gpio 里）。
    dma.recvchannel.transfer(outbuf)
    dma.sendchannel.transfer(inbuf)
    dm.wait_dma(dma, dm.DMASR_MM2S, "MM2S", 5.0)
    dm.wait_dma(dma, dm.DMASR_S2MM, "S2MM", 5.0)
    t_wall = time.perf_counter()

    ctrl.write(0, word)          # 撤销 arm，下一轮才有新的上升沿

    t0 = time.time()
    while not (mm["cap"].read(0) & 1):
        if time.time() - t0 > timeout:
            raise TimeoutError("抓取器 %.1f s 内没有 done（trig_sel=%d 没等到触发？）"
                               % (timeout, trig_sel))

    t_r0 = time.time()
    win = np.array([mm["bram_in"].read(4 * i) for i in range(DEPTH)],
                   dtype=np.uint32)
    wout = np.array([mm["bram_out"].read(4 * i) for i in range(DEPTH)],
                    dtype=np.uint32)
    print("  trig_sel=%d  done 已置位，两块 %d 字读出用了 %.1f ms"
          % (trig_sel, DEPTH, (time.time() - t_r0) * 1e3))
    del inbuf, outbuf
    return win, wout


def unpack(win, wout):
    """拆成六个 0/1 序列 + 两路数据。位排布见 src/rtl/fir_wave_cap.v。"""
    def bits(w, i):
        return ((w >> i) & 1).astype(np.uint8)
    d = dict(
        in_valid=bits(win, 0), in_ready=bits(win, 1), in_last=bits(win, 2),
        out_valid=bits(wout, 0), out_ready=bits(wout, 1), out_last=bits(wout, 2),
        in_tdata=(win >> 16).astype(np.int16),
        out_tdata=(wout >> 16).astype(np.int16),
    )
    d["in_fire"] = d["in_valid"] & d["in_ready"]
    d["out_fire"] = d["out_valid"] & d["out_ready"]
    return d


def report(d, label):
    fi = np.nonzero(d["in_fire"])[0]
    fo = np.nonzero(d["out_fire"])[0]
    if len(fi) >= 2:
        gaps = np.diff(fi)
        print("  [%s] 窗口内输入握手 %d 次，间隔 %d~%d 拍（中位 %d）"
              % (label, len(fi), gaps.min(), gaps.max(), int(np.median(gaps))))
    else:
        print("  [%s] 窗口内输入握手 %d 次" % (label, len(fi)))
    if len(fo):
        print("  [%s] 窗口内输出握手 %d 次，第一次在第 %d 拍"
              % (label, len(fo), fo[0]))
        if d["out_last"].any():
            print("  [%s] TLAST 在第 %d 拍" % (label, int(np.nonzero(d["out_last"])[0][0])))
    else:
        print("  [%s] 窗口内没有输出握手" % label)


def draw(d, label, win, path_ax, n_cycle, notes=()):
    lanes = [("in_valid", "#1f77b4"), ("in_ready", "#ff7f0e"),
             ("in_last", "#2ca02c"), ("out_valid", "#d62728"),
             ("out_ready", "#9467bd"), ("out_last", "#8c564b")]
    x = np.arange(n_cycle)
    for k, (name, col) in enumerate(lanes):
        y = d[name][:n_cycle].astype(np.float64) * 0.7 + (len(lanes) - 1 - k)
        path_ax.step(x, y, where="post", lw=1.2, color=col)
    path_ax.set_yticks(np.arange(len(lanes)))
    path_ax.set_yticklabels([n for n, _ in lanes][::-1], fontsize=8)
    path_ax.set_ylim(-0.4, len(lanes) - 0.1)   # 顶上留一点，in_valid 常高时不被边框压住
    path_ax.set_xlim(0, n_cycle - 1)
    path_ax.grid(axis="x", alpha=0.2)

    # 少数几处直接写在图上，省得看图的人自己数
    for nx, ny, txt in notes:
        path_ax.text(nx, ny, txt, fontsize=8, va="center",
                     bbox=dict(boxstyle="round,pad=0.2", fc="white",
                               ec="#cccccc", alpha=0.9))

    # 握手点打竖线，方便数拍
    for i in np.nonzero(d["in_fire"][:n_cycle])[0]:
        path_ax.axvline(i, color="#1f77b4", alpha=0.12, lw=0.8)
    path_ax.set_title("%s  (trigger at cycle 0, %d cycles @100 MHz = %.1f us)"
                      % (label, n_cycle, n_cycle / FCLK_HZ * 1e6), fontsize=9)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drc", action="store_true")
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--win", type=int, default=384,
                    help="图上画多少拍（抓的是 1024 拍）")
    args = ap.parse_args()

    print("=== 0. 加载 overlay ===")
    dma, mm = load()

    print("=== 1. 输入 ===")
    gx, ref = dm.read_golden()
    if gx is not None:
        n = args.n or len(gx)
        x = gx if n == len(gx) else dm.synth(n)
        print("  用 in_q15.mem，%d 点" % len(x))
    else:
        n = args.n or 4800
        x = dm.synth(n)
        print("  没带黄金向量，用板内合成信号，%d 点" % n)

    win_cycles = min(args.win, DEPTH)

    print("\n=== 2. 块头（trig_sel=0）===")
    wi, wo = capture(dma, mm, x, 0, drc=args.drc)
    dh = unpack(wi, wo)
    report(dh, "head")

    print("\n=== 3. 块尾（trig_sel=1）===")
    wi2, wo2 = capture(dma, mm, x, 1, drc=args.drc)
    dt = unpack(wi2, wo2)
    report(dt, "tail")

    # 判据：块头必须看到 3 拍一次的输入握手；块尾必须看到 TLAST。
    # 间隔不能要求全等于 3：DMA 起头时 MM2S 偶尔会插一个气泡，那是搬运算子的
    # 行为，不是核的节流。看 3 拍占的比例。
    fi = np.nonzero(dh["in_fire"])[0]
    if len(fi) >= 2:
        gaps = np.diff(fi)
        frac3 = float(np.mean(gaps == 3))
        check("块头输入握手 3 拍/采样", frac3 >= 0.95,
              "3 拍占 %.1f%%（间隔 %s）"
              % (frac3 * 100, dict(zip(*np.unique(gaps, return_counts=True)))))
    else:
        check("块头输入握手 3 拍/采样", False, "窗口里只有 %d 次握手" % len(fi))
    check("块尾抓到 TLAST", bool(dt["in_last"].any()) or bool(dt["out_last"].any()),
          "in_last 第 %s 拍 / out_last 第 %s 拍"
          % (np.nonzero(dt["in_last"])[0][:1], np.nonzero(dt["out_last"])[0][:1]))

    # 群延迟不体现在拍数上，体现在数值上（外壳头部写着：复位后延迟线是 0，
    # 前 96 个输出就是 0，进多少个出多少个）。所以输出几乎和输入同步发射，
    # 块尾 TLAST 也只差十来拍；要看 96 采样群延迟得看数据，不是看 valid。
    if dh["out_fire"].any() and dh["in_fire"].any():
        fo = np.nonzero(dh["out_fire"])[0]
        y = np.asarray(dh["out_tdata"][fo], dtype=np.int32)
        nz = np.nonzero(y)[0]
        lead = int(nz[0]) if len(nz) else -1
        print("  [head] 第一个输出握手在触发后第 %d 拍（流水线延迟）" % fo[0])
        check("前 96 个输出采样为 0（群延迟在数值上）", lead == 96,
              "前 %d 个输出全 0，第 %d 个起非零" % (lead, lead + 1))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 1, figsize=(11, 6.4), sharex=True)
        n_fi = int(np.count_nonzero(dh["in_fire"][:win_cycles]))
        note_head = [(win_cycles * 0.42, 1.5,
                      "in_ready 1 cycle every 3  ->  3 cycles/sample"
                      "   (%d handshakes in %d cycles)" % (n_fi, win_cycles))]
        note_tail = []
        ol = np.nonzero(dt["out_last"][:win_cycles])[0]
        if len(ol):
            note_tail.append((win_cycles * 0.08, 0.45,
                              "out_last at cycle %d -- the 96-sample group delay"
                              " is in the values (first 96 outputs = 0), not in the beats"
                              % int(ol[0])))
        draw(dh, "block head: tvalid/tready throttle", win_cycles, axes[0],
             win_cycles, note_head)
        draw(dt, "block tail: drain + TLAST", win_cycles, axes[1],
             win_cycles, note_tail)
        axes[1].set_xlabel("cycle (@100 MHz, 1 cycle = 10 ns)")
        fig.tight_layout()
        fig.savefig("wave_timing.png", dpi=150)
        plt.close(fig)
        print("\n图已存：wave_timing.png")
    except Exception as e:
        print("\n[skip] 出图失败：%s: %s" % (type(e).__name__, e))

    print("\n=== 结论 ===")
    print("  全过。" if ok_all else "  有 FAIL，看上面。")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
