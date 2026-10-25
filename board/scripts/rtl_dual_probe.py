"""两版位流轮跑、输出并排（PLAN §11.14 §4 的拔高档前半）

同一个输入、同一份黄金向量，先后加载两个 overlay：

    fir_rtl.bit      块模式（AXI-Lite + 双口 BRAM，PS 自己搬）
    fir_rtl_dma.bit  DMA 版（axi_dma + AXI-Stream，PS 只写地址和长度）

比三件事：输出逐位是否相同、核内拍数是否同一口径、含搬运的墙钟差多少。
最后出两张图（`dual_outputs.png`、`dual_timings.png`）给报告用。

图上的字用英文：板子的 matplotlib 没有中文字体，写中文会全变成方框。

用法（板子上，要 root，四个文件放同一目录）：
    fir_rtl.bit / fir_rtl.hwh / fir_rtl_dma.bit / fir_rtl_dma.hwh
    in_q15.mem / rtl_fe_out.txt
    rtl_probe.py / rtl_dma_probe.py      （直接复用，量法就是前两轮那份）

    echo xilinx | sudo -S env XILINX_XRT=/usr \
      /usr/local/share/pynq-venv/bin/python3 rtl_dual_probe.py

可选：
    --drc    两个 overlay 都走 ctrl 的压缩位（块模式是 bit17、DMA 版是 bit0，
             脚本各按各的口径写，调用方不用管）
    --n N    块长
"""

import argparse
import os
import sys
import time

import numpy as np

import rtl_probe as blk
import rtl_dma_probe as dma

GD = 96
FCLK_HZ = 100_000_000.0

ok_all = True


def check(name, passed, detail=""):
    global ok_all
    ok_all = ok_all and passed
    print("  [%s] %s%s" % ("OK  " if passed else "FAIL", name,
                           ("  " + detail) if detail else ""))
    return passed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drc", action="store_true", help="走压缩路径")
    ap.add_argument("--n", type=int, default=0)
    args = ap.parse_args()

    for f in ("fir_rtl.bit", "fir_rtl.hwh", "fir_rtl_dma.bit", "fir_rtl_dma.hwh",
              "in_q15.mem", "rtl_fe_out.txt"):
        if not os.path.exists(f):
            print("ERROR: 缺 %s" % f)
            sys.exit(1)

    g = np.array([int(s, 16) for s in open("in_q15.mem") if s.strip()], dtype=np.int32)
    x = (((g + 0x8000) & 0xFFFF) - 0x8000).astype(np.int16)
    ref = np.array([int(s) for s in open("rtl_fe_out.txt") if s.strip()], dtype=np.int32)
    if args.n and args.n != len(x):
        print("ERROR: 黄金向量是 %d 点，和 --n %d 对不上" % (len(x), args.n))
        sys.exit(1)
    n = len(x)
    print("输入 %d 点，黄金向量 %d 点，模式 %s"
          % (n, len(ref), "fe+gain+WDRC" if args.drc else "仅前端"))

    # ---------------- 块模式 ----------------
    print("\n=== 1. 加载 fir_rtl.bit（块模式）===")
    mm = blk.load()
    t0 = time.perf_counter()
    yb, cb, tb = blk.run_block(mm, x, "block", drc=args.drc)
    if yb is None:
        print("ERROR: 块模式没跑出来")
        sys.exit(1)

    # ---------------- DMA 版 ----------------
    print("\n=== 2. 加载 fir_rtl_dma.bit（DMA 版）===")
    d, ctrl, stat = dma.load()
    y_d, cd, td = dma.run_dma(d, ctrl, stat, x, drc=args.drc)

    # ---------------- 比 ----------------
    print("\n=== 3. 并排比 ===")
    diff = yb.astype(np.int64) - y_d.astype(np.int64)
    n_bad = int(np.count_nonzero(diff))
    max_abs = int(np.max(np.abs(diff))) if len(diff) else 0
    check("两版输出逐位相同", n_bad == 0,
          "%d 点，不同 %d 点，最大差 %d LSB" % (n, n_bad, max_abs))
    if ref is not None and len(ref) == n:
        both_ok = (int(np.count_nonzero(yb != ref)) == 0 and
                   int(np.count_nonzero(y_d != ref)) == 0)
        check("两版各自都和 xsim 黄金向量相同", both_ok,
              "block %d 点不同 / dma %d 点不同"
              % (int(np.count_nonzero(yb != ref)),
                 int(np.count_nonzero(y_d != ref))))

    # 核内拍数是同一套计数器口径（都是 3*(n-1)+尾巴，尾巴差几拍是外壳不同）
    print("  [info] 核内拍数：block %d（%.3f 拍/采样）  dma %d（%.3f 拍/采样）"
          % (cb, cb / max(n - 1, 1), cd, cd / max(n - 1, 1)))
    print("  [info] 核内时间：block %.3f ms  dma %.3f ms"
          % (cb / FCLK_HZ * 1e3, cd / FCLK_HZ * 1e3))

    t_block = sum(tb)
    print("  [info] 墙钟：block %.2f ms（写 %.2f + 跑 %.2f + 读 %.2f）"
          % (t_block * 1e3, tb[0] * 1e3, tb[1] * 1e3, tb[2] * 1e3))
    print("  [info] 墙钟：dma   %.2f ms" % (td * 1e3))
    print("  [info] 折 48 kHz：block %.0f 倍实时 / dma %.0f 倍实时"
          % (n / t_block / 48000.0, n / td / 48000.0))

    # ---------------- 出图 ----------------
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        m = min(n, 1200)
        idx = np.arange(m)

        fig, axes = plt.subplots(3, 1, figsize=(10, 7.2), sharex=True)
        axes[0].plot(idx, yb[:m], lw=0.9, color="#1f77b4")
        axes[0].set_ylabel("block mode")
        axes[0].set_title("Same input, two bitstreams, outputs side by side "
                          "(first %d of %d samples)" % (m, n), fontsize=10)
        axes[1].plot(idx, y_d[:m], lw=0.9, color="#d62728")
        axes[1].set_ylabel("DMA mode")
        axes[2].plot(idx, diff[:m], lw=0.9, color="#2ca02c")
        axes[2].set_ylabel("block - DMA")
        axes[2].set_xlabel("sample index")
        axes[2].set_ylim(-1.5, 1.5)
        axes[2].text(0.01, 0.5, "max |diff| = %d LSB" % max_abs,
                     transform=axes[2].transAxes, fontsize=9, color="#2ca02c")
        for a in axes:
            a.grid(alpha=0.25)
        fig.tight_layout()
        fig.savefig("dual_outputs.png", dpi=150)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(6.4, 3.6))
        labels = ["kernel\n(%d cycles)" % cb, "wall clock\n(block)",
                  "kernel\n(%d cycles)" % cd, "wall clock\n(DMA)"]
        vals = [cb / FCLK_HZ * 1e3, t_block * 1e3, cd / FCLK_HZ * 1e3, td * 1e3]
        cols = ["#9ecae1", "#1f77b4", "#fcae91", "#d62728"]
        bars = ax.bar(labels, vals, color=cols)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, "%.3f" % v,
                    ha="center", va="bottom", fontsize=8)
        ax.set_ylabel("time for %d samples (ms)" % n)
        ax.set_title("Kernel time vs. end-to-end wall clock", fontsize=10)
        ax.grid(axis="y", alpha=0.25)
        fig.tight_layout()
        fig.savefig("dual_timings.png", dpi=150)
        plt.close(fig)
        print("\n图已存：dual_outputs.png / dual_timings.png")
    except Exception as e:
        print("\n[skip] 出图失败：%s: %s" % (type(e).__name__, e))

    print("\n=== 结论 ===")
    print("  全过。" if ok_all else "  有 FAIL，看上面。")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
