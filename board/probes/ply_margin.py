#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ply_margin.py —— 量播放读指针（0x1C）的真实推进节拍。**只在板子上跑**（自测除外）。

    sudo env XILINX_XRT=/usr PYTHONIOENCODING=utf-8 \
        /usr/local/share/pynq-venv/bin/python3 ply_margin.py --seconds 60

    python3 ply_margin.py --selftest      # PC 上跑，不需要 pynq / 板子

为什么要它：`ply_cnt` 只在 `fe_din_vld && ply_sel` 时自增（`room` 是输出
FIFO 水位节流，`fe_din_ready` 是前端背压）。所以读指针**不是**匀速的 ——
它一卡，写端（PS 按绝对时刻、每 1024 样本一块）还在往前走。

两个容易混的量（单位都是秒，但含义不同，别互相套）：

  lead    = LEAD_CHUNKS * CHUNK / FS      写头领先读头多少
          4 块 = 83.9 ms，5 块 = 104.9 ms（也就是常说的「预读 105 ms」）
  margin  = (RING - LEAD_CHUNKS*CHUNK)/FS 读头能停多久还不被写头套圈
          4 块 = 251.6 ms，5 块 = 230.7 ms

预读加一块 = lead 多 21 ms、margin 少 21 ms。判据用的是 **margin**：卡超过
margin，写端就绕一圈盖掉读端还没读的字，耳朵听到的就是一次跳变。
8a6e7f5 之前这里把「4 块的 lead（84 ms）」当阈值用，比真 margin 小 3 倍，
会漏报套圈。

`--lead-chunks` 要和板上跑的 `fir_pl_live.py` 里 `PLAY_LEAD_CHUNKS` 一致，
否则判据会偏。板上还是 4 块时用 `--lead-chunks 4`。

输出 /dev/shm/ply_margin_<tag>.jsonl，一行一个采样：
    {"t": 12.345, "rd": 1234, "pop": 7}

最后一行打印 PLY_MARGIN {json} 汇总：
    rd_rate_hz       读指针推进速率（逐点解绕后累加，14 位计数器绕圈不影响）
    stall_max_ms     最长的一次「读指针不动」，**不是**「慢了」。读指针只要还在
                     动就不算卡顿，即使这几毫秒只走了几个字。判断卡顿要不要紧
                     看它和 margin 的比：stall_max_ms < margin_ms 就还没套圈。
    stall_n_ge_margin  超过 margin 的卡顿次数（非 0 = 已经套圈）
    pop_delta        这轮 POP_CNT（0x80，|Δ|>8192 的次数）涨了多少
"""
import argparse
import json
import sys
import time

BASE = 0x43C40000
OFF_PLY_RD = 0x1C
OFF_PLY_CTL = 0x24
OFF_POP_CNT = 0x80

FS = 48828.125
RING = 16384
CHUNK = 1024
LEAD_CHUNKS_DEFAULT = 5       # 和 fir_pl_live.py 的 PLAY_LEAD_CHUNKS 对齐
RD_MASK = 0x3FFF


def margin_sec_of(lead_chunks):
    return (RING - lead_chunks * CHUNK) / FS


def analyze(rows, margin_sec):
    """rows: [{"t", "rd", "pop"}]。逐点解绕后累加，再找最长静止。

    解绕（T4）：相邻两点差值先按 14 位折到 ±8192 以内，再累加。**不能**用
    (last - first) & 0x3FFF —— 计数器一圈只有 16384 个字（335 ms @ 48.8 kHz），
    60 秒的采集会绕 179 圈，只看首尾等于把圈数全丢掉。
    逐点解绕的前提是「相邻两点之间读指针不会真的走超过 8192 个字」，
    也就是采样率不能低于 FS/8192 ≈ 6 Hz；默认 500 Hz 时每点只走 98 个字。
    """
    total = 0
    stalls = []
    stall = 0.0
    for i in range(1, len(rows)):
        d = (rows[i]["rd"] - rows[i - 1]["rd"]) & RD_MASK
        if d > RD_MASK // 2:
            d -= RD_MASK + 1
        total += d
        if rows[i]["rd"] == rows[i - 1]["rd"]:
            stall += rows[i]["t"] - rows[i - 1]["t"]
        else:
            if stall > 0:
                stalls.append(stall)
            stall = 0.0
    if stall > 0:
        stalls.append(stall)

    stalls.sort(reverse=True)
    long_stalls = [s for s in stalls if s >= margin_sec]
    span = rows[-1]["t"] - rows[0]["t"] if len(rows) > 1 else 0.0

    return {
        "span_s": round(span, 3),
        "rd_advance": total,
        "rd_rate_hz": round(total / span, 2) if span else 0,
        "nominal_hz": round(FS, 3),
        "rate_err_ppm": round((total / span - FS) / FS * 1e6, 1) if span else 0,
        "stall_max_ms": round(stalls[0] * 1e3, 1) if stalls else 0.0,
        "stall_n_ge_margin": len(long_stalls),
        "stall_ge_margin_ms": [round(s * 1e3, 1) for s in long_stalls[:20]],
    }


def selftest():
    """造假数据：绕圈、匀速、一次卡顿，检查解绕和判据。"""
    dt = 0.002
    ok = True

    # 1) 60 s 匀速，位置连续累加后取低 14 位 —— 跨两次回绕
    rows = []
    pos = 0.0
    t = 0.0
    wraps = 0
    prev = 0
    for _ in range(30000):
        pos += FS * dt
        rd = int(pos) & RD_MASK
        if rd < prev:
            wraps += 1
        prev = rd
        rows.append({"t": t, "rd": rd, "pop": 0})
        t += dt
    r = analyze(rows, margin_sec_of(5))
    good = abs(r["rate_err_ppm"]) < 1.0          # 判据：< 1 ppm
    ok &= good
    print("SELFTEST rate  %s  %.2f Hz (err %.2f ppm), 绕圈 %d 次, 推进 %d 字"
          % ("PASS" if good else "FAIL", r["rd_rate_hz"], r["rate_err_ppm"],
             wraps, r["rd_advance"]))

    # 2) 中间卡 200 ms，余量按 100 ms 判 —— 必须抓到 1 次
    rows = []
    pos = 0.0
    t = 0.0
    for i in range(5000):
        if not (2000 <= i < 2100):
            pos += FS * dt
        rows.append({"t": t, "rd": int(pos) & RD_MASK, "pop": 0})
        t += dt
    r = analyze(rows, 0.100)
    good = (195 <= r["stall_max_ms"] <= 205) and r["stall_n_ge_margin"] >= 1
    ok &= good
    print("SELFTEST stall %s  max %.1f ms, n_ge_margin %d"
          % ("PASS" if good else "FAIL", r["stall_max_ms"], r["stall_n_ge_margin"]))

    print("SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--tag", default="run")
    ap.add_argument("--rate", type=float, default=500.0,
                    help="采样率（Hz），太高会自己把板子拖卡")
    ap.add_argument("--lead-chunks", type=int, default=LEAD_CHUNKS_DEFAULT,
                    help="板上 Player 的 PLAY_LEAD_CHUNKS（4 旧 / 5 新）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    # 解绕的前提：相邻两点之间读指针走不到半圈（8192 字）
    step = FS / a.rate
    if step > RD_MASK // 2:
        print("!! --rate %.2f 太低：每点要走 %.0f 个字（>8192），解绕分不清方向。"
              "最低用 %.2f Hz。" % (a.rate, step, FS / (RD_MASK // 2)))
        return 2

    from pynq import MMIO

    margin = margin_sec_of(a.lead_chunks)
    regs = MMIO(BASE, 0x1000)
    out_path = "/dev/shm/ply_margin_%s.jsonl" % a.tag
    f = open(out_path, "w")

    t0 = time.monotonic()
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
        rd = regs.read(OFF_PLY_RD) & RD_MASK
        pop = regs.read(OFF_POP_CNT)
        f.write('{"t": %.6f, "rd": %d, "pop": %d}\n' % (t, rd, pop))
        rows += 1

    f.close()

    data = []
    with open(out_path) as g:
        for line in g:
            data.append(json.loads(line))

    summary = analyze(data, margin)
    summary.update({
        "tag": a.tag,
        "rows": rows,
        "lead_chunks": a.lead_chunks,
        "margin_ms": round(margin * 1e3, 1),
        "pop_first": pop_first,
        "pop_last": data[-1]["pop"],
        "pop_delta": (data[-1]["pop"] - pop_first) & 0xFFFFFFFF,
        "file": out_path,
    })
    print("PLY_MARGIN " + json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
