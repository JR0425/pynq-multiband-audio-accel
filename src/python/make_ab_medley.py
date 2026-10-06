"""把六个候选参数拼成一个「串烧」文件，方便发给别人听。

为什么要有这个：
    board/scripts/fir_audio_loop.py 第 8 节在板子上逐个放，听完就没了；
    而且存下来是十个独立 wav，发出去别人得一个个切着听 —— 来回切换时
    音量、设备、注意力都在变，比不出东西。
    拼成一个文件：顺序固定、响度已经对齐，从头听到尾就行。

文件结构（响度全部对齐到基准）：
    三声低音提示           ← 后面这一段是「不压缩」的基准
    基准                    loop_2_过核_直通.wav
    一声高音提示 + 1 号     param_1_*.wav
    ...
    六声高音提示 + 6 号     param_6_*.wav

也可以手工指定要拼哪几段（这个模式下面每条提示音都是 1 kHz，
响几声就是第几段，第一段当响度基准）：
    python src/python/make_ab_medley.py <目录> --files loop_1_录进来.wav \
        loop_5_过核_压缩_最狠.wav --repeat 2
    → 一声提示 = 原声，两声提示 = 压缩后，整串听两遍。

怎么用（先把板子上的音频解压到一个文件夹）：
    python src/python/make_ab_medley.py <解压目录>
输出：
    <解压目录>/对比串烧.wav   48 kHz 单声道 16 位
"""

import argparse
import glob
import os
import re
import wave

import numpy as np

FS = 48000
I16_MAX = 32767

REF_PAT = "loop_2_*直通*.wav"
PARAM_PAT = "param_*.wav"


def read_wav16(path):
    """读 wav，返回 (采样率, int16 单声道)。

    板子上存的是 24 位双声道（codec 的格式）。24 位塞在 3 个字节里、
    低 8 位是零（写的时候是 int16 << 8），所以读回来就是 >>8 —— 和板子上
    24→16 那一步对称，不引入误差。
    """
    with wave.open(path, "rb") as f:
        fs, ch, sw = f.getframerate(), f.getnchannels(), f.getsampwidth()
        raw = f.readframes(f.getnframes())
    if sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)     # 24 位补符号
        v = v >> 8                                        # 24 位 → 16 位
    elif sw == 2:
        v = np.frombuffer(raw, dtype="<i2").astype(np.int32)
    else:
        raise SystemExit("%s 每采样 %d 字节，只认 2 或 3" % (path, sw))
    if ch > 1:
        v = v.reshape(-1, ch).mean(axis=1)
    return fs, np.round(v).astype(np.int16)


def rms(v):
    return float(np.sqrt(np.mean(np.asarray(v, dtype=np.float64) ** 2)))


def match(x, target_rms):
    """把 x 的响度对齐到 target_rms，峰值不许超过满量程的 90%。"""
    r = rms(x)
    pk = float(np.abs(x).max())
    g = min(target_rms / max(r, 1.0), 0.90 * I16_MAX / max(pk, 1.0))
    return np.clip(np.round(x.astype(np.float64) * g), -I16_MAX - 1, I16_MAX).astype(np.int16), g


def beeps(n, freq, out_len_s):
    """n 声短提示音，拼成一段 int16。"""
    seg = np.zeros(int(FS * out_len_s), dtype=np.int16)
    for k in range(n):
        a = int(FS * (0.03 + k * 0.08))
        b = min(len(seg), a + int(FS * 0.05))
        if b <= a:
            break
        t = np.arange(b - a) / FS
        seg[a:b] = (0.28 * I16_MAX * np.sin(2 * np.pi * freq * t)).astype(np.int16)
    return seg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", help="audio_material.zip 解压出来的目录")
    ap.add_argument("-o", "--out", default="对比串烧.wav")
    ap.add_argument("--tail", type=float, default=0.45,
                    help="每个片段之间静音多长（秒）")
    ap.add_argument("--files", nargs="+", default=None,
                    help="按这个顺序拼（相对 dir 的文件名）。不给就走参数串烧模式。")
    ap.add_argument("--repeat", type=int, default=1,
                    help="整串从头再来几遍（--files 模式下让对方多听几轮）")
    args = ap.parse_args()

    d = args.dir
    # 每一段：(文件, 说明, 提示音数, 提示音频率)
    if args.files:
        plan = [(os.path.join(d, f), os.path.basename(f), i + 1, 1000.0)
                for i, f in enumerate(args.files)]
        for p, _, _, _ in plan:
            if not os.path.exists(p):
                raise SystemExit("找不到 %s" % p)
        first_note = "第一段是基准（后面每一段的响度都对齐到它）"
    else:
        refs = sorted(glob.glob(os.path.join(d, REF_PAT)))
        params = sorted(glob.glob(os.path.join(d, PARAM_PAT)),
                        key=lambda p: int(re.search(r"param_(\d+)", p).group(1)))
        if not refs:
            raise SystemExit("没找到基准 %s —— 解压目录对吗？" % REF_PAT)
        if not params:
            raise SystemExit("没找到 %s —— 解压目录对吗？" % PARAM_PAT)
        plan = [(refs[0], "基准 不压缩", 3, 500.0)]
        plan += [(p, os.path.basename(p),
                  int(re.search(r"param_(\d+)", p).group(1)), 1000.0) for p in params]
        first_note = "最前面三声低音 = 不压缩的基准，后面几号就响几声"

    fs, ref = read_wav16(plan[0][0])
    if fs != FS:
        raise SystemExit("基准是 %d Hz，要的是 %d Hz" % (fs, FS))
    target = rms(ref)
    print("基准：%s" % os.path.basename(plan[0][0]))
    print("       %d 个采样 = %.2f 秒，有效值 %.0f（满量程的 %.1f%%）"
          % (len(ref), len(ref) / FS, target, 100 * target / 32768))
    print("每一段的响度都对到这一条，所以听到的差别只有动态范围。")
    print("%s。\n" % first_note)

    gap = np.zeros(int(FS * args.tail), dtype=np.int16)

    def build():
        pieces = []
        for p, label, n, freq in plan:
            fs2, x = read_wav16(p)
            if fs2 != FS:
                raise SystemExit("%s 是 %d Hz" % (p, fs2))
            y, g = match(x, target)
            m = re.search(r"thr([\d.]+)_r([\d.]+)\.wav", label)
            tag = ("阈值 %s  压缩比 %s" % (m.group(1), m.group(2))) if m else label
            print("  %d 声提示  %-32s 补 %.2f 倍  ｜ 峰均比 %.2f"
                  % (n, tag, g, float(np.abs(y).max()) / max(rms(y), 1.0)))
            pieces += [beeps(n, freq, 0.05 + n * 0.08), y, gap]
        return pieces

    pieces = []
    for r in range(max(1, args.repeat)):
        if r:
            print("  —— 第 %d 遍 ——" % (r + 1))
        pieces += build()

    out = np.concatenate(pieces)
    path = os.path.join(d, args.out)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(FS)
        f.writeframes(out.astype("<i2").tobytes())

    print("\n已写出：%s" % path)
    print("        %.1f 秒，%.1f MB" % (len(out) / FS, os.path.getsize(path) / 1e6))
    print("\n发给别人的时候把这张表一起发：")
    print("  %s。" % first_note)
    print("  听法：盯住同一句话的开头或结尾（音量轻下去的地方），")
    print("        看是不是更容易听清，同时底噪没有变响。")
    print("  带着耳机听，别外放，别转成微信语音。")


if __name__ == "__main__":
    main()
