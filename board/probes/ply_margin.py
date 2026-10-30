#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ply_margin.py —— 量播放读指针（0x1C）的真实推进节拍。**只在板子上跑。**

    sudo env XILINX_XRT=/usr PYTHONIOENCODING=utf-8 \
        /usr/local/share/pynq-venv/bin/python3 ply_margin.py --seconds 60

为什么要它：`ply_cnt` 只在 `fe_din_vld && ply_sel` 时自增（`room` 是输出
FIFO 水位节流，`fe_din_ready` 是前端背压）。所以读指针**不是**匀速的 ——
它一卡，写端（PS 按绝对时刻、每 1024 样本一块）还在往前走，两块之间只有
`PLAY_RING - 4*PLAY_CHUNK = 4096` 样本 = 84 ms 的余量。卡超过 84 ms，
写端就绕一圈盖掉读端还没读的字，耳朵听到的就是一次跳变。

本脚本直接看这 84 ms 余量还剩多少：读 0x1C，记录每次自增的墙钟间隔。

输出 /dev/shm/ply_margin_<tag>.jsonl，一行一个采样：
    {"t": 12.345, "rd": 1234, "pop": 7}

最后一行打印 PLY_MARGIN {json} 汇总：
    gaps_gt_84ms     卡顿超过 4096 样本的时长（这些就是「啪」的候选）
    step_hist        读指针每走 1 格用掉的时间直方图（ms 分桶）
    pop_delta        这轮 POP_CNT（0x80，|Δ|>8192 的次数）涨了多少
"""
import argparse
import json
import sys
import time

from pynq import MMIO

BASE = 0x43C40000
OFF_PLY_RD = 0x1C
OFF_PLY_CTL = 0x24
OFF_POP_CNT = 0x80

FS = 48828.125
RING = 16384
MARGIN = 4096                 # 写端领先读端的样本数
MARGIN_SEC = MARGIN / FS      # 83.9 ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--tag", default="run")
    ap.add_argument("--rate", type=float, default=500.0,
                    help="采样率（Hz），太高会自己把板子拖卡")
    a = ap.parse_args()

    regs = MMIO(BASE, 0x1000)
    out_path = "/dev/shm/ply_margin_%s.jsonl" % a.tag
    f = open(out_path, "w")

    t0 = time.monotonic()
    rd_prev = None
    pop_first = regs.read(OFF_POP_CNT)
    rows = 0

    period = 1.0 / a.rate
    nxt = t0
    while True:
        now = time.monotonic()
        if now - t0 >= a.seconds:
            break
        if now < nxt:
            time.sleep(nxt - now)
        nxt += period
        t = time.monotonic() - t0
        rd = regs.read(OFF_PLY_RD) & 0x3FFF
        pop = regs.read(OFF_POP_CNT)
        f.write('{"t": %.6f, "rd": %d, "pop": %d}\n' % (t, rd, pop))
        rows += 1
        rd_prev = rd

    # 卡顿时长：连续同一个 rd 值持续了多久。直接重算 jsonl，比循环里维护状态机
    # 不容易写错；采样周期 ~2 ms，所以低于 2 ms 的卡顿看不见 —— 我们要找的是 84 ms。
    f.close()

    rows_data = []
    with open(out_path) as g:
        for line in g:
            rows_data.append(json.loads(line))

    stall = 0.0
    stalls = []
    total = 0
    for i in range(1, len(rows_data)):
        d = (rows_data[i]["rd"] - rows_data[i - 1]["rd"]) & 0x3FFF
        if d > 8192:
            d -= 16384
        total += d
        if rows_data[i]["rd"] == rows_data[i - 1]["rd"]:
            stall += rows_data[i]["t"] - rows_data[i - 1]["t"]
        else:
            if stall > 0:
                stalls.append(stall)
            stall = 0.0
    if stall > 0:
        stalls.append(stall)

    stalls.sort(reverse=True)
    long_stalls = [s for s in stalls if s >= MARGIN_SEC]
    span = rows_data[-1]["t"] - rows_data[0]["t"]

    summary = {
        "tag": a.tag,
        "rows": rows,
        "span_s": round(span, 3),
        "rd_advance": total,
        "rd_rate_hz": round(total / span, 2) if span else 0,
        "nominal_hz": round(FS, 3),
        "rate_err_ppm": round((total / span - FS) / FS * 1e6, 1) if span else 0,
        "stall_max_ms": round(stalls[0] * 1e3, 1) if stalls else 0.0,
        "stall_n_ge_margin": len(long_stalls),
        "stall_ge_margin_ms": [round(s * 1e3, 1) for s in long_stalls[:20]],
        "pop_first": pop_first,
        "pop_last": rows_data[-1]["pop"],
        "pop_delta": (rows_data[-1]["pop"] - pop_first) & 0xFFFFFFFF,
        "file": out_path,
    }
    print("PLY_MARGIN " + json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
