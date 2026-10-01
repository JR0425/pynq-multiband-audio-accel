"""把第 2 步那声"嘟"连放三遍 —— 只为了让人听得见。

为什么不直接再跑一遍 verify_own_overlay_audio.py：
    那个脚本每次都重新加载一遍 bit（十来秒），只换一次 3 秒的响。
    这里把 overlay 载一次，然后用同一个缓冲区连放三遍，中间停 1 秒。

    缓冲区是 IP 自己的 —— load() 已经把样本灌进去了，play() 只是让它从头放一遍，
    所以连放不需要重新读文件，也不会重复走 I2C 配置。

用法（板子上，串口控制台要 sudo）：

    echo xilinx | sudo -S env XILINX_XRT=/usr \
        /usr/local/share/pynq-venv/bin/python3 replay_own_overlay_tone.py
"""
import os
import sys
import time

from pynq import Overlay

from verify_audio_playback import VOLUME, WAV_PATH, make_tone

BITFILE = "audio.bit"
TIMES = 3
GAP_S = 1.0

if not os.path.exists(WAV_PATH):
    print("生成 %s ..." % WAV_PATH)
    make_tone(WAV_PATH)

print("加载 %s ..." % BITFILE)
ol = Overlay(BITFILE)

audio = ol.audio_codec_ctrl_0
audio.configure()
audio.set_volume(VOLUME)
audio.load(WAV_PATH)
print("音量 %d，准备连放 %d 遍（每遍 3 秒，间隔 %.0f 秒）" % (VOLUME, TIMES, GAP_S))
print(">>> 听 <<<")

for i in range(1, TIMES + 1):
    t0 = time.time()
    audio.play()
    print("  第 %d 遍放完（%.2f 秒）" % (i, time.time() - t0))
    if i != TIMES:
        time.sleep(GAP_S)

print("三遍都放完了。")
sys.exit(0)
