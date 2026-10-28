# 板上 WAV 播放器（批 1 环形波表 BRAM 版，定速填充）
#
# 用法：
#   nohup python3 ply_upload.py <wav> [db] > /tmp/ply.log 2>&1 &
#   python3 ply_upload.py --stop     切回麦克风
#
# 环形 BRAM 16384 字（64 KB）。RTL 每采样读一字（48 kHz），
# PS 侧必须以同样速率填充：每 21.33 ms 写 1024 字（16 个_chunk 绕环一圈），
# 用绝对时刻调度防漂移；偶尔迟到只重复 ~21 ms，听不出来。
import sys
import threading
import time
import wave

import numpy as np
from pynq import Overlay, MMIO
import dma_audio_probe as P

BRAM_BASE = 0x43C50000
RING = 16384          # 字
CHUNK = 1024          # 每次写 1024 字 = 4 KB
CHUNK_T = CHUNK / 48000.0   # 21.33 ms
REGS = 0x43C40000
BANK0 = 0x100


def main():
    args = sys.argv[1:]
    ol = Overlay("/home/xilinx/dma_exp/fir_pl.bit")
    time.sleep(0.3)
    P.codec_init()

    regs = MMIO(REGS, 0x1000)
    bram = MMIO(BRAM_BASE, 0x10000)
    ctrl = None
    for name, d in ol.ip_dict.items():
        if name.startswith("gpio_ctrl"):
            ctrl = MMIO(d["phys_addr"], 0x1000)

    if args and args[0] == "--stop":
        regs.write(0x024, 0)          # PLY_CTL en=0
        ctrl.write(0, 0x03)           # run|drc，源回麦克风
        print("stopped, back to mic")
        return

    wav_path = args[0]
    db = float(args[1]) if len(args) > 1 else 30.0

    w = wave.open(wav_path, "rb")
    print("wav: ch=%d sr=%d n=%d" % (w.getnchannels(), w.getframerate(), w.getnframes()))
    raw = w.readframes(w.getnframes())
    nch = w.getnchannels()
    w.close()

    n_samples = len(raw) // 2
    smp = np.frombuffer(raw[: n_samples * 2], dtype="<i2")
    if nch == 2:
        smp = smp[0::2]
    smp = smp.astype(np.int64)
    print("song: %d samples (%.1f s)" % (len(smp), len(smp) / 48000.0))

    # 增益 + 斜坡
    g = int(round(256 * 10 ** (db / 20.0)))
    for i in range(4):
        regs.write(BANK0 + 4 * i, g)
    regs.write(0x170, 5)

    # 预填整环：song[0:16384]
    def words_of(seg):
        return (np.asarray(seg, dtype=np.uint64) & 0xFFFF).astype(np.uint32) << 16

    bram.write(0, words_of(smp[:RING]).tobytes())

    stop_ev = threading.Event()

    def feeder():
        pos = RING                     # 已填 0..16383，下一个要写的 song 下标
        next_t = time.time() + CHUNK_T
        while not stop_ev.is_set():
            idx = np.arange(pos, pos + CHUNK) % len(smp)
            off = (pos % RING) * 4
            bram.write(off, words_of(smp[idx]).tobytes())
            pos += CHUNK
            now = time.time()
            if now < next_t:
                time.sleep(next_t - now)
            next_t += CHUNK_T          # 绝对时刻调度，不积累漂移

    t = threading.Thread(target=feeder, daemon=True)
    t.start()

    # 预填完成后再启动读取
    time.sleep(0.2)
    regs.write(0x028, RING)            # PLY_LEN（环形总长）
    regs.write(0x024, 0x3)             # PLY_CTL: en | loop
    ctrl.write(0, 0x03 | 0x20)         # run | drc_en | src=wav
    print("playing: GAIN=+%d dB, ring=%d words, feeder paced %d words/%.1f ms"
          % (db, RING, CHUNK, CHUNK_T * 1000))
    sys.stdout.flush()

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
