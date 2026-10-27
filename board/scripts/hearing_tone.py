# -*- coding: utf-8 -*-
"""测听用的放音助手：加载一次 overlay，之后按行读命令、放一个测试音。

为什么单独起一个进程，而不是在 fir_webui 里直接 import pynq
------------------------------------------------------------
`Overlay()` 会把 `/dev/uio*` 打开并一直握着。fir_live.py 每次起跑也走
`Overlay(BIT)`，两个进程同时握着同一个 UIO，后一个加载位流大概率被
内核拒掉（Device or resource busy）。所以放音放在子进程里：
webui 要起实时之前先把它杀掉，句柄随进程退出一起释放。

协议（stdin 一行一条，stdout 一行一条 JSON）
--------------------------------------------
    "250 -20 1.2"   → 放 250 Hz、-20 dBFS、1.2 秒
    返回 {"ok": true, "freq_hz": 250.0, "level_db": -20.0}
    "quit"          → 退出

电平定义：-20 dBFS = 满量程幅度的 10%。
音量取 HEAR_VOLUME（默认 54，和 fir_live.py、fir_audio_loop.py 的默认音量一致），
这样测听时听到的响度和之后实时通路是同一档。

用法（只由 fir_webui.py 起，不手工跑）：

    python3 hearing_tone.py
"""
import json
import math
import os
import struct
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
VOLUME = int(os.environ.get("HEAR_VOLUME", "54"))
FS = 48000
TONE_PATH = "/dev/shm/hear_tone.wav"


def _find_bit():
    """和 fir_live.py 一样：cwd → 脚本目录 → ../overlay。"""
    cands = [os.environ.get("FIR_BIT"),
             os.path.join(os.getcwd(), "fir.bit"),
             os.path.join(HERE, "fir.bit"),
             os.path.join(HERE, "..", "overlay", "fir.bit")]
    for c in cands:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return None


def _write_tone(path, hz, level_db, seconds):
    """24 bit / 双声道 / 48 kHz 正弦，和 verify_audio_playback.make_tone 同一格式。"""
    amp = 10.0 ** (level_db / 20.0)
    peak = int(amp * (2 ** 23 - 1))
    n = int(FS * seconds)
    frames = bytearray()
    for i in range(n):
        v = int(peak * math.sin(2.0 * math.pi * hz * i / FS))
        raw = struct.pack("<i", v)[:3]
        frames += raw + raw
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(3)
        f.setframerate(FS)
        f.writeframes(bytes(frames))
    return path


def _reply(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    os.environ.setdefault("XILINX_XRT", "/usr")
    try:
        from pynq import Overlay
    except Exception as e:                       # noqa: BLE001
        _reply({"ok": False, "msg": "import pynq 失败：%r" % (e,)})
        return 1
    bit = _find_bit()
    if not bit:
        _reply({"ok": False, "msg": "找不到 fir.bit（cwd=%s）" % os.getcwd()})
        return 1
    try:
        ol = Overlay(bit)
        audio = ol.audio_codec_ctrl_0
        audio.configure()
        audio.set_volume(VOLUME)
    except Exception as e:                       # noqa: BLE001
        _reply({"ok": False, "msg": "加载 overlay / 配 codec 失败：%r" % (e,)})
        return 1
    _reply({"ok": True, "msg": "ready", "volume": VOLUME, "bit": bit})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line == "quit":
            break
        try:
            parts = line.split()
            hz = float(parts[0])
            level_db = float(parts[1])
            sec = float(parts[2]) if len(parts) > 2 else 1.2
            if not (20.0 <= hz <= 20000.0):
                raise ValueError("频率 %g 超出 20~20000 Hz" % hz)
            if not (-90.0 <= level_db <= 0.0):
                raise ValueError("电平 %g 超出 -90~0 dBFS" % level_db)
            if not (0.2 <= sec <= 5.0):
                raise ValueError("时长 %g 超出 0.2~5 秒" % sec)
            _write_tone(TONE_PATH, hz, level_db, sec)
            audio.load(TONE_PATH)
            audio.play()
            _reply({"ok": True, "freq_hz": hz, "level_db": level_db,
                    "sec": sec})
        except Exception as e:                   # noqa: BLE001
            _reply({"ok": False, "msg": "放音失败：%r" % (e,)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
