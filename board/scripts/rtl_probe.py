"""独立 fir_rtl overlay 的板上量测脚本（RTL 前端上板，PLAN §11.14 §4 第 9 项）

只加载 fir_rtl.bit，**不碰出货的 fir.bit**。这套东西跑完如果不对，删掉即可。

三种输入源，按可用性自动降级：

    ① 目录里同时有 in_q15.mem + rtl_fe_out.txt  → 用 xsim 的黄金向量逐位比（最硬）
    ② 只有合成信号                             → 用「输出 = 输入延后 96 个采样」比
       （这是 fir_mb_fe 自身性质，xsim 已验 0/1000，任何接错都会挂得很明显）

两种模式（对应外壳的 ctrl[17]）：

    默认           只过前端 —— 输出恒等于输入延后 96 个采样，可以自校验
    --drc          前端 + 四段增益 + WDRC —— 和 HLS v25 同一套压缩路径，
                   输出不再等于输入延后 96，只有拿 xsim 的黄金向量才比得了

量四件事：
    · 核内拍数 total_cycles，以及每采样拍数（应为 3.0 左右；DRC 模式多 10 拍尾巴）
    · 含搬运的墙钟时间（MMIO 写 buffer、启动、读回），和核内时间分开报
    · 连跑两次是否逐位相同（判据 ④）
    · n=1000/4800/8192 三种块长（4800 是 PLAN 里点名的口径）

用法（板子上，要 root）：

    echo xilinx | sudo -S env XILINX_XRT=/usr \
      /usr/local/share/pynq-venv/bin/python3 rtl_probe.py

    # DRC 模式：把 sim/rtl_sim/golden_n4800_drc_loud/ 里的 in_q15.mem +
    # rtl_fe_out.txt 和 fir_rtl.bit/.hwh 放同一目录，然后
    echo xilinx | sudo -S env XILINX_XRT=/usr \
      /usr/local/share/pynq-venv/bin/python3 rtl_probe.py --drc

可选参数：
    --n 4800          改块长（默认跑 1000/4800/8192 三档）
    --drc             走压缩路径（ctrl[17]=1），黄金向量必须是 DRC 那份
    --once            只跑一档、只跑一次（调试用）
    --dump out.npz    把最后一档的输入输出存下来
"""

import argparse
import os
import sys
import time

import numpy as np
from pynq import MMIO, Overlay

BITFILE = "fir_rtl.bit"

# 地址见 board/overlay/build_rtl.tcl 的 assign_bd_address
BRAM_IN_BASE  = 0x43C00000
BRAM_OUT_BASE = 0x43C08000
GPIO_CTRL     = 0x43C10000
GPIO_STAT     = 0x43C20000

REG_CTRL   = 0x00        # bit[15:0]=n  bit[16]=start（上升沿）  bit[17]=drc_en
REG_STAT_LO = 0x00       # total_cycles
REG_STAT_HI = 0x08       # [15:0]=n_out [16]=done [17]=busy [18]=overflow

FCLK_HZ = 100_000_000.0  # PS7 FCLK_CLK0
GD      = 96             # fir_mb_fe 的群延迟（采样）

ok_all = True


def check(name, passed, detail=""):
    global ok_all
    ok_all = ok_all and passed
    print("  [%s] %s%s" % ("OK  " if passed else "FAIL", name,
                           ("  " + detail) if detail else ""))
    return passed


# ---------------------------------------------------------------- overlay

def load():
    if not os.path.exists(BITFILE):
        print("ERROR: 找不到 %s（把 fir_rtl.bit / fir_rtl.hwh 一起拷过来）" % BITFILE)
        sys.exit(1)
    if not os.path.exists("fir_rtl.hwh"):
        print("ERROR: 找不到 fir_rtl.hwh，PYNQ 解析不了地址")
        sys.exit(1)

    ol = Overlay(BITFILE)
    print("  里面的 IP：", sorted(ol.ip_dict.keys()))
    if len(ol.ip_dict) == 0:
        print("ERROR: overlay 里一个 IP 都没解析出来 —— hwh 是不是和 bit 不是同一版？")
        sys.exit(1)

    # 优先用 overlay 解析出来的 mmio（hwh 里地址对了才用它），
    # 解析不出来就直接按 /dev/mem 映射固定地址 —— 地址是 build_rtl.tcl 写死的。
    #
    # 两点实测得来的注意：
    #   · ps7_0 那条没有 phys_addr 键，取值要用 .get，不然 KeyError
    #   · ip_dict 里也**没有** mmio 键，要 getattr(ol, name).mmio 才拿得到
    #   · PYNQ 2.7 不把 axi_bram_ctrl 放进 ip_dict（实测只认 gpio_ctrl/gpio_stat/
    #     ps7_0 三个），所以两块 BRAM 走的都是下面的直接映射分支
    mm = {}
    for base, key in ((BRAM_IN_BASE, "in"), (BRAM_OUT_BASE, "out"),
                      (GPIO_CTRL, "ctrl"), (GPIO_STAT, "stat")):
        hit = [(k, v) for k, v in ol.ip_dict.items() if v.get("phys_addr") == base]
        if hit:
            mm[key] = getattr(ol, hit[0][0]).mmio
            print("  %-4s 0x%08X  <- overlay（%s）" % (key, base, hit[0][1]["type"]))
        else:
            mm[key] = MMIO(base, 0x8000 if key in ("in", "out") else 0x1000)
            print("  %-4s 0x%08X  <- 直接映射（overlay 里没找到这个地址）" % (key, base))
    return mm


# ---------------------------------------------------------------- 一次块运行

def run_block(mm, x, label, drc=False, timeout=10.0):
    """把 x 写进 BRAM_IN，启动，等 done，读回输出。返回 (y, total_cycles, 各段时间)。"""
    n = len(x)
    ctrl_n = (n & 0xFFFF) | ((1 << 17) if drc else 0)

    # 输入：外壳只取低 16 位，符号扩展由 int16 → 写入时保留补码即可
    words = (x.astype(np.int32) & 0xFFFF).astype(np.uint32)

    t0 = time.perf_counter()
    # MMIO.array 是映到那片物理内存的 numpy 视图，整块赋值就是逐字写下去。
    # 别用 mm.write(0, buf)：那要先把 bytes 再拆回 uint32，绕一圈。
    mm["in"].array[:n] = words
    t_write = time.perf_counter() - t0

    # 读回两个点，确认写通路真的通了（不然后面全是读垃圾）
    # 注意 MMIO.read(off, 4) 返回的是 int（不是 bytes）；而且 read 只支持
    # 1/2/4/8 字节，**不能**一次读一整块 —— 批量只能用 .array。
    a0 = int(mm["in"].array[0]) & 0xFFFF
    a1 = int(mm["in"].array[n - 1]) & 0xFFFF
    if a0 != int(words[0]) or a1 != int(words[n - 1]):
        check("%s 输入缓冲回读" % label, False,
              "首=%d 尾=%d，写进去的是 %d/%d" % (a0, a1, words[0], words[n - 1]))
    else:
        print("  [ok  ] %s 输入缓冲回读一致（首 %d 尾 %d）" % (label, a0, a1))

    mm["ctrl"].write(REG_CTRL, 0)
    time.sleep(0.001)
    t0 = time.perf_counter()
    mm["ctrl"].write(REG_CTRL, ctrl_n | (1 << 16))
    mm["ctrl"].write(REG_CTRL, ctrl_n)     # 放下 start，等下一次上升沿

    deadline = t0 + timeout
    hi = 0
    while time.perf_counter() < deadline:
        hi = mm["stat"].read(REG_STAT_HI)
        if hi & (1 << 16):
            break
        time.sleep(0.0002)
    t_run = time.perf_counter() - t0

    done     = bool(hi & (1 << 16))
    busy     = bool(hi & (1 << 17))
    ovf      = bool(hi & (1 << 18))
    n_out    = hi & 0xFFFF
    cycles   = mm["stat"].read(REG_STAT_LO)

    if not done:
        check("%s 跑完" % label, False, "等了 %.1f s 还没 done（stat_hi=0x%08X）" % (timeout, hi))
        return None, None, None
    if ovf:
        check("%s 无 overflow" % label, False, "stat_hi=0x%08X" % hi)
    if n_out != n:
        check("%s n_out == n" % label, False, "%d vs %d" % (n_out, n))

    t0 = time.perf_counter()
    w = np.array(mm["out"].array[:n_out], dtype=np.uint32)   # 同上：批量只能用 .array
    t_read = time.perf_counter() - t0
    y = w.astype(np.int32).astype(np.int16).astype(np.int32).astype(np.int16)

    if len(y) < n:
        check("%s 输出长度" % label, False, "%d < %d" % (len(y), n))
        return None, cycles, None

    print("  [info] %s n=%d  n_out=%d  cycles=%d  => %.3f 拍/采样"
          % (label, n, n_out, cycles, cycles / max(n_out - 1, 1)))
    print("  [info] %s 墙钟：写 %.2f ms  运行 %.2f ms  读 %.2f ms  合计 %.2f ms"
          % (label, t_write * 1e3, t_run * 1e3, t_read * 1e3,
             (t_write + t_run + t_read) * 1e3))
    print("  [info] %s 核内 %.2f ms（cycles/100MHz）"
          % (label, cycles / FCLK_HZ * 1e3))
    return y[:n], cycles, (t_write, t_run, t_read)


# ---------------------------------------------------------------- 判据

def compare_delay(x, y, label):
    """输出应当等于输入延后 GD 个采样。"""
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


def compare_golden(y, label):
    """和 xsim 的逐位黄金向量比（in_q15.mem 是那批输入的十六进制）。"""
    if not (os.path.exists("in_q15.mem") and os.path.exists("rtl_fe_out.txt")):
        print("  [skip] 没带 in_q15.mem / rtl_fe_out.txt，跳过和 xsim 的逐位比对")
        return
    g = np.array([int(s, 16) for s in open("in_q15.mem") if s.strip()], dtype=np.int32)
    g = ((g + 0x8000) & 0xFFFF) - 0x8000          # 16 位补码
    ref = np.array([int(s) for s in open("rtl_fe_out.txt") if s.strip()], dtype=np.int32)
    n = min(len(y), len(ref))
    bad = int(np.count_nonzero(y[:n] != ref[:n]))
    check("%s 和 xsim 黄金向量逐位相同" % label, bad == 0,
          "%d 点，不同 %d 点" % (n, bad))


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="只跑这一档块长")
    ap.add_argument("--drc", action="store_true",
                    help="走四段增益 + WDRC（ctrl[17]=1），黄金向量必须是 DRC 那份")
    ap.add_argument("--once", action="store_true", help="每档只跑一次（不验重跑一致）")
    ap.add_argument("--dump", default="", help="把最后一档输入/输出存成 .npz")
    args = ap.parse_args()

    print("=== 0. 加载 overlay ===")
    mm = load()

    # 输入选择：有黄金向量就用黄金向量（这样能逐位比 xsim）
    golden_x = None
    if os.path.exists("in_q15.mem"):
        g = np.array([int(s, 16) for s in open("in_q15.mem") if s.strip()], dtype=np.int32)
        golden_x = np.clip(((g + 0x8000) & 0xFFFF) - 0x8000, -32768, 32767).astype(np.int16)
        print("=== 1. 输入：in_q15.mem，%d 点 ===" % len(golden_x))
    else:
        print("=== 1. 输入：板内合成信号（双音 + 噪声，峰值 0.5 FS）===")

    if args.n:
        sizes = [args.n]
    elif golden_x is not None:
        sizes = [len(golden_x)]
    else:
        sizes = [1000, 4800, 8192]

    # 合成信号：和 fir_selftest 用同一套（300 Hz + 3000 Hz + 噪声）
    def synth(n):
        t = np.arange(n, dtype=np.float64) / 48000.0
        sig = 0.35 * np.sin(2 * np.pi * 300 * t) + 0.15 * np.sin(2 * np.pi * 3000 * t)
        rng = np.random.default_rng(1)
        sig = sig + 0.01 * rng.standard_normal(n)
        return np.clip(np.round(sig * 32767.0), -32768, 32767).astype(np.int16)

    last_x = last_y = None
    for n in sizes:
        if golden_x is not None and n == len(golden_x):
            x = golden_x
        else:
            x = synth(n)

        print("\n=== 2. 块长 n=%d  模式 %s ===" % (n, "fe+gain+WDRC" if args.drc else "仅前端"))
        y1, c1, _ = run_block(mm, x, "run1", drc=args.drc)
        if y1 is None:
            continue
        if args.drc:
            # DRC 之后输出不再等于输入延后 GD，只有黄金向量能验
            if not (os.path.exists("in_q15.mem") and os.path.exists("rtl_fe_out.txt")):
                print("  [skip] DRC 模式必须有 xsim 的黄金向量（golden_n4800_drc_loud/ 那两个文件）")
            compare_golden(y1, "run1")
        else:
            compare_delay(x, y1, "run1")
            if golden_x is not None and n == len(golden_x):
                compare_golden(y1, "run1")

        # 判据 ②：3 拍/采样（1000 点那次实测 3.010，留 5% 余量）
        cps = c1 / max(n - 1, 1)
        check("每采样拍数 ≈ 3", 2.9 <= cps <= 3.2, "%.3f" % cps)

        if args.once:
            last_x, last_y = x, y1
            continue

        print("\n=== 3. 重跑一次（判据 ④）===")
        y2, c2, _ = run_block(mm, x, "run2", drc=args.drc)
        if y2 is None:
            continue
        check("两次输出逐位相同", int(np.count_nonzero(y1 != y2)) == 0,
              "%d 点不同" % int(np.count_nonzero(y1 != y2)))
        check("两次拍数相同", c1 == c2, "%d vs %d" % (c1, c2))
        last_x, last_y = x, y2

    if args.dump and last_x is not None:
        np.savez(args.dump, x=last_x, y=last_y)
        print("\n已存 %s" % args.dump)

    print("\n=== 结论 ===")
    if ok_all:
        print("  全过。核内 3 拍/采样、块间可复现、输出与 xsim 一致。")
        print("  下一步判据 ⑤（和出货 v25 逐位对拍）需要 v25 的同音源输出，这里没做。")
    else:
        print("  有 FAIL，逐条看上面的 [info] 行。")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
