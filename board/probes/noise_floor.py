#!/usr/bin/env python3
"""量底噪的性格：到底是「嘶」（宽带）、「嗡」（50 Hz 工频）还是「隆」（低频隆隆）。

用法（板上跑，**不要 Overlay()** —— live 正在跑，重配 PL 会把 AXI 挂死）：

    sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 noise_floor.py [--secs 12]

做法：
  1. 等通路在跑（/api/start 过），人别说话；
  2. 每 0.4 s 抓一窗 4096 点（走 0x78/0x7C 窗口，和网页同一套读法）；
  3. 去直流 → 加汉宁窗 → rfft → 按 1/3 倍频程汇总，再挑几个关键点：
     50/100/150 Hz（工频与谐波）、300 Hz–3 kHz（语音带）、3–8 kHz（嘶声最刺耳）。
  4. 输出每段的 dBFS，并给一句结论。

判据（人话）：
  * 50/100 Hz 比邻近频段高 10 dB 以上 → 工频/地环路，去查电源和地，数字侧降增益没用。
  * 1 kHz 以上是平的、整体 −75 dBFS 左右 → 白噪，多半是话筒自噪声 + 模拟链路。
  * 能量集中在 200 Hz 以下且滚降陡 → 空调/风扇/桌面震动，加高通（PL 里两级 biquad 就够）。
"""

import argparse
import json
import os
import time
import urllib.request

import numpy as np

REGS_BASE = 0x43C40000
OFF_SCOPE_CTL = 0x64
OFF_SCOPE_WR = 0x6C
OFF_CAP_IDX = 0x78
OFF_CAP_DATA = 0x7C

FS = 48828.0
N = 1024          # 可读窗口上限（环形 2048 字，读口只到 0..1023）


def _http(path, body=None, timeout=8):
    base = "http://127.0.0.1:8080"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def grab(regs):
    """冻 → 读 4096 字 → 解冻。返回 (in, out) 两路 int16 数组。"""
    regs.write(OFF_SCOPE_CTL, 1)
    time.sleep(0.002)
    ptr = regs.read(OFF_SCOPE_WR)
    w = np.empty(N, dtype=np.uint32)
    for i in range(N):
        regs.write(OFF_CAP_IDX, (i * 4) & 0xFFF)     # 字节地址步进
        w[i] = regs.read(OFF_CAP_DATA)
    regs.write(OFF_SCOPE_CTL, 0)
    k = (((ptr >> 2) & 0x3FF) + 1) & 0x3FF
    w = np.roll(w, -k)
    ins = ((w & 0xFFFF).astype(np.int32) ^ 0x8000) - 0x8000
    outs = (((w >> 16) & 0xFFFF).astype(np.int32) ^ 0x8000) - 0x8000
    return ins, outs


def band_db(x, lo, hi):
    """一段频带的 RMS，dBFS（满刻度 32768）。"""
    sp = np.fft.rfft(x * np.hanning(len(x)))
    fr = np.fft.rfftfreq(len(x), 1.0 / FS)
    m = (fr >= lo) & (fr < hi)
    if not m.any():
        return -120.0
    # 单边谱 → 时域幅度：乘 2/N，再去掉窗的相干增益（汉宁 0.5）
    amp = np.abs(sp[m]) * 2.0 / (len(x) * 0.5)
    return 20 * np.log10(max(np.sqrt((amp ** 2).sum()), 1e-12) / 32768.0)


def tone_db(x, f0, half=25.0):
    """单频点的能量（±half Hz），dBFS。"""
    return band_db(x, f0 - half, f0 + half)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=12.0)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    os.environ.setdefault("XILINX_XRT", "/usr")
    from pynq import MMIO

    regs = MMIO(REGS_BASE, 0x1000)

    st = _http("/api/status")
    if not st.get("running"):
        print("!! 通路没开，先 /api/start"); return 1
    # 底噪要在「增益生效」的状态下量，否则量的是 0 dB 的数字底噪
    print("增益 =", st.get("gain_applied_db"))

    rows = []
    t_end = time.time() + a.secs
    while time.time() < t_end:
        ins, outs = grab(regs)
        rows.append(ins.astype(np.float64))
        time.sleep(0.05)

    x = np.concatenate(rows)
    x = x - x.mean()
    print("采样 %d 点（%.1f s），峰值 %d LSB，RMS %.1f LSB" %
          (len(x), len(x) / FS, int(np.abs(x).max()), float(np.sqrt((x ** 2).mean()))))

    bands = [(20, 50), (50, 100), (100, 200), (200, 500), (500, 1000),
             (1000, 2000), (2000, 4000), (4000, 8000), (8000, 16000)]
    res = {"rms_dbfs": round(20 * np.log10(max(np.sqrt((x ** 2).mean()), 1e-9) / 32768), 1),
           "peak_lsb": int(np.abs(x).max())}
    print("\n  频段          电平 dBFS")
    for lo, hi in bands:
        d = round(float(band_db(x, lo, hi)), 1)
        res["band_%d_%d" % (lo, hi)] = d
        print("  %5d–%-5d Hz   %6.1f" % (lo, hi, d))

    print("\n  工频与谐波（±25 Hz）")
    for f in (50, 100, 150, 200):
        d = round(float(tone_db(x, f)), 1)
        res["tone_%d" % f] = d
        print("  %3d Hz         %6.1f" % (f, d))

    talk = res["band_500_1000"]
    hiss = (res["band_2000_4000"] + res["band_4000_8000"]) / 2
    hum = max(res["tone_50"], res["tone_100"]) - (res["band_20_50"] + res["band_100_200"]) / 2

    print("\n  结论：", end="")
    if hum > 10:
        print("工频突出（%.0f dB 高于邻带）→ 电源/地环路，降数字增益没用，要查供电和屏蔽。" % hum)
    elif talk > hiss + 3:
        print("能量偏中低频（500–1k %.0f vs 2–8k %.0f）→ 更像环境/桌面振动，"
              "PL 里加一级 120 Hz 高通就能改善。" % (talk, hiss))
    else:
        print("宽带平铺（2–8k %.0f dBFS）→ 白噪，话筒自噪声 + 模拟链路。"
              "数字侧降增益、靠近说话最有效；算法降噪（谱减）能再压 6~10 dB。" % hiss)

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("  JSON →", a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
