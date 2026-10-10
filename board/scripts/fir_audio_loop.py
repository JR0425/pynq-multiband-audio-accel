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
#
# ⚠️ **原来这里写的是"做成真·实时要把核改成流式接口"—— 那个归因是错的。**
# 核这边早就没有障碍了：0.28 µs/采样（28 拍 @ 100 MHz）对 48 kHz 的 20.83 µs 预算，
# 余量 74 倍；而且延迟线**跨块保持**（第 5 节的分块连续性检验：分块跑和整块跑
# 逐位相同，0 错配），所以核本来就能一路流水地吃小段，不需要动它。
#
# 真正卡住的是**音频通路**：audio.record() 和 audio.play() 各自阻塞，
# 录的时候不播、播的时候不录 —— 半双工。半双工下块切多小都会"录一段、播一段"，
# 中间必然丢音。要改的是这一条，不是核。
#
# 能不能改成全双工，取决于那个自研音频 IP 内部有没有独立的收发 FIFO ——
# 这件事**搜不到，只能实测**：跑 board/scripts/audio_duplex_probe.py
# （它会把板上那份 pynq/lib/audio.py 的源码打出来，再量两边能不能并行）。
# 在探针的结论出来之前，这里维持分块写法。
# 对「出声 + 拍演示视频 + 量加速比」这三件事，分块这条路性价比高得多。
#
# 输入有两种取法，脚本自己挑：
#   ① 板上放着 board_input_48k.wav（由 src/python/export_board_input.py 生成）→ 用它。
#      跑多少遍都是同一串比特，参数组之间比出来的差别才只来自参数；
#      而且不碰麦克风那个口（板子上的自动耳机开关 U41 时不时插不进去）。
#   ② 没这个文件 → 退回现场录，麦克风 / 线路输入各试一遍取有声音的那个。
#
# 用法（板子上，要 root）：
#   echo xilinx | sudo -S env XILINX_XRT=/usr \
#     /usr/local/share/pynq-venv/bin/python3 fir_audio_loop.py

import os
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
CHUNK = int(os.environ.get("CHUNK", "8000"))   # 核 m_axi 的 -depth 是 8192，留一点余量

# 存到 **Jupyter 的根目录**里，不是 /home/xilinx —— 两者差一层，
# 而 Jupyter 的文件浏览器只能看到它自己的根（/home/xilinx/jupyter_notebooks），
# 看不到上一级。存到 /home/xilinx 的话，脚本跑完文件是有的，
# 但**在 Jupyter 里点不到、下不下来**，拍演示视频的时候会卡在这。
DIR = "/home/xilinx/jupyter_notebooks"
WAV_RAW = DIR + "/loop_1_录进来.wav"
WAV_DRY = DIR + "/loop_2_过核_直通.wav"
WAV_WET = DIR + "/loop_3_过核_压缩.wav"

# 固定输入。板上放了这个文件就用它，不再碰麦克风。
# 由 src/python/export_board_input.py 从 data/audio/real_voice.wav 生成：
# 48 kHz、单声道、int16，已经归一化到核的工作电平（板子上再算增益会得到 1.0000）。
# 置成空字符串就强制走现场录音。
INPUT_WAV = DIR + "/board_input_48k.wav"

# 录音电平低于这个值就当「没插麦克风 / 没声音」。
# 这个电平和下面打印的是同一个单位：int16 域（i32_to_i16_chan 里 >>8 过了），
# 满量程 32767，不是 24 位的 8.4e6。
# 实测（2026-10-02，耳机麦）：没检测到时本底只有 7～45；
# 正常说话 400～1200，峰值 2000～5400。取 200，两边各留一倍以上余量。
SILENT_RMS = 200.0

# 进核之前的输入增益匹配。
# 压缩器的门限是 0.1，也就是 int16 的 3276。耳机麦直接录进来很小
# （实测有效值 1084，满量程的 3.3%），够不到门限，压缩等于没做 ——
# 实测 1084 → 1081，1.00 倍。所以按有效值把录音抬到 TARGET_RMS，
# 再用峰值卡住不超过满量程的 90%，免得削顶。
TARGET_RMS = 0.15 * 32767.0

# 压缩器参数，Q1.15 整数，写进 AXI 寄存器 —— 改它不用重新综合。
# 出厂默认是阈值 0.1、压缩比 0.7。但 drc() 比的是**单个频段**的幅度，
# 不是整段信号：四段一分，每段只剩总能量的一小块，绝大多数样本够不到 0.1，
# drc 直接原样返回。实测人声只降 1.1 dB、合成信号 1.8 dB —— 都听不出来。
# 下面这组是按实测电平重定的。
THR_Q15   = 983          # 0.030 满量程
RATIO_Q15 = 16384        # 0.5
N_BANDS   = 4

# 留给耳朵挑的一组候选，从「几乎不压」排到「压得狠」。
# 4c 把它们的数字打出来，第 8 节再把它们逐个放出来 ——
# 「哪一组好听」是数字定不了的，只能听。
SWEEP = ((3277, 22938),   # 1 号：0.100 / 0.70，出厂默认，压得最轻
         (1638, 22938),   # 2 号：0.050 / 0.70
         (983,  22938),   # 3 号：0.030 / 0.70
         (983,  16384),   # 4 号：0.030 / 0.50 ← 现在文件里用的这组
         (656,  13107),   # 5 号：0.020 / 0.40
         (328,  9830))    # 6 号：0.010 / 0.30，压得最狠

# 电平台阶测试（第 9 节）。压缩器改的是「响的和轻的相对比例」，听感上本来就
# 不明显 —— 那是它的设计目标（保持响度），不是它坏了。所以听感之外要有一个
# 量出来的证据：喂一段电平一档一档往下走的信号，量每一档进核前、出核后各是多少。
#
# 四个正弦叠一起，频率分别落在四个频段里（段边界 500 / 1000 / 2000 Hz），
# 这样四段都被激励到。电平每 1 秒降 8 dB。
STEP_DB    = (0, -8, -16, -24, -32, -40)
STEP_SEC   = 1.0
STEP_TONES = (250.0, 700.0, 1400.0, 4000.0)
STEP_RMS   = 0.20        # 最响一档的有效值（满量程比例）
STEP_SKIP  = 0.10        # 每档开头丢掉的时间，避开滤波器和块切换的瞬态


def cr(v):
    """峰均比（峰值 / 有效值）。压缩器要压的就是它 —— 只看有效值会被
    「整体变轻」带偏，峰均比变小才说明动态范围真的被压窄了。"""
    v = v.astype(np.float64)
    r = rms(v)
    return float(np.abs(v).max()) / r if r else 0.0


ok_all = True
sweep = []                       # 4c 算出来的候选，第 8 节要放它们


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
#
# ⚠️ 收到的那一路还**带着一个大直流偏置**，见 fir_live.py 里 adc_to_i16 的注释。
# 这个函数只做格式换算、不去直流；喂核前要再去一次。

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


def load_wav16(path):
    """读回固定输入：48 kHz / 单声道 / int16。多声道就取平均，采样率不对就报错
    —— 不在这里悄悄重采样，那会让「板子的输入」和别的脚本的输入对不上。"""
    with wave.open(path, "rb") as f:
        fs = f.getframerate()
        ch = f.getnchannels()
        sw = f.getsampwidth()
        raw = f.readframes(f.getnframes())
    if fs != int(FS):
        raise SystemExit("%s 是 %d Hz，核按 %d Hz 设计 —— 先用 "
                         "src/python/export_board_input.py 转一遍" % (path, fs, int(FS)))
    if sw != 2:
        raise SystemExit("%s 每采样 %d 字节，要的是 2（int16）" % (path, sw))
    x = np.frombuffer(raw, dtype="<i2")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return np.round(x).astype(np.int16)


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
# 插耳机麦 → MIC；插线路输入 → LINE_IN。不知道插的是哪种，
# 于是两种都录一遍，取电平大的那个 —— 免得因为选错输入而录到一片静音，
# 却以为是通路坏了。
print("\n3) 取输入")

x = None
lvl = 0.0
rec_dt = 0.0
src_name = ""

if INPUT_WAV and os.path.exists(INPUT_WAV):
    x = load_wav16(INPUT_WAV)
    lvl = rms(x.astype(np.float64))
    rec_dt = len(x) / FS
    src_name = "固定文件"
    pk = float(np.abs(x).max())
    print("   用固定输入（不碰麦克风）：%s" % os.path.basename(INPUT_WAV))
    print("   %d 个采样 = %.2f 秒 @ %d Hz" % (len(x), len(x) / FS, int(FS)))
    print("   有效值 %.0f（满量程的 %.1f%%）、峰值 %d（%.1f%%）、峰均比 %.2f"
          % (lvl, 100.0 * lvl / 32768.0, int(pk), 100.0 * pk / 32768.0,
             pk / lvl if lvl else 0.0))
    note("跑多少遍都是同一串比特 —— 参数组之间比出来的差别才只来自参数。")
else:
    if INPUT_WAV:
        print("   没有 %s，退回现场录" % INPUT_WAV)
    # 输入有两种接法（板子上同一个 3.5mm 口，靠 codec 寄存器切换）：
    # 插耳机麦 → MIC；插线路输入 → LINE_IN。不知道插的是哪种，
    # 于是两种都录一遍，取电平大的那个 —— 免得因为选错输入而录到一片静音，
    # 却以为是通路坏了。
    print("   现场录 %.1f 秒（麦克风 / 线路输入各试一遍，取有声音的那个）"
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
    if src_name == "固定文件":
        note("** 固定输入几乎是静音 ** —— 文件本身是空的，或者放错了地方。")
    else:
        note("** 录音几乎是静音 ** —— 麦克风/线路输入没插东西，或者插了但是没声。")
        note("   插着耳机麦还报这个：把耳机**拔掉停两秒再插回去**，然后重跑。")
        note("   板子 HP+MIC 口里有颗自动耳机开关（U41），只在插头插入那一下")
        note("   做检测；板子上电时插头已经插在里面，它就检测不到麦克风。")
    note("   下面的算术检验照样全跑（静音也是合法的输入），")
    note("   但「压缩听出区别」这一条会被跳过 —— 静音压不出变化。")
    note("   第 5 节用合成信号补上这一条保证能看的效果。")
else:
    note("输入有信号，可以继续。")

save_wav24(WAV_RAW, x)
print("   这一段的原始输入已存：%s" % WAV_RAW)

# 进核之前做增益匹配，把输入抬到压缩器的工作区间。
# 直通和压缩用的是同一个 x，两边乘同一个增益，所以对比仍然成立。
# 用固定输入时这一步应当算出 1.0000 倍 —— 电平在生成那个文件时就定好了，
# 这里是拿同一个公式再算一遍对账（两边公式必须一致）。
if lvl >= SILENT_RMS:
    raw_rms = rms(x)
    raw_pk = float(np.abs(x).max())
    gain = min(TARGET_RMS / max(raw_rms, 1.0), 0.90 * 32767.0 / max(raw_pk, 1.0))
    x = np.clip(np.round(x.astype(np.float64) * gain), -32768, 32767).astype(np.int16)
    print("   增益匹配：有效值 %.0f、峰值 %d → 乘 %.4f 倍"
          % (raw_rms, int(raw_pk), gain))
    print("      （压缩器门限 0.1 = %d，信号不抬到这个量级根本够不到）"
          % int(0.1 * 32767))
    if src_name == "固定文件" and abs(gain - 1.0) > 1e-4:
        note("固定输入本该算出 1.0000 倍 —— 文件被改过，或者生成脚本的公式变了。")
else:
    print("   输入是静音，跳过增益匹配（放大了也只是噪声）")

# ---------------- 4. 过核 ----------------
print("\n4) 过核（这就是 ③d 的关键一步：声音进核了）")

print("   4a 直通（bypass=1，核只延迟不压缩）")
dry, dry_dt, dry_blocks = core.process(x, reset=True, bypass=True, chunk=CHUNK)
D = FirMultiband.GROUP_DELAY
ref = np.zeros_like(x)
ref[D:] = x[:len(x) - D]
mism = int(np.count_nonzero(dry != ref))
check("核的输出逐位等于「输入延后 %d 拍」" % D, mism == 0,
      "错配 %d / %d 个，切了 %d 块" % (mism, len(x), len(dry_blocks)))

print("   4b 压缩（阈值 %.3f、压缩比 %.2f，运行期参数）"
      % (THR_Q15 / 32768.0, RATIO_Q15 / 32768.0))
wet, wet_dt, wet_blocks = core.process(x, reset=True, bypass=False, chunk=CHUNK,
                                       thr=(THR_Q15,) * N_BANDS,
                                       ratio=(RATIO_Q15,) * N_BANDS)

diff = int(np.count_nonzero(wet != dry))
save_wav24(WAV_DRY, dry)
save_wav24(WAV_WET, wet)

if lvl >= SILENT_RMS:
    check("压缩真的起作用了（输出和直通不一样）", diff > 0,
          "不同的采样 %d / %d（%.1f%%）" % (diff, len(x), 100.0 * diff / len(x)))
    r_in, r_dry, r_wet = rms(x[D:]), rms(dry[D:]), rms(wet[D:])
    print("   有效值：录音 %.0f ｜ 直通 %.0f ｜ 压缩后 %.0f" % (r_in, r_dry, r_wet))
    print("   峰均比：直通 %.2f ｜ 压缩后 %.2f   （压缩器该压的是这个数）"
          % (cr(dry[D:]), cr(wet[D:])))
    check("压缩后整体变轻", r_wet < r_dry,
          "%.0f → %.0f（%.2f 倍，合 %.1f dB）"
          % (r_dry, r_wet, r_dry / r_wet if r_wet else 0,
             20.0 * np.log10(r_wet / r_dry) if r_dry and r_wet else 0.0))

    # 4c 参数扫描。阈值和压缩比是 AXI 寄存器，扫一遍不用重新综合 ——
    # 这也是这核的设计卖点（换一组参数就是换一种验配，不必重综合）。
    print("\n   4c 阈值/压缩比扫描（数字在这里，声音在第 8 节）")
    print("      %-20s %10s %10s %10s" % ("(阈值, 压缩比)", "有效值", "相对直通", "峰均比"))
    for thr, ratio in SWEEP:
        y, _, _ = core.process(x, reset=True, bypass=False, chunk=CHUNK,
                               thr=(thr,) * N_BANDS, ratio=(ratio,) * N_BANDS)
        r = rms(y[D:])
        sweep.append((thr, ratio, y))
        print("      (%5.3f, %.2f)%s %10.0f %9.1f dB %10.2f"
              % (thr / 32768.0, ratio / 32768.0,
                 " ←" if (thr, ratio) == (THR_Q15, RATIO_Q15) else "  ",
                 r, 20.0 * np.log10(r / r_dry) if r_dry else 0.0, cr(y[D:])))
    print("      「峰均比」从第 1 号往下应该越来越小 —— 那是动态范围被压窄，")
    print("      才是压缩器该干的事。有效值变小只是「整体变轻」，不算成绩。")
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
s_wet, s_dt, s_blocks = core.process(x_s, reset=True, bypass=False, chunk=CHUNK,
                                     thr=(THR_Q15,) * N_BANDS,
                                     ratio=(RATIO_Q15,) * N_BANDS)
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

if lvl >= SILENT_RMS and sweep:
    # A/B 用扫描里**最狠的一档**，不用默认那档。
    # 温和的几档互相之间只差零点几 dB（见 4c 的峰均比那列），
    # 拿它们 A/B 是白费功夫 —— 要比就比「完全不动」和「压得最狠」这两头。
    thr_ab, ratio_ab, wet_ab = sweep[-1]
    r_ab = rms(wet_ab[D:])
    print("   A/B 用的是扫描里最狠的一档（阈值 %.3f、压缩比 %.2f）："
          % (thr_ab / 32768.0, ratio_ab / 32768.0))
    print("   先放**原声**，停 1 秒，再放**压缩后**。")
    audio.sample_len = len(x)
    audio.buffer = i16_to_i32_stereo(x)
    t0 = time.time()
    audio.play()
    print("   原声放完，墙钟 %.2f 秒" % (time.time() - t0))
    time.sleep(1.0)
    audio.buffer = i16_to_i32_stereo(wet_ab)
    t0 = time.time()
    audio.play()
    print("   压缩后放完，墙钟 %.2f 秒" % (time.time() - t0))

    # 第三遍：把压缩后那一段补回等响度再放一遍。
    # 不加这一步，A/B 听出来的只是「第二遍变小了」——那是音量差，不是压缩。
    mk = r_dry / r_ab if r_ab else 1.0
    wf = wet_ab.astype(np.float64) * mk
    wet_m = np.clip(np.round(wf), -32768, 32767).astype(np.int16)
    over = int(np.count_nonzero(np.abs(wf) > 32767))
    time.sleep(1.0)
    audio.buffer = i16_to_i32_stereo(wet_m)
    t0 = time.time()
    audio.play()
    print("   等响度补回放完，墙钟 %.2f 秒（补 %.2f 倍 = +%.1f dB，削顶 %d 个点）"
          % (time.time() - t0, mk, 20.0 * np.log10(mk), over))
    print("   三遍：① 原声 ｜ ② 压缩后（整体变轻）｜ ③ 压缩后补回等响度")
    print("   要比的是 ① 和 ③ —— 一样响的前提下，③ 的大小声落差是不是更小。")
    print("   ② 只是音量小了，它不算证据。")
    save_wav24(DIR + "/loop_5_过核_压缩_最狠.wav", wet_m)
    print("   ① 和 ③ 都存了：loop_1_录进来.wav（=①）、loop_5_过核_压缩_最狠.wav（=③）")
else:
    print("   输入是静音（或没有参数档），A/B 就没得听了。")

# ---------------- 7. 计时 ----------------
print("\n7) 耗时")
budget = len(x) / FS
print("   这一段 %.1f 秒的声音，核跑了 %.4f 秒（直通 %.4f / 压缩 %.4f）"
      % (budget, wet_dt, dry_dt, wet_dt))
print("   每采样：直通 %.2f µs ｜ 压缩 %.2f µs"
      % (dry_dt / len(x) * 1e6, wet_dt / len(x) * 1e6))
note("这个时间**含** Python 的寄存器读写和 cache 维护开销，不是纯硬件速度。")
note("要拿它当加速比，得和「板子上 ARM 核跑同一套算法」比 —— 那是另一件事（见下）。")

# ---------------- 8. 逐个听参数 ----------------
# 4c 只给了数字。「哪一组好听」不是数字能定的 —— 得听。
# 这一段把候选逐个放出来：每一段都补回和直通一样的有效值再放，
# 所以听到的差别只来自「动态范围被压窄多少」，不是「音量变小」。
# 每一号前面先响几声提示音，几号就响几声，方便对号。
# 峰均比是「峰值/有效值」，两边同乘一个数它不变，所以这里和 4c 表里的数一致。
if lvl >= SILENT_RMS and sweep:
    print("\n8) 逐个听参数（几号就响几声提示音；每段响度已经和直通对齐）")
    print("   听到的差别不是音量，是「小声的地方抬起来多少、大声的地方压下去多少」。")
    print("   听完记住几号最好听，对照表在最下面。")

    for i, (thr, ratio, y) in enumerate(sweep, 1):
        r = rms(y[D:])
        mk = r_dry / r if r else 1.0
        yf = y.astype(np.float64) * mk
        over = int(np.count_nonzero(np.abs(yf) > 32767))
        ym = np.clip(np.round(yf), -32768, 32767).astype(np.int16)

        # 提示音：1 kHz 短音，几号响几声
        n_mark = int(FS * (0.05 + i * 0.08))
        mark = np.zeros(n_mark, dtype=np.int16)
        for k in range(i):
            a = int(FS * (0.03 + k * 0.08))
            b = a + int(FS * 0.05)
            tt = np.arange(b - a) / FS
            mark[a:b] = (0.30 * 32767.0
                         * np.sin(2 * np.pi * 1000.0 * tt)).astype(np.int16)
        audio.sample_len = len(mark)
        audio.buffer = i16_to_i32_stereo(mark)
        audio.play()
        time.sleep(0.30)

        audio.sample_len = len(ym)
        audio.buffer = i16_to_i32_stereo(ym)
        t0 = time.time()
        audio.play()

        p = DIR + "/param_%d_thr%.3f_r%.2f.wav" % (i, thr / 32768.0, ratio / 32768.0)
        save_wav24(p, ym)
        print("   %d 号  阈值 %.3f  压缩比 %.2f  ｜ 峰均比 %.2f  ｜ "
              "放了 %.2f 秒  ｜ 削顶 %d 点  ｜ %s"
              % (i, thr / 32768.0, ratio / 32768.0, cr(ym[D:]),
                 time.time() - t0, over, p.rsplit("/", 1)[-1]))
        time.sleep(0.45)

    print("\n   对照表 —— 挑好之后，把那一组填回本文件顶部的 THR_Q15 / RATIO_Q15：")
    for i, (thr, ratio, y) in enumerate(sweep, 1):
        print("     %d 号   THR_Q15 = %-5d  RATIO_Q15 = %-6d   (%.3f, %.2f)%s"
              % (i, thr, ratio, thr / 32768.0, ratio / 32768.0,
                 "   ← 现在文件里用的" if (thr, ratio) == (THR_Q15, RATIO_Q15) else ""))
else:
    print("\n8) 输入是静音，没得听 —— 换个有声音的输入再跑一次就有。")

# ---------------- 9. 电平台阶：把「压窄了多少」量出来 ----------------
# 第 8 节靠耳朵。但压缩器的设计目标就是**保持响度**，它改的是「响的和轻的
# 相对比例」，不是「听起来变了多少」—— 所以听不太出来是正常的，
# 不能把听感当成唯一的证据。
# 这一段喂一段电平一档一档往下走的信号，量每一档进核前、出核后各是多少。
# 报出来的是压缩器的输入-输出曲线，这是它性能的标准表征方式。
print("\n9) 电平台阶（量出压缩器的输入-输出曲线）")

n_step = int(FS * STEP_SEC)
skip = int(FS * STEP_SKIP)
parts = []
for db in STEP_DB:
    tt = np.arange(n_step) / FS
    s = np.zeros(n_step)
    for i, f in enumerate(STEP_TONES):
        s += np.sin(2 * np.pi * f * tt + 0.7 * i)      # 固定相位，不随机
    s /= len(STEP_TONES)
    parts.append(s * (STEP_RMS / np.sqrt(np.mean(s ** 2))) * 10.0 ** (db / 20.0))
x_st = np.clip(np.round(np.concatenate(parts) * 32768.0),
               -32768, 32767).astype(np.int16)
print("   %d 档 × %.1f 秒，每档降 %d dB，%d 个正弦（%s Hz）"
      % (len(STEP_DB), STEP_SEC, STEP_DB[0] - STEP_DB[1], len(STEP_TONES),
         " / ".join("%g" % f for f in STEP_TONES)))
print("   最响一档有效值 %.0f（满量程的 %.0f%%），全段峰值 %d（%.0f%%），不削顶"
      % (STEP_RMS * 32768, 100 * STEP_RMS,
         int(np.abs(x_st).max()), 100 * np.abs(x_st).max() / 32768))

st_dry, _, _ = core.process(x_st, reset=True, bypass=True, chunk=CHUNK)
st_thr, st_ratio = (sweep[-1][0], sweep[-1][1]) if sweep else (THR_Q15, RATIO_Q15)
st_wet, _, _ = core.process(x_st, reset=True, bypass=False, chunk=CHUNK,
                            thr=(st_thr,) * N_BANDS, ratio=(st_ratio,) * N_BANDS)


def step_rms(sig, k):
    return rms(sig[k * n_step + skip:(k + 1) * n_step])


in_r = [step_rms(x_st, k) for k in range(len(STEP_DB))]
dry_r = [step_rms(st_dry, k) for k in range(len(STEP_DB))]
wet_r = [step_rms(st_wet, k) for k in range(len(STEP_DB))]

# 补回等响度：和最响的那一档对齐。这样表里最后一列就是「相对最响档抬了多少」，
# 正好是压缩器在做的事。
g0 = dry_r[0] / wet_r[0] if wet_r[0] else 1.0
lifts = [20.0 * np.log10((wet_r[k] * g0) / dry_r[k]) if dry_r[k] and wet_r[k] else 0.0
         for k in range(len(STEP_DB))]

print("   用的是扫描里最狠那一档（阈值 %.3f、压缩比 %.2f），响度已对齐最响档"
      % (st_thr / 32768.0, st_ratio / 32768.0))
print("     %-7s %10s %10s %10s %12s" % ("档位", "输入", "直通", "压缩后", "相对直通"))
for k, db in enumerate(STEP_DB):
    print("     %+4d dB %10.0f %10.0f %10.0f %+11.1f dB"
          % (db, in_r[k], dry_r[k], wet_r[k] * g0, lifts[k]))

span_in = 20.0 * np.log10(in_r[0] / in_r[-1]) if in_r[-1] else 0.0
span_wet = 20.0 * np.log10(wet_r[0] / wet_r[-1]) if wet_r[-1] else 0.0
check("压缩把电平跨度压窄了", span_in - span_wet > 2.0,
      "输入跨 %.1f dB，输出跨 %.1f dB，压窄 %.1f dB"
      % (span_in, span_wet, span_in - span_wet))
mono = all(lifts[k] <= lifts[k + 1] + 0.2 for k in range(len(lifts) - 1))
check("越轻的档抬得越多（方向没反）", mono and lifts[-1] > 2.0,
      "最轻档 %+.1f dB；最响那一行的 0 是按定义来的（拿它当基准）" % lifts[-1])
print("   输入跨了 %.1f dB，输出只跨了 %.1f dB —— 压窄了 %.1f dB。"
      % (span_in, span_wet, span_in - span_wet))
note("这一列才是压缩器的成绩：它不改整体音量，改的是大小声之间的落差。")
note("最下面几档数值一样是正常的 —— 落到门限以下就不动了（硬拐点）。")

st_wet_m = np.clip(np.round(st_wet.astype(np.float64) * g0),
                   -32768, 32767).astype(np.int16)
save_wav24(DIR + "/step_1_直通.wav", st_dry)
save_wav24(DIR + "/step_2_压缩.wav", st_wet_m)
print("   存了两个（压缩那个已补回响度）：step_1_直通.wav、step_2_压缩.wav")

print("   放一遍（耳机）：先直通，停 1 秒，再压缩后补回响度。")
audio.sample_len = len(st_dry)
audio.buffer = i16_to_i32_stereo(st_dry)
audio.play()
time.sleep(1.0)
audio.sample_len = len(st_wet_m)
audio.buffer = i16_to_i32_stereo(st_wet_m)
audio.play()
print("   直通是每一档均匀往下掉；压缩后应该听到低的几档**没掉那么多**。")

print("\n" + "=" * 70)
print("结论：%s" % ("全过" if ok_all else "有失败项"))
print("wav 都在 %s/ 下（loop_*.wav / param_*.wav / step_*.wav），"
      "可以用 Jupyter 的文件列表下载下来听" % DIR)
print("=" * 70)
sys.exit(0 if ok_all else 1)
