"""分辨「偶发咔哒」的来源：I2S 丢样点 vs 我们写参数造成的 ramp 台阶。

## 两个必须知道的坑（都是 2026-10-30 当天踩出来的）

1. **`0x78` 收的是字节地址**：写 `i*4` 才拿到第 i 个字。写 `i` 的话每 4 个下标
   读到同一个字，时间轴和频谱全部错 4 倍。
2. 环形只有 **2048 字**，可读窗口 **1024 点（21 ms）**，接缝精确落在
   `((cap_wr>>2)&0x3FF)+1`，按这个位置旋转后接缝在两端，掩掉即可。

## 为什么必须冻结再读

读窗口走 `0x78` → `0x7C`，一次 40 µs，读满 1024 点约 41 ms，而可读窗口只有
21 ms 的音频。**不冻结的话，扫到一半写指针就追上来了**：
窗口里必然出现一道「读到的新旧样本交界」，Δ⁸ 直接上 5e5 —— 这是测法造的，
不是音频里的。实测：不冻结连读 60 s，100% 的窗口都被判成"有台阶"，全是假象。

冻结（`0x64[0]=1`）后写指针停住，`0x6C` 里锁存的那份就是接缝位置。
代价是冻结这 165 ms 的音频没记进缓冲，恢复后接缝处有一次相位跳变 ——
但位置已知（就是锁存到的 `W`），按 `W±32` 掩掉即可。

## 判据

八阶差分 max|Δ⁸|：250 Hz、幅 8000 的正弦理论值 ≈ 0，实测干净窗口 45~100；
单点跳变被 C(8,4)=70 倍放大到 10³ 量级。

## 用法

    python3 pop_compare2.py --secs 60 --tag quiet
    python3 pop_compare2.py --secs 60 --tag wiggle --wiggle 5

`--wiggle 5` = 每 5 s 把增益挪 ±0.5 dB（会真的触发 ramp）。
两组的台阶率一样 → 是链路丢样；明显上升 → 是我们写参数。
"""
import argparse
import json
import mmap
import os
import time
import urllib.request

import numpy as np

BASE = 0x43C40000
OFF_SCOPE_CTL = 0x64
OFF_SCOPE_WR = 0x6C
OFF_CAP_IDX = 0x78
OFF_CAP_DATA = 0x7C
N = 1024
MASK = 24          # 接缝两侧各掩掉这么多点


def open_regs():
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    m = mmap.mmap(fd, 4096, mmap.MAP_SHARED,
                  mmap.PROT_READ | mmap.PROT_WRITE, offset=BASE)
    return fd, m, memoryview(m).cast("I")


def grab(mv):
    """冻 → 读 4096 → 解冻。返回 (ins, outs, 接缝位置)。"""
    mv[OFF_SCOPE_CTL >> 2] = 1
    time.sleep(0.003)
    ptr = mv[OFF_SCOPE_WR >> 2]
    w = np.empty(N, dtype=np.uint32)
    for i in range(N):
        # **0x78 收的是字节地址**（BRAM 的 A 口按字节解释）：要写 i*4，
        # 否则读回来每 4 个下标是同一个字（2026-10-30 深夜踩过）
        mv[OFF_CAP_IDX >> 2] = (i * 4) & 0xFFF
        w[i] = mv[OFF_CAP_DATA >> 2]
    mv[OFF_SCOPE_CTL >> 2] = 0
    # 接缝在第 k 点：把窗口转成时间顺序，接缝就落到两端
    k = (((ptr >> 2) & 0x3FF) + 1) & 0x3FF
    w = np.roll(w, -k)
    ins = ((w & 0xFFFF).astype(np.int32) << 16) >> 16
    outs = ((w >> 16).astype(np.int32) << 16) >> 16
    return ins, outs, 0        # 旋转后接缝落在下标 0


def http(path, body=None):
    req = urllib.request.Request("http://127.0.0.1:8080" + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=8))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=60.0)
    ap.add_argument("--tag", default="run")
    ap.add_argument("--wiggle", type=float, default=0.0)
    ap.add_argument("--thr", type=float, default=400.0)
    a = ap.parse_args()

    st = http("/api/status")
    if not st.get("running"):
        print("!! 通路没开"); return 1
    print("增益 %s，录 %.0f s（tag=%s，wiggle=%s）" %
          (st.get("gain_applied_db"), a.secs, a.tag, a.wiggle))

    fd, m, mv = open_regs()
    t0 = time.time()
    tune = http("/api/tune")["tune"]
    base_gain = list(tune["gain_db"])
    nxt_wig = t0 + a.wiggle if a.wiggle else 1e18
    flip = 0

    n = 0
    flagged = 0
    rows = []
    while time.time() - t0 < a.secs:
        if time.time() >= nxt_wig:
            nxt_wig += a.wiggle
            flip ^= 1
            tune["gain_db"] = [g + (0.5 if flip else -0.5) for g in base_gain]
            tune.setdefault("b1", {})["gain_db"] = list(tune["gain_db"])
            try:
                http("/api/tune", tune)
            except Exception as ex:
                print("  wiggle 失败", ex)
        ins, outs, seam = grab(mv)
        # 掩掉接缝两侧（循环意义上，接缝在下标 0）
        keep = np.ones(N, dtype=bool)
        keep[:MASK] = False
        keep[-MASK:] = False
        o = outs[keep].astype(np.float64)
        i_ = ins[keep].astype(np.float64)
        do = float(np.abs(np.diff(o, n=8)).max())
        di = float(np.abs(np.diff(i_, n=8)).max())
        n += 1
        if max(do, di) > a.thr:
            flagged += 1
            rows.append((round(time.time() - t0, 2), int(do), int(di)))
        time.sleep(0.02)

    res = {"tag": a.tag, "secs": round(time.time() - t0, 1), "windows": n,
           "flagged": flagged, "rate_pct": round(100.0 * flagged / max(n, 1), 2),
           "wiggle": a.wiggle, "thr": a.thr,
           "first_events": rows[:8]}
    print(json.dumps(res, ensure_ascii=False))
    with open("/home/xilinx/dma_exp/popcmp2_%s.json" % a.tag, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    tune["gain_db"] = base_gain
    tune.setdefault("b1", {})["gain_db"] = base_gain
    try:
        http("/api/tune", tune)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
