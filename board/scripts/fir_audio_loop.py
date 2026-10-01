# ③d：让声音真的经过核 —— 麦克风 → 核 → 耳机
#
# 前面几步各证了一件事：核自己算得对（csim / 上板直通检验）、板子会出声、我们的 bit 会出声。
# 但核一直只吃「Python 塞进去的数组」—— 和真实声音没关系。
# 这一步把两条线接上：**录进来的真实声音，走核，再从耳机出去**。
#
# 数据怎么走（全程在这块板子上，不经过电脑）：
#
#   ① codec 把麦克风的模拟声音变成数字，存进 PL 里的缓冲区
#   ② audio.record() → 一块 int32 缓冲（交织立体声，每个采样 24 位有效）
#   ③ 24 位 → 16 位（右移 8 位送掉低 8 位）→ 核能吃的 int16
#   ④ 核：Python 把地址写进寄存器，核自己去 DDR 读、算完写回
#   ⑤ 16 位 → 24 位（左移 8 位）→ 塞回 audio.buffer → audio.play() → 耳机
#
# 为什么不用重跑 Vivado：现在的 fir.bit 里音频 IP 和核**都在**。
# pynq.lib.audio 的 record() / play() 只认那个音频 IP，核是另外一坨，
# 两件事在 Python 里就能串起来，不需要改硬件。
#
# 这一段的取舍：一次处理一块（3 秒），不是「边录边放」的实时流。
# 做成真·实时要把核改成流式接口，那会把已经验完的仿真 / 拍数 / 时序全部作废。
# 对「出声 + 拍演示视频 + 量加速比」这三件事，分块这条路性价比高得多。
#
# 用法（板子上，要 root）：
#   echo xilinx | sudo -S env XILINX_XRT=/usr \
#     /usr/local/share/pynq-venv/bin/python3 fir_audio_loop.py

import sys
import time
import wave

import numpy as np
from pynq import Overlay

from fir_core import FirMultiband

BITFILE = "fir.bit"
FS = 48000.0
REC_SECONDS = 3.0
VOLUME = 62                      # 62 是上限（源码写 "[0,63)"）
CHUNK = 8000                     # 核 m_axi 的 -depth 是 8192，留一点余量

# 存到 **Jupyter 的根目录**里，不是 /home/xilinx —— 两者差一层，
# 而 Jupyter 的文件浏览器只能看到它自己的根（/home/xilinx/jupyter_notebooks），
# 看不到上一级。存到 /home/xilinx 的话，脚本跑完文件是有的，
# 但**在 Jupyter 里点不到、下不下来**，拍演示视频的时候会卡在这。
DIR = "/home/xilinx/jupyter_notebooks"
WAV_RAW = DIR + "/loop_1_录进来.wav"
WAV_DRY = DIR + "/loop_2_过核_直通.wav"
WAV_WET = DIR + "/loop_3_过核_压缩.wav"

# 录音电平低于这个值就当「没插麦克风 / 没声音」。
# 满量程 2^23 ≈ 8.4e6，这个阈值约等于满量程的千分之一。
SILENT_RMS = 8000.0

ok_all = True


def check(name, passed, detail=""):
    global ok_all
    ok_all = ok_all and passed
    print("  [%s] %s%s" % ("过  " if passed else "没过", name,
                           ("  —— " + detail) if detail else ""))
    return passed


def note(msg):
    print("        " + msg)


# ---------------- 24 位 ⇄ 16 位 ----------------
# codec 每个采样是 24 位，塞在 int32 的低 3 字节里。
# 核是 Q1.15 的 int16。两头都差 8 位，所以就是移 8 位 —— 不引入任何误差，
# 丢掉的只是最后 8 位精度（48 dB 底噪，远低于 codec 自己的本底）。

def i32_to_i16_chan(buf, ch):
    return (buf.reshape(-1, 2)[:, ch] >> 8).astype(np.int16)


def i16_to_i32_stereo(x16):
    """16 位单声道 → audio.buffer 要的 int32 交织立体声（左右同一份）。"""
    v = x16.astype(np.int32) << 8
    out = np.empty(v.size * 2, dtype=np.int32)
    out[0::2] = v
    out[1::2] = v
    return out


def save_wav24(path, x16):
    """存成 codec 认的格式：24 位 / 双声道 / 48 kHz。"""
    x32 = x16.astype(np.int32) << 8
    stereo = np.repeat(x32, 2)                      # 左右声道同份
    raw = stereo.astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(3)
        f.setframerate(int(FS))
        f.writeframes(raw)


def rms(x):
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


# ---------------- 1. overlay ----------------
print("=" * 70)
print("③d：麦克风 → 核 → 耳机")
print("=" * 70)

print("\n1) 加载 %s" % BITFILE)
ol = Overlay(BITFILE)
have = sorted(n for n in ol.ip_dict if "fir" in n or "audio" in n)
print("   相关的 IP：", have)
check("核在 overlay 里", "fir_multiband_0" in ol.ip_dict)
check("音频 IP 在 overlay 里", "audio_codec_ctrl_0" in ol.ip_dict)
if "fir_multiband_0" not in ol.ip_dict or "audio_codec_ctrl_0" not in ol.ip_dict:
    sys.exit(1)

audio = ol.audio_codec_ctrl_0
core = FirMultiband(ol.fir_multiband_0.mmio)
print("   核地址 %s   音频 IP 地址 %s"
      % (hex(ol.ip_dict["fir_multiband_0"]["phys_addr"]),
         hex(ol.ip_dict["audio_codec_ctrl_0"]["phys_addr"])))

# ---------------- 2. 配 codec ----------------
print("\n2) 配 codec")
audio.configure()                    # 默认 iic_index=1 / uio_name='audio-codec-ctrl'
audio.set_volume(VOLUME)
check("configure + 音量 %d" % VOLUME, True)

# ---------------- 3. 录音 ----------------
# 输入有两种接法（板子上同一个 3.5mm 口，靠 codec 寄存器切换）：
# 插耳机麦 → MIC；插线路输入 → LINE_IN。不知道用户插的是哪种，
# 于是两种都录一遍，取电平大的那个 —— 免得因为选错输入而录到一片静音，
# 却以为是通路坏了。
print("\n3) 录音 %.1f 秒（麦克风 / 线路输入各试一遍，取有声音的那个）"
      % REC_SECONDS)

tries = []

for name, sel in (("LINE_IN", audio.select_line_in),
                  ("MIC", audio.select_microphone)):
    sel()
    time.sleep(0.3)
    t0 = time.time()
    audio.record(REC_SECONDS)
    dt = time.time() - t0

    st = audio.buffer.reshape(-1, 2).astype(np.float64)
    e_l = float(np.mean(st[:, 0] ** 2))
    e_r = float(np.mean(st[:, 1] ** 2))
    ch = 0 if e_l >= e_r else 1
    x = i32_to_i16_chan(audio.buffer, ch)
    tries.append((rms(x.astype(np.float64)), name, ch, x, dt))
    print("   %-8s 录了 %.2f 秒  左/右能量 %10.0f / %-10.0f  取%s声道  有效值 %8.0f"
          % (name, dt, e_l, e_r, "LR"[ch], tries[-1][0]))

lvl, src_name, ch, x, rec_dt = max(tries, key=lambda t: t[0])
print("   → 用 %s 这一遍" % src_name)

if lvl < SILENT_RMS:
    note("** 录音几乎是静音 ** —— 麦克风/线路输入没插东西，或者插了但是没声。")
    note("   下面的算术检验照样全跑（静音也是合法的输入），")
    note("   但「压缩听出区别」这一条会被跳过 —— 静音压不出变化。")
    note("   第 5 节用合成信号补上这一条保证能看的效果。")
else:
    note("录音有信号，可以继续。")

save_wav24(WAV_RAW, x)
print("   原始录音已存：%s" % WAV_RAW)

# ---------------- 4. 过核 ----------------
print("\n4) 过核（这就是 ③d 的关键一步：声音进核了）")

print("   4a 直通（bypass=1，核只延迟不压缩）")
dry, dry_dt, dry_blocks = core.process(x, reset=True, bypass=True, chunk=CHUNK)
D = FirMultiband.GROUP_DELAY
ref = np.zeros_like(x)
ref[D:] = x[:len(x) - D]
mism = int(np.count_nonzero(dry != ref))
check("核的输出逐位等于「录音延后 %d 拍」" % D, mism == 0,
      "错配 %d / %d 个，切了 %d 块" % (mism, len(x), len(dry_blocks)))

print("   4b 压缩（默认阈值 0.1、压缩比 0.7）")
wet, wet_dt, wet_blocks = core.process(x, reset=True, bypass=False, chunk=CHUNK)

diff = int(np.count_nonzero(wet != dry))
save_wav24(WAV_DRY, dry)
save_wav24(WAV_WET, wet)

if lvl >= SILENT_RMS:
    check("压缩真的起作用了（输出和直通不一样）", diff > 0,
          "不同的采样 %d / %d（%.1f%%）" % (diff, len(x), 100.0 * diff / len(x)))
    r_in, r_dry, r_wet = rms(x[D:]), rms(dry[D:]), rms(wet[D:])
    print("   有效值：录音 %.0f ｜ 直通 %.0f ｜ 压缩后 %.0f" % (r_in, r_dry, r_wet))
    check("压缩后整体变轻", r_wet < r_dry,
          "%.0f → %.0f（%.2f 倍）" % (r_dry, r_wet, r_dry / r_wet if r_wet else 0))
else:
    print("   跳过「压缩有区别」这一条（录音是静音，见上面那行提示）")

# ---------------- 5. 合成信号的那一遍 ----------------
# 麦克风接没接，这一步都能跑 —— 它保证有一段"能听见压缩效果"的音频。
# 同时也是给演示视频用的素材（干净的 300 Hz + 3000 Hz 双音，压缩前后差别明显）。
print("\n5) 合成信号过核（不依赖麦克风，保证有一段可听的效果）")
n = int(FS * REC_SECONDS)
t = np.arange(n) / FS
synth = (0.55 * np.sin(2 * np.pi * 300.0 * t)
         + 0.35 * np.sin(2 * np.pi * 3000.0 * t))
x_s = np.clip(np.round(synth * 32767.0), -32768, 32767).astype(np.int16)

s_dry, _, _ = core.process(x_s, reset=True, bypass=True, chunk=CHUNK)
s_wet, s_dt, s_blocks = core.process(x_s, reset=True, bypass=False, chunk=CHUNK)
s_diff = int(np.count_nonzero(s_wet != s_dry))
r_sd, r_sw = rms(s_dry[D:]), rms(s_wet[D:])
print("   输入有效值 %.0f ｜ 直通 %.0f ｜ 压缩后 %.0f" % (rms(x_s), r_sd, r_sw))
check("合成信号上压缩有效", s_diff > 0 and r_sw < r_sd,
      "不同 %d 个，有效值 %.0f → %.0f" % (s_diff, r_sd, r_sw))
save_wav24(DIR + "/loop_4_合成_压缩.wav", s_wet)

# ---------------- 6. 放出来 ----------------
print("\n6) 放出来（耳机！）")
print("   先放压缩后的合成信号（差别最明显），再放录音那一段。")

audio.sample_len = len(s_wet)
audio.buffer = i16_to_i32_stereo(s_wet)
t0 = time.time()
audio.play()
print("   合成（压缩后）放完，墙钟 %.2f 秒" % (time.time() - t0))

if lvl >= SILENT_RMS:
    print("   现在 A/B 对比：先放**原声**，停 1 秒，再放**压缩后**。")
    audio.sample_len = len(x)
    audio.buffer = i16_to_i32_stereo(x)
    t0 = time.time()
    audio.play()
    print("   原声放完，墙钟 %.2f 秒" % (time.time() - t0))
    time.sleep(1.0)
    audio.buffer = i16_to_i32_stereo(wet)
    t0 = time.time()
    audio.play()
    print("   压缩后放完，墙钟 %.2f 秒" % (time.time() - t0))
    print("   ⚠️ 这一条只有耳朵能判：第二遍是不是比第一遍「平」一些？")
    print("      （应该听到：小声的地方被抬起来、大声的地方被压下去，整体不那么忽大忽小）")
else:
    print("   录音是静音，A/B 就没得听了 —— 插上麦克风再跑一次就有。")

# ---------------- 7. 计时 ----------------
print("\n7) 耗时")
budget = len(x) / FS
print("   录音 %.1f 秒的声音，核跑了 %.4f 秒（直通 %.4f / 压缩 %.4f）"
      % (REC_SECONDS, wet_dt, dry_dt, wet_dt))
print("   每采样：直通 %.2f µs ｜ 压缩 %.2f µs"
      % (dry_dt / len(x) * 1e6, wet_dt / len(x) * 1e6))
note("这个时间**含** Python 的寄存器读写和 cache 维护开销，不是纯硬件速度。")
note("要拿它当加速比，得和「板子上 ARM 核跑同一套算法」比 —— 那是另一件事（见下）。")

print("\n" + "=" * 70)
print("结论：%s" % ("全过" if ok_all else "有失败项"))
print("wav 都在 %s/loop_*.wav（可以用 Jupyter 的文件列表下载下来听）" % DIR)
print("=" * 70)
sys.exit(0 if ok_all else 1)
