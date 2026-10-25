"""fir_rtl_dma overlay 的板上量测脚本（DMA 版，PLAN §11.14 §4）

和 rtl_probe.py（块模式）量的是同一件事，区别只在数据怎么搬：

    块模式：PS 自己把采样写进 BRAM、启动、轮询 done、再从 BRAM 读回来
    DMA 版：PS 只写一次地址和长度，axi_dma 自己从 DDR 读、往 DDR 写

所以这里不再有 BRAM，也不再有 start 位。一次传输 = 两个 transfer() + 两次 wait()。

量四件事：
    · 和 xsim 黄金向量逐位比（bypass / DRC 各一份，目录里放哪份就比哪份）
    · 核内拍数 total_cycles（应与块模式同口径，3 拍/采样左右）
    · 单次传输的墙钟时间，以及扣掉核内时间后剩下的搬运/驱动开销
    · 连发 K 次不间停的持续吞吐（判据：能不能跟上 48 kHz 实时）

用法（板子上，要 root）：

    echo xilinx | sudo -S env XILINX_XRT=/usr \
      /usr/local/share/pynq-venv/bin/python3 rtl_dma_probe.py

    # DRC 模式：把 sim/rtl_sim/golden_n4800_drc_loud/ 的 in_q15.mem +
    # rtl_fe_out.txt 和 fir_rtl_dma.bit/.hwh 放同一目录
    echo xilinx | sudo -S env XILINX_XRT=/usr \
      /usr/local/share/pynq-venv/bin/python3 rtl_dma_probe.py --drc

可选参数：
    --n 4800         改块长（默认用黄金向量的长度）
    --drc            走压缩路径（ctrl[0]=1），黄金向量必须是 DRC 那份
    --once           不做重跑一致性检查
    --stream 10      持续吞吐：连发 10 次 65535 点
    --dump out.npz   把最后一次的输入输出存下来

一个必须交代的前提：外壳的 n_in / n_out 是 16 位，所以单块最多 65535 点。
再长要把计数加宽，不是改 Python 能解决的。
"""

import argparse
import os
import sys
import time

import numpy as np
from pynq import MMIO, Overlay, allocate

BITFILE = "fir_rtl_dma.bit"
HWHFILE = "fir_rtl_dma.hwh"

# 地址见 board/overlay/build_rtl_dma.tcl 的 assign_bd_address
GPIO_CTRL = 0x43C10000
GPIO_STAT = 0x43C20000

REG_CTRL    = 0x00       # bit[0] = drc_en（本块第一个采样被接收时锁存）
REG_STAT_LO = 0x00       # total_cycles
REG_STAT_HI = 0x08       # [15:0]=n_out [16]=done [17]=busy [18]=overflow

DMASR_MM2S = 0x04        # MM2S_DMASR
DMASR_S2MM = 0x34        # S2MM_DMASR
SR_IDLE    = 0x0002
SR_ERR     = 0x0070      # DMAIntErr | DMASlvErr | DMADecErr

FCLK_HZ = 100_000_000.0  # PS7 FCLK_CLK0
GD      = 96             # fir_mb_fe 的群延迟（采样）
MAXN    = 65535          # 外壳里 n_in/n_out 是 16 位

ok_all = True


def check(name, passed, detail=""):
    global ok_all
    ok_all = ok_all and passed
    print("  [%s] %s%s" % ("OK  " if passed else "FAIL", name,
                           ("  " + detail) if detail else ""))
    return passed


# ---------------------------------------------------------------- overlay

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
            print("  DMA  %-10s %s" % (k, v["type"]))
            break
    if dma is None:
        print("ERROR: overlay 里没有 DMA —— hwh 是不是和 bit 不是同一版？")
        sys.exit(1)

    # 单次传输上限由位流里 axi_dma 的 c_sg_length_width 决定（26 位 → 64 MB）。
    # **不要**在这里抬 driver 的 _max_size：这个参数是 IP 的真实位宽，
    # 抬高之后 IP 不报错，只把长度截到低 14 位（19200 & 0x3FFF = 2816 字节），
    # 板上表现为"只传了 704 个采样、后面的输出还是哨兵"。上限不够就改 tcl 重编。
    print("  DMA 单次上限 %d 字节（%.0f 个 32 位采样）"
          % (dma.buffer_max_size, dma.buffer_max_size // 4))

    # 两个 gpio 按地址找；找不到就直接 /dev/mem 映射（地址是 tcl 写死的）
    mm = {}
    for base, key in ((GPIO_CTRL, "ctrl"), (GPIO_STAT, "stat")):
        hit = [(k, v) for k, v in ol.ip_dict.items() if v.get("phys_addr") == base]
        if hit:
            mm[key] = getattr(ol, hit[0][0]).mmio
            print("  %-4s 0x%08X  <- overlay（%s）" % (key, base, hit[0][1]["type"]))
        else:
            mm[key] = MMIO(base, 0x1000)
            print("  %-4s 0x%08X  <- 直接映射（overlay 里没找到）" % (key, base))
    return dma, mm["ctrl"], mm["stat"]


# ---------------------------------------------------------------- 一次 DMA 传输

def wait_dma(dma, off, label, timeout):
    """等 DMA 空闲。用寄存器轮询而不是 ch.wait()：后者没有超时，
    板子上第一次跑要是 S2MM 收不到 TLAST，会一直卡在那里不吭声。"""
    t0 = time.perf_counter()
    # 刚写完长度寄存器时 DMASR 里可能还是上一次的 Idle，给几十微秒让它真忙起来。
    # 这一段是纯软件等待，会原样算进墙钟：早先用 1 ms，量 4800 点时才 1.7 ms 的
    # 传输被它灌了 1 ms 水分。状态位是同一个时钟域的，50 us 已经非常宽。
    while (dma.mmio.read(off) & SR_IDLE) and (time.perf_counter() - t0 < 50e-6):
        pass
    while True:
        sr = dma.mmio.read(off)
        if sr & SR_ERR:
            raise RuntimeError("%s 报了 AXI 错误，DMASR=0x%08X" % (label, sr))
        if sr & SR_IDLE:
            return sr
        if time.perf_counter() - t0 > timeout:
            raise TimeoutError("%s 等 %.1f s 还没空闲，DMASR=0x%08X"
                               % (label, timeout, sr))


def run_dma(dma, ctrl, stat, x, drc=False, timeout=5.0,
            inbuf=None, outbuf=None, verify=True):
    """把 x 从 DDR 送进 PL，再收回结果。返回 (y, total_cycles, 墙钟秒)。

    verify=False 只查状态寄存器（O(1)），跳过逐点比对。测持续吞吐时必须关掉：
    Cortex-A9 上 np.count_nonzero(y32 >> 16 != y32 >> 31) 这种整块运算要 5~6 ms，
    比一次 65535 点的传输（3.5 ms）还长，开着量出来的就不是数据通路的速度。
    """
    n = len(x)
    if inbuf is None:
        inbuf = allocate(shape=(n,), dtype=np.int32)
    if outbuf is None:
        outbuf = allocate(shape=(n,), dtype=np.int32)

    inbuf[:] = x.astype(np.int32)           # 符号扩展到 32 位，外壳取低 16 位
    outbuf[:] = 0x5A5A5A5A                  # 哨兵：S2MM 漏写的地方能看出来
    inbuf.flush()
    outbuf.flush()

    ctrl.write(REG_CTRL, 1 if drc else 0)

    t0 = time.perf_counter()
    # 顺序要紧：S2MM 先挂上，再启动 MM2S，否则前几拍输出没人接
    dma.recvchannel.transfer(outbuf)
    dma.sendchannel.transfer(inbuf)
    wait_dma(dma, DMASR_MM2S, "MM2S", timeout)
    wait_dma(dma, DMASR_S2MM, "S2MM", timeout)
    t_wall = time.perf_counter() - t0
    outbuf.invalidate()

    hi     = stat.read(REG_STAT_HI)
    cycles = stat.read(REG_STAT_LO)

    done  = bool(hi & (1 << 16))
    busy  = bool(hi & (1 << 17))
    ovf   = bool(hi & (1 << 18))
    n_out = hi & 0xFFFF

    if not done:
        check("跑完", False, "done 没置位（stat_hi=0x%08X busy=%d）" % (hi, busy))
    if ovf:
        check("无 overflow", False, "输出 FIFO 溢出，stat_hi=0x%08X" % hi)
    if n_out != n:
        check("n_out == n", False, "%d vs %d" % (n_out, n))

    if not verify:
        return None, cycles, t_wall

    y32 = np.array(outbuf, dtype=np.int32)
    # 外壳把 16 位结果符号扩展到 32 位，所以高 16 位应当和低 16 位的符号一致
    n_ext = int(np.count_nonzero((y32 >> 16) != (y32 >> 31)))
    if n_ext:
        check("32 位符号扩展", False, "%d/%d 个点高 16 位不对" % (n_ext, n))
    y = (y32 & 0xFFFF).astype(np.uint16).view(np.int16).astype(np.int32)
    return y, cycles, t_wall


# ---------------------------------------------------------------- 判据

def compare_delay(x, y, label):
    """bypass 模式下输出 = 输入延后 GD 个采样（外壳自身性质，接错必挂）。"""
    k = len(y) - GD
    if k <= 0:
        check("%s 延迟比对" % label, False, "太短")
        return
    bad = int(np.count_nonzero(y[GD:] != x[:k]))
    if bad:
        idx = np.nonzero(y[GD:] != x[:k])[0][:5]
        detail = "，前几个：" + " ".join(
            "[%d] 出=%d 期=%d" % (int(i) + GD, int(y[i + GD]), int(x[i])) for i in idx)
        check("%s 输出 == 输入延后 %d" % (label, GD), False,
              "%d/%d 点不同%s" % (bad, k, detail))
    else:
        check("%s 输出 == 输入延后 %d" % (label, GD), True, "%d 点全同" % k)


def read_golden():
    if not (os.path.exists("in_q15.mem") and os.path.exists("rtl_fe_out.txt")):
        return None, None
    g = np.array([int(s, 16) for s in open("in_q15.mem") if s.strip()], dtype=np.int32)
    g = ((g + 0x8000) & 0xFFFF) - 0x8000          # 16 位补码
    ref = np.array([int(s) for s in open("rtl_fe_out.txt") if s.strip()], dtype=np.int32)
    return g.astype(np.int16), ref


def compare_golden(y, ref, label):
    n = min(len(y), len(ref))
    bad = int(np.count_nonzero(y[:n] != ref[:n]))
    if bad:
        idx = np.nonzero(y[:n] != ref[:n])[0][:5]
        detail = "，前几个：" + " ".join(
            "[%d] 出=%d 期=%d" % (int(i), int(y[i]), int(ref[i])) for i in idx)
        check("%s 和 xsim 黄金向量逐位相同" % label, False,
              "%d 点不同%s" % (bad, detail))
    else:
        check("%s 和 xsim 黄金向量逐位相同" % label, True, "%d 点全同" % n)


# ---------------------------------------------------------------- 主流程

def synth(n):
    t = np.arange(n, dtype=np.float64) / 48000.0
    sig = (0.35 * np.sin(2 * np.pi * 300 * t)
           + 0.15 * np.sin(2 * np.pi * 3000 * t))
    rng = np.random.default_rng(1)
    sig = sig + 0.01 * rng.standard_normal(n)
    return np.clip(np.round(sig * 32767.0), -32768, 32767).astype(np.int16)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="块长（默认用黄金向量长度）")
    ap.add_argument("--drc", action="store_true",
                    help="走四段增益 + WDRC（ctrl[0]=1），黄金向量必须是 DRC 那份")
    ap.add_argument("--once", action="store_true", help="不做重跑一致性检查")
    ap.add_argument("--stream", type=int, default=0,
                    help="持续吞吐：连发这么多次 65535 点")
    ap.add_argument("--dump", default="", help="把最后一次的输入/输出存成 .npz")
    args = ap.parse_args()

    print("=== 0. 加载 overlay ===")
    dma, ctrl, stat = load()

    print("=== 1. 输入 ===")
    gx, ref = read_golden()
    if gx is not None:
        print("  用 in_q15.mem，%d 点；黄金向量 %d 点" % (len(gx), len(ref)))
        n = args.n or len(gx)
        x = gx if n == len(gx) else synth(n)
    else:
        print("  没带黄金向量，用板内合成信号（双音 + 噪声，峰值 0.5 FS）")
        n = args.n or 4800
        x = synth(n)

    if n > MAXN:
        print("ERROR: 外壳的计数是 16 位，单块最多 %d 点" % MAXN)
        sys.exit(1)

    print("\n=== 2. n=%d  模式 %s ===" % (n, "fe+gain+WDRC" if args.drc else "仅前端"))
    t_start = time.perf_counter()
    y1, c1, t1 = run_dma(dma, ctrl, stat, x, drc=args.drc)
    print("  [info] n=%d  cycles=%d  => %.3f 拍/采样"
          % (n, c1, c1 / max(n - 1, 1)))
    print("  [info] 墙钟 %.2f ms，其中核内 %.2f ms，搬运+驱动 %.2f ms"
          % (t1 * 1e3, c1 / FCLK_HZ * 1e3,
             (t1 - c1 / FCLK_HZ) * 1e3))
    print("  [info] 单次吞吐 %.2f Msamp/s（%.0f 倍实时 @48k）"
          % (n / t1 / 1e6, (n / t1) / 48000.0))

    if args.drc:
        if ref is None:
            print("  [skip] DRC 模式必须有 xsim 的黄金向量（golden_n4800_drc_loud/）")
        else:
            compare_golden(y1, ref, "run1")
    else:
        compare_delay(x, y1, "run1")
        if ref is not None and n == len(gx):
            compare_golden(y1, ref, "run1")

    cps = c1 / max(n - 1, 1)
    check("每采样拍数 ≈ 3", 2.9 <= cps <= 3.2, "%.3f" % cps)

    if not args.once:
        print("\n=== 3. 重跑一次 ===")
        y2, c2, _ = run_dma(dma, ctrl, stat, x, drc=args.drc)
        check("两次输出逐位相同", int(np.count_nonzero(y1 != y2)) == 0,
              "%d 点不同" % int(np.count_nonzero(y1 != y2)))
        check("两次拍数相同", c1 == c2, "%d vs %d" % (c1, c2))

    # ---------------------------------------------------------- 持续吞吐
    if args.stream > 0:
        print("\n=== 4. 持续吞吐：%d × %d 点，不间停 ===" % (args.stream, MAXN))
        n_big = MAXN
        xs = synth(n_big)
        inb = allocate(shape=(n_big,), dtype=np.int32)
        outb = allocate(shape=(n_big,), dtype=np.int32)
        # 先空跑一次，把首块的冷启动（DMA 初始化、cache 预热）排除掉
        run_dma(dma, ctrl, stat, xs, drc=args.drc, inbuf=inb, outbuf=outb,
                verify=False)

        cyc_sum = 0
        wall_sum = 0.0
        t0 = time.perf_counter()
        for i in range(args.stream):
            _, c, tw = run_dma(dma, ctrl, stat, xs, drc=args.drc,
                               inbuf=inb, outbuf=outb, verify=False)
            cyc_sum += c
            wall_sum += tw
        dt = time.perf_counter() - t0

        total = args.stream * n_big
        rate = total / dt
        print("  [info] %d 点 × %d 次 = %d 点，用时 %.3f s"
              % (n_big, args.stream, total, dt))
        print("  [info] 纯传输墙钟吞吐 %.2f Msamp/s  => %.0f 倍实时 @48k"
              % (total / wall_sum / 1e6, total / wall_sum / 48000.0))
        print("  [info] 每块墙钟 %.2f ms（其中核内 %.2f ms，DMA+驱动 %.2f ms）"
              % (wall_sum / args.stream * 1e3,
                 (cyc_sum / args.stream) / FCLK_HZ * 1e3,
                 (wall_sum / args.stream - (cyc_sum / args.stream) / FCLK_HZ) * 1e3))
        print("  [info] 核内吞吐 %.2f Msamp/s（%.2f 拍/采样）"
              % (total / (cyc_sum / FCLK_HZ) / 1e6, cyc_sum / total))
        check("持续吞吐 ≥ 48 kHz 实时", total / wall_sum >= 48000.0,
              "%.0f 倍" % (total / wall_sum / 48000.0))

        # 吞吐量完了再验一遍数据：这一趟带着逐点判据，不进上面的计时
        print("\n=== 4b. 最后一块逐点验 ===")
        y_big, _, _ = run_dma(dma, ctrl, stat, xs, drc=args.drc,
                              inbuf=inb, outbuf=outb)
        if args.drc:
            print("  [skip] %d 点没有黄金向量，只验状态位和符号扩展" % n_big)
        else:
            compare_delay(xs, y_big, "%d 点" % n_big)

    if args.dump:
        np.savez(args.dump, x=x, y=y1)
        print("\n已存 %s" % args.dump)

    print("\n=== 结论 ===")
    if ok_all:
        print("  全过。输出与 xsim 逐位一致，核内 3 拍/采样，块间可复现。")
    else:
        print("  有 FAIL，逐条看上面的 [info] 行。")
    print("  总用时 %.1f s" % (time.perf_counter() - t_start))
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
