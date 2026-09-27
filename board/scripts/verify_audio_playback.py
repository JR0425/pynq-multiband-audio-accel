"""W2 里程碑：让板子出声，并量出「搬运是 CPU 干的」。

为什么要有这个脚本：
    W2 的目标只有一句话 —— 对着麦克风说话，耳机里能听到。
    但「听到了」没法写进仓库，所以这里把它拆成两件可复现的事：
        ① 放一段数学上已知干净的正弦波，出声 = 通路是活的
        ② 用 CPU 时间证明样本是谁搬的（这决定了自研核要替掉什么）

    顺带解决一个排查问题：手上那段 recording_0.wav 底噪重、电平只有满量程的 25%，
    分不清杂音是板子的还是素材的。换成自己生成的正弦波，如果还是听到杂音，
    问题就一定在板子这一侧。

用法（在 PYNQ 板子上跑）：

    # 从 Jupyter 里（Jupyter 服务本身就是 root 跑的）
    %run verify_audio_playback.py

    # 从串口控制台（xilinx 用户）—— 必须加 sudo，原因见下面「踩坑」
    echo xilinx | sudo -S env XILINX_XRT=/usr \
        /usr/local/share/pynq-venv/bin/python3 verify_audio_playback.py

实测（2026-09-27，出厂 base overlay，音量 62）：

    播放 3 秒 wav  →  墙钟 2.96 s   CPU 2.96 s
    录音 3 秒      →  墙钟 2.96 s   CPU 2.95 s

    芯片是双核，所以这等于「一个核被 100% 占满」。
    如果样本是 FPGA 搬的，CPU 应该几乎不花时间 —— 结论：搬运是 CPU 在扛。

踩坑（都实测过）：

    1. 碰 MMIO 就要 root。/dev/mem 属 root:kmem，而 xilinx 用户在 xilinx/adm/sudo
       组里、不在 kmem，所以同一份代码在 Jupyter 里能跑、在串口控制台报
       OSError: Root permissions required. —— 差别不在代码，在「谁在跑」。
    2. set_volume 的上限是 62。源码报错信息写的是 "[0,63]"，但判断是
       `0 <= volume < 63`，所以写 63 直接 ValueError。
    3. load() 只吃 24 bit / 双声道 / 48 kHz 的 wav —— 它是按每个采样 3 字节解包的。
    4. record() 只接受 (0, 60] 秒，而且不带文件名参数；存文件要用 save()。
"""
import math
import os
import resource
import struct
import time
import wave

from pynq.overlays.base import BaseOverlay

SAMPLE_RATE = 48000          # codec 固定 48 kHz，不要改
TONE_HZ = 440.0              # A4，听得最清楚
DURATION_S = 3.0
WAV_PATH = "/home/xilinx/w2_tone440.wav"
REC_PATH = "/home/xilinx/w2_recording.wav"
VOLUME = 62                  # 62 是允许的最大值（不是 63）


def make_tone(path, hz=TONE_HZ, seconds=DURATION_S, rate=SAMPLE_RATE, amp=0.5):
    """写一个干净的 24 bit / 双声道 / 48 kHz 正弦波 wav。

    amp 取 0.5（满量程一半）：比手上那段录音（只有 25%）响得多，
    又留足余量不会削顶。
    """
    peak = int(amp * (2 ** 23 - 1))
    n = int(rate * seconds)
    frames = bytearray()
    for i in range(n):
        v = int(peak * math.sin(2 * math.pi * hz * i / rate))
        raw = struct.pack("<i", v)[:3]       # 小端 24 bit，取低 3 字节
        frames += raw + raw                  # 左右声道放同一份
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(3)
        f.setframerate(rate)
        f.writeframes(bytes(frames))
    return path


def measure(fn, *args):
    """跑一次 fn，返回 (墙钟秒数, 进程 CPU 秒数)。

    ru_utime / ru_stime 是进程「累计」值，必须先取基准再差分。
    直接读会把之前所有的工作（比如加载 overlay）一起算进来，
    算出过 "墙钟 2.96 s / CPU 13.21 s" 这种超过双核上限的荒谬数。
    """
    r0 = resource.getrusage(resource.RUSAGE_SELF)
    t0 = time.time()
    fn(*args)
    wall = time.time() - t0
    r1 = resource.getrusage(resource.RUSAGE_SELF)
    cpu = (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime)
    return wall, cpu


def main():
    if not os.path.exists(WAV_PATH):
        print("生成测试音频 %s ..." % WAV_PATH)
        make_tone(WAV_PATH)

    # 板子开机时出厂固件已经把 base overlay 灌好了，这里只是取个句柄
    audio = BaseOverlay("base.bit").audio
    audio.set_volume(VOLUME)
    audio.load(WAV_PATH)
    print("已加载 %s，音量 %d" % (WAV_PATH, VOLUME))

    print("播放 %g 秒 %g Hz 正弦波（应该听到一声干净的蜂鸣）..." % (DURATION_S, TONE_HZ))
    wall, cpu = measure(audio.play)
    print("  播放   墙钟 %.2f 秒   CPU %.2f 秒" % (wall, cpu))

    print("录音 %g 秒（内容取决于 HP+Mic 口插的是什么，这里只验通路）..." % DURATION_S)
    wall, cpu = measure(audio.record, DURATION_S)
    print("  录音   墙钟 %.2f 秒   CPU %.2f 秒" % (wall, cpu))
    audio.save(REC_PATH)
    print("  已存到 %s" % REC_PATH)

    print()
    print("判读：CPU 时间 ≈ 墙钟时间 × 1，说明一个核被占满了 ——")
    print("      样本是 CPU 一个一个搬的，FPGA 只做 I2S 时序和 codec 配置。")
    print("      这正是本项目要用 DMA + 自研滤波核替掉的那一段。")


if __name__ == "__main__":
    main()
