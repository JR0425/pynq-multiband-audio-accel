#!/usr/bin/env python3
"""端到端电学环回：DAC -> 环回线 -> ADC，用抓取窗口量往返延迟。

通路：PL 内部方波（ctrl[3]=1，绕过整条处理链）-> I2S -> codec DAC -> 耳机口
      -> 环回线 -> codec ADC -> I2S -> fe_din -> 抓取窗口低 16 位。
抓取窗口高 16 位是 mpo_l（出核），低 16 位是 fe_din_q（进核），同一拍写入。

所以窗口里 out 的方波起点到 in 的回声起点之间的差，就是
    codec DAC 延迟 + 线缆 + codec ADC 延迟
（PL 处理链被 ctrl[3] 旁路，不进这个差）。把这个差加上已知的
核群 96 采样（1.966 ms），就是 ADC -> PL -> DAC 的前向电学延迟；
声学 1.23 ms 是另一段，不在本测量里。

做法：发一个 0.7 ms 的单脉冲（开 TONE -> 关 TONE），等 9 ms 后冻结窗口。
窗口读最新的 1024 点（21 ms），脉冲起点约在 530 点处，后面有 10 ms 给回声。
tone_sel=0（250 Hz 方波）只决定脉冲极性；用第一个沿定位，不靠稳态相位。
单脉冲不会像连续方波那样每 2 ms 一个沿，避免把第二沿当成第一沿。

用法（板上 root，服务要先停）：

    systemctl stop fir-pl-webui
    pkill -f '[f]ir_pl_live'
    XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u loopback_delay.py

    # 只看电平、不判延迟（怀疑线没接好时先跑这个）
    ... loopback_delay.py --probe-only

    # 只听 ADC（不放音、不出测试音），查话筒通路
    ... loopback_delay.py --listen --secs 60 --pga 0xFF

    # 换输入增益档（0x03 = -12 dB，0xFF = +35.25 dB）
    ... loopback_delay.py --pga 0x43 --amp 300,1000,3000

输出 ASCII，原始窗口存 loopback_caps.npz。
"""
import argparse
import os
import sys
import time

os.environ.setdefault("XILINX_XRT", "/usr")
sys.path.insert(0, "/home/xilinx/dma_exp")

import numpy as np  # noqa: E402
from pynq import MMIO, Overlay  # noqa: E402

import dma_audio_probe as P  # noqa: E402

BIT = "/home/xilinx/dma_exp/fir_pl.bit"
REGS_BASE_FALLBACK = 0x43C40000
CTRL_BASE_FALLBACK = 0x43C00000

OFF_CTRL = 0x00
OFF_TONE_SEL = 0x58
OFF_TONE_AMP = 0x5C
OFF_SCOPE_CTL = 0x64
OFF_SCOPE_WR = 0x6C
OFF_CAP_IDX = 0x78
OFF_CAP_DATA = 0x7C

CTRL_RUN = 1 << 0
CTRL_DRC = 1 << 1
CTRL_FLUSH = 1 << 2
CTRL_TONE = 1 << 3

FS = 100e6 / 2048.0          # 48828.125 Hz，板上真实采样率
N = 1024
DEPTH = 2048


def find_ip(ol, prefix):
    for name, d in ol.ip_dict.items():
        if name.startswith(prefix) and d.get("phys_addr") is not None:
            return name, d["phys_addr"]
    return None, None


def read_window(regs):
    """冻指针 -> 读**最新**的 1024 点（止于写指针）-> 解锁。

    缓冲深 2048 点 = 42 ms。只读最老的一半时，打开方波 10 ms 后的
    样点全在最新端，窗口里是静音 —— 第一次跑就是这么读到 out_peak=0 的。
    """
    regs.write(OFF_SCOPE_CTL, 1)
    time.sleep(0.002)
    ptr = regs.read(OFF_SCOPE_WR) & (DEPTH - 1)
    start = (ptr + 1 - N) & (DEPTH - 1)
    words = [0] * N
    for i in range(N):
        regs.write(OFF_CAP_IDX, ((start + i) & (DEPTH - 1)) * 4)
        words[i] = regs.read(OFF_CAP_DATA)
    regs.write(OFF_SCOPE_CTL, 0)
    ins = np.array([((x & 0xFFFF) ^ 0x8000) - 0x8000 for x in words], dtype=float)
    outs = np.array([(((x >> 16) & 0xFFFF) ^ 0x8000) - 0x8000 for x in words], dtype=float)
    return ins, outs


def first_edge(x, thr, start=0, run=3):
    """第一个连续 run 个点超过 thr 的位置；找不到返回 None。"""
    n = len(x)
    i = start
    while i < n - run + 1:
        if all(abs(x[i + k]) > thr for k in range(run)):
            return i
        i += 1
    return None


def measure(regs, ctrl, amp, pga_code, square=False):
    """返回一次测量的字典。

    默认发一个 0.7 ms 的单脉冲（开 TONE -> 关 TONE），不是连续方波：
    连续方波每 2 ms 一个沿，若第一沿被输入高通/静音门限吃掉，就会把
    第二沿当成第一沿，lag 一次多 98 采样。单脉冲没有这个问题。
    `square=True` 只用于看电平（连续方波，稳态），不算延迟。
    """
    # 先确保静音，抓一段底噪
    ctrl.write(0, CTRL_RUN | CTRL_DRC)
    time.sleep(0.05)
    in0, out0 = read_window(regs)
    noise = float(np.max(np.abs(in0 - np.median(in0))))

    ctrl.write(0, CTRL_RUN | CTRL_DRC | CTRL_FLUSH)
    time.sleep(0.02)
    regs.write(OFF_TONE_SEL, 0)
    regs.write(OFF_TONE_AMP, amp)

    if square:
        ctrl.write(0, CTRL_RUN | CTRL_DRC | CTRL_TONE)
        time.sleep(0.03)
        ins, outs = read_window(regs)
        ctrl.write(0, CTRL_RUN | CTRL_DRC)
    else:
        ctrl.write(0, CTRL_RUN | CTRL_DRC | CTRL_TONE)
        time.sleep(0.0007)
        ctrl.write(0, CTRL_RUN | CTRL_DRC)
        time.sleep(0.009)
        ins, outs = read_window(regs)

    out_peak = float(np.max(np.abs(outs)))
    out_clip = int(np.sum(np.abs(outs) >= 32767))
    i_out = first_edge(outs, max(0.5 * out_peak, 200.0))

    rec = {
        "amp": amp, "pga": pga_code,
        "noise": noise, "out_peak": out_peak, "out_clip": out_clip,
        "i_out": -1, "i_in": -1, "lag": -1,
        "in_peak": 0.0, "in_clip": 0,
        "base": 0.0, "dt_ms": 0.0,
        "ins": ins, "outs": outs,
    }
    if i_out is None:
        return rec
    rec["i_out"] = i_out

    base = float(np.median(ins[:max(i_out - 8, 1)]))
    d = ins - base
    tail = d[i_out:]
    rec["base"] = base
    rec["in_peak"] = float(np.max(np.abs(tail))) if len(tail) else 0.0
    rec["in_clip"] = int(np.sum(np.abs(ins) >= 32767))

    thr = max(5.0 * noise, 6.0)
    i_in = first_edge(d, thr, start=i_out, run=2)
    if i_in is not None:
        rec["i_in"] = i_in
        rec["lag"] = i_in - i_out
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--amp", default="300,1000,3000",
                    help="方波幅度候选（计数），逗号分隔")
    ap.add_argument("--pga", default="0x03",
                    help="codec 模拟输入增益寄存器 0x400E/0x400F 的值，默认 0x03=-12 dB")
    ap.add_argument("--rounds", type=int, default=7)
    ap.add_argument("--probe-only", action="store_true", help="只测电平，不算延迟")
    ap.add_argument("--no-codec", action="store_true", help="不重配 codec")
    ap.add_argument("--input", choices=("mic", "line"), default="mic",
                    help="环回线接的是耳麦话筒(mic)还是线路输入(line)")
    ap.add_argument("--square", action="store_true",
                    help="连续方波只测电平（不算延迟）")
    ap.add_argument("--listen", action="store_true",
                    help="不出测试音、不放音，只打印 ADC 峰值（查话筒通路）")
    ap.add_argument("--secs", type=float, default=60.0, help="--listen 的时长")
    a = ap.parse_args()

    amps = [int(x) for x in a.amp.split(",") if x.strip()]
    pga = int(a.pga, 16) if a.pga.lower().startswith("0x") else int(a.pga)

    print("== 载入 %s ==" % BIT)
    ol = Overlay(BIT)
    time.sleep(0.3)
    rname, rbase = find_ip(ol, "fir_pl_regs")
    cname, cbase = find_ip(ol, "gpio_ctrl")
    if rbase is None:
        print("ERROR: overlay 里没有 fir_pl_regs；overlay 内容：%s"
              % sorted(ol.ip_dict.keys()))
        return 1
    if cbase is None:
        cbase = CTRL_BASE_FALLBACK
    regs = MMIO(rbase, 0x1000)
    ctrl = MMIO(cbase, 0x1000)
    print("  %s @ 0x%08X" % (rname, rbase))
    print("  %s @ 0x%08X" % (cname or "gpio_ctrl(fallback)", cbase))
    print("  ID = 0x%08X" % regs.read(0x000))

    if not a.no_codec:
        print("== 配 codec ==")
        P.codec_init()
        if a.input == "line":
            import cffi
            ffi = cffi.FFI()
            lib = ffi.dlopen(P.LIB_AUDIO)
            ffi.cdef("void select_line_in(int iic_index);")
            lib.select_line_in(1)
            print("  输入选择：LINE_IN")
        else:
            print("  输入选择：MIC（codec_init 里的 select_mic）")
        P.codec_input_gain(pga)
    print("  输入增益 0x%02X = %+.2f dB（含固定 +20 dB boost）"
          % (pga, -12.0 + ((pga >> 2) & 0x3F) * 0.75 + 20.0))

    if a.listen:
        ctrl.write(0, CTRL_RUN | CTRL_DRC | CTRL_FLUSH)
        time.sleep(0.02)
        ctrl.write(0, CTRL_RUN | CTRL_DRC)
        time.sleep(0.2)
        print("\n== 只听 ADC（不放音、不出测试音）%g 秒 ==" % a.secs)
        t0 = time.time()
        while time.time() - t0 < a.secs:
            ins, outs = read_window(regs)
            base = float(np.median(ins))
            peak = int(np.max(np.abs(ins - base)))
            rms = float(np.sqrt(np.mean((ins - base) ** 2)))
            print("  %5.1fs  peak=%6d  rms=%7.1f  base=%7.0f"
                  % (time.time() - t0, peak, rms, base))
            sys.stdout.flush()
            time.sleep(0.5)
        return 0

    # 清掉上一次可能残留的 TONE 位
    ctrl.write(0, CTRL_RUN | CTRL_DRC | CTRL_FLUSH)
    time.sleep(0.02)
    ctrl.write(0, CTRL_RUN | CTRL_DRC)

    print("\n== 幅度扫描 ==")
    print("  amp   out_peak  in_peak   in_clip   noise   i_out  i_in  lag")
    caps = {}
    scan = []
    for amp in amps:
        r = measure(regs, ctrl, amp, pga, square=a.square)
        scan.append(r)
        print("  %5d  %8.0f  %8.0f  %7d  %7.1f  %5d  %4d  %4d"
              % (amp, r["out_peak"], r["in_peak"], r["in_clip"],
                 r["noise"], r["i_out"], r["i_in"], r["lag"]))
        sys.stdout.flush()
        caps["scan_%d_in" % amp] = r["ins"]
        caps["scan_%d_out" % amp] = r["outs"]
        time.sleep(0.05)

    if a.probe_only:
        print("\n--probe-only：不继续测延迟。")
        return 0

    # 选一个 input 不削顶、又足够大的幅度
    usable = [r for r in scan if r["in_peak"] >= 20 and r["in_clip"] == 0]
    if not usable:
        usable = [r for r in scan if r["in_peak"] >= 20]
    if not usable:
        print("\n没收到回声：in_peak 都 < 20。先确认环回线接在耳机输出和话筒输入之间，"
              "以及板上输入跳线选的是耳麦话筒。")
        return 2
    best = max(usable, key=lambda r: r["in_peak"])
    amp = best["amp"]
    print("\n选 amp=%d（in_peak=%.0f，未削顶）" % (amp, best["in_peak"]))

    print("\n== 重复测量 ==")
    lags = []
    rows = []
    for k in range(a.rounds):
        r = measure(regs, ctrl, amp, pga, square=a.square)
        rows.append(r)
        caps["run_%d_in" % k] = r["ins"]
        caps["run_%d_out" % k] = r["outs"]
        if r["lag"] >= 0:
            lags.append(r["lag"])
        print("  #%d  out=%7.0f  in=%7.0f  i_out=%4d  i_in=%4d  lag=%4d  (%.3f ms)"
              % (k + 1, r["out_peak"], r["in_peak"], r["i_out"], r["i_in"],
                 r["lag"], r["lag"] * 1e3 / FS if r["lag"] >= 0 else -1))
        sys.stdout.flush()
        time.sleep(0.05)

    if not lags:
        print("\n所有重复都没找到回声沿。")
        return 2

    med = int(np.median(lags))
    print("\n== 结果 ==")
    print("  有效次数      %d / %d" % (len(lags), a.rounds))
    print("  逐次 lag      %s" % lags)
    print("  中位 lag      %d 采样 = %.3f ms" % (med, med * 1e3 / FS))
    print("  范围          %d..%d 采样 = %.3f..%.3f ms"
          % (min(lags), max(lags), min(lags) * 1e3 / FS, max(lags) * 1e3 / FS))
    print("  codec+线缆往返 %.3f ms" % (med * 1e3 / FS))
    fwd = (med + 96) * 1e3 / FS
    print("  + 核群 96 采样 => ADC->PL->DAC 前向电学延迟 %.3f ms" % fwd)
    print("  + 声学 1.23 ms  => 嘴到耳约 %.3f ms（三项口径，非单次端到端）"
          % (fwd + 1.23))
    print("  注：DAC/ADC 各自的转换延迟各只算了一次；线缆延迟包含在内。")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loopback_caps.npz")
    np.savez(out, lags=np.array(lags), median=med, fs=FS,
             amp=amp, pga=pga, **caps)
    print("  原始窗口 -> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
