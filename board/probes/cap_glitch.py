#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cap_glitch.py —— 连续抓窗口，找“啪”那一刻数字流里的跳变。

实时话筒模式下每 ~45 ms 抓一窗（1024 点 = 21 ms，覆盖率约 45%），
窗口里 LSB = 进核 fe_din，MSB = 出核 mpo_l。对两路分别算二阶差分，
找远超本底的单点跳变。

判读：
  进核和出核同一位置都跳  → 跳变来自 ADC 之前，codec 输入侧
  只有出核跳              → 跳变产生在进核之后（增益/DRC/FIFO 喂数）
  两路都不跳              → 数字流干净，“啪”在 DAC 之后

板上跑（live 服务在线，不碰播放文件）：
    sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u cap_glitch.py --seconds 180
"""
import argparse
import json
import time

from pynq import MMIO

REGS_BASE = 0x43C40000
OFF_SCOPE_CTL = 0x64
OFF_SCOPE_WR = 0x6C
OFF_CAP_IDX = 0x78
OFF_CAP_DATA = 0x7C

N = 1024          # 读口按字节地址解释，下标 ×4
FS = 48828.0


def s16(v):
    v &= 0xFFFF
    return v - 65536 if v >= 32768 else v


def read_window(regs):
    regs.write(OFF_SCOPE_CTL, 1)
    time.sleep(0.002)
    ptr = regs.read(OFF_SCOPE_WR)
    words = []
    for i in range(N):
        regs.write(OFF_CAP_IDX, (i * 4) & 0xFFF)
        words.append(regs.read(OFF_CAP_DATA))
    regs.write(OFF_SCOPE_CTL, 0)
    rot = (((ptr >> 2) & 0x3FF) + 1) & 0x3FF
    words = words[rot:] + words[:rot]
    ins = [s16(x) for x in words]
    outs = [s16(x >> 16) for x in words]
    return ins, outs


def spikes(x, k_mad=12.0, abs_min=20.0):
    """二阶差分找单点跳变。返回 [(相对下标, 跳变幅度)]。"""
    if len(x) < 8:
        return []
    d2 = [abs(x[i + 1] - 2 * x[i] + x[i - 1]) for i in range(1, len(x) - 1)]
    s = sorted(d2)
    med = s[len(s) // 2]
    mad = sorted(abs(v - med) for v in d2)[len(d2) // 2] or 1
    thr = med + k_mad * 1.4826 * mad
    thr = max(thr, abs_min)
    return [(i + 1, d2[i]) for i in range(len(d2)) if d2[i] > thr]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=180.0)
    a = ap.parse_args()

    regs = MMIO(REGS_BASE, 0x1000)
    windows = 0
    events = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < a.seconds:
        t = round(time.monotonic() - t0, 2)
        ins, outs = read_window(regs)
        windows += 1
        si = spikes(ins)
        so = spikes(outs)
        # 同一个相对位置 ±2 点内同时出现，算“两路同跳”
        for i, v in so:
            near = [j for j, w in si if abs(j - i) <= 2]
            tag = "BOTH" if near else "OUT"
            events.append({"t": t, "where": tag, "i": i,
                           "in_jump": round(ins[i - 1] - 2 * ins[i] + ins[i + 1], 0),
                           "out_jump": round(v, 0),
                           "in": ins[i], "out": outs[i]})
        for i, v in si:
            if not [j for j, w in so if abs(j - i) <= 2]:
                events.append({"t": t, "where": "IN", "i": i,
                               "in_jump": round(v, 0), "out_jump": 0,
                               "in": ins[i], "out": outs[i]})

    events.sort(key=lambda e: -max(abs(e["in_jump"]), abs(e["out_jump"])))
    print("windows=%d  coverage=%.0f%%  events=%d"
          % (windows, 100.0 * windows * (N / FS) / a.seconds, len(events)))
    for e in events[:15]:
        print("  t=%7.2fs %-4s i=%4d  in=%-6d out=%-6d  in_jump=%8.0f out_jump=%8.0f"
              % (e["t"], e["where"], e["i"], e["in"], e["out"],
                 e["in_jump"], e["out_jump"]))
    out = {
        "seconds": a.seconds,
        "windows": windows,
        "events": len(events),
        "both": sum(1 for e in events if e["where"] == "BOTH"),
        "out_only": sum(1 for e in events if e["where"] == "OUT"),
        "in_only": sum(1 for e in events if e["where"] == "IN"),
    }
    print("CAP_GLITCH " + json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
