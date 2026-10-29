"""增益阶梯试听（+18/24/30/36 dB 各 6 s）—— 2026-10-28 用它拍板 +30
板上跑：sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 gain_ladder.py
"""
# 增益阶梯试听：+18 → +24 → +30 → +36 dB，每档 6 秒，报数给用户挑
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")
from pynq import Overlay, MMIO
import dma_audio_probe as P

BIT = "/home/xilinx/dma_exp/fir_pl.bit"

print("[1] load overlay")
ol = Overlay(BIT)
time.sleep(0.3)
print("[2] codec_init()")
P.codec_init()
print("[3] unmute ok")

regs = None
for name, d in ol.ip_dict.items():
    if name.startswith("fir_pl_regs"):
        regs = MMIO(d["phys_addr"], 0x1000)
if regs is None:
    raise SystemExit("no fir_pl_regs")

regs.write(0x170, 5)          # 斜坡 div=5，切档无声
ol.gpio_ctrl.mmio.write(0, 0x03)   # run | drc_en
time.sleep(0.2)

for db in (18, 24, 30, 36):
    g = int(round(256 * 10 ** (db / 20.0)))
    g = min(g, 32767)
    for i in range(4):
        regs.write(0x100 + 4 * i, g)
    print("=== NOW +%d dB (GAIN=%d), 6 s ===" % (db, g))
    time.sleep(6.0)

ol.gpio_ctrl.mmio.write(0, 0)
print("done, ctrl=0")
