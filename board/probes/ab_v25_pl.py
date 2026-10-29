"""v25 vs PL直连 响度 A/B（各 0 dB）—— 注意段A依赖板上已编 libaudio_stream.so
板上跑：sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 ab_v25_pl.py
"""
# A/B 响度对照：段A = v25（fir.bit + fir_live，0 dB）；段B = PL直连（GAIN 0 dB）
# 用户对着麦克风连续说话，凭耳朵报"PL 比 v25 小多少 dB"。
import subprocess
import sys
import time

sys.path.insert(0, "/home/xilinx/dma_exp")
from pynq import Overlay, MMIO
import dma_audio_probe as P

print("=== 段 A：v25（fir_live，增益 0 dB）20 秒，请持续说话 ===")
r = subprocess.run(
    ["python3", "/home/xilinx/dma_exp/fir_live.py"],
    timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("    段 A 结束")

print("    ... 5 秒间隔 ...")
time.sleep(5.0)
print(">>> 现在是段 B（PL 直连，0 dB），请用同样的音量说话 <<<")

print("=== 段 B：PL 直连（GAIN 0 dB）12 秒 ===")
ol = Overlay("/home/xilinx/dma_exp/fir_pl.bit")
time.sleep(0.3)
P.codec_init()
regs = None
for name, d in ol.ip_dict.items():
    if name.startswith("fir_pl_regs"):
        regs = MMIO(d["phys_addr"], 0x1000)
if regs:
    regs.write(0x170, 5)          # 斜坡
    for i in range(4):
        regs.write(0x100 + 4 * i, 256)   # 0 dB
ol.gpio_ctrl.mmio.write(0, 0x03)
time.sleep(12.0)
ol.gpio_ctrl.mmio.write(0, 0)
print("    段 B 结束。对比两段，PL 需要补多少 dB？")
