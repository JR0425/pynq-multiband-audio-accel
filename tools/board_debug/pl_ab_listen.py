"""判 fir_pl.bit 到底有没有把声音送到耳机 —— 不靠耳朵。

思路（差分法，抵消"PC 喇叭直漏进 PC 麦克风"这条干扰）：

  1. PC 喇叭全程放 440 Hz（不中断），板子的麦克风会收到它；
  2. 板子的 `run` 每 4 秒开、4 秒关，共 3 轮；
  3. PC 麦克风全程录音（`sd.playrec` 全双工，别用 `sd.rec`+`sd.play`——
     那样 `sd.wait()` 只等播放，录音缓冲没填满，读出来是全零）。

  run=0 的窗口里，PC 麦克风听到的 440 只有"喇叭直漏"；
  run=1 的窗口里，除了直漏，还有"板子麦克风 -> PL -> 耳机 -> PC 麦克风"。
  两个窗口的中位数之比就是板子贡献了多少。

用法（PC 侧，Windows）：

    python tools/board_debug/pl_ab_listen.py --secs 30

判据：ON/OFF 中位数之比 > 1.5 算"耳机在出声"；< 1.15 算"没声"。
"""

import argparse
import threading
import time

import numpy as np
import paramiko
import sounddevice as sd

HOST, USER, PASS = "192.168.2.99", "xilinx", "xilinx"
FS = 48000
F0 = 440.0

# 板上一侧：加载 -> 配 codec -> 周期开关 run。print 只能 ASCII。
BOARD_CMD = r'''cd /home/xilinx/dma_exp && env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -c "
import os,sys,time; os.environ.setdefault('XILINX_XRT','/usr'); sys.path.insert(0,'/home/xilinx/dma_exp')
import dma_audio_probe as P
from pynq import Overlay, MMIO
ol=Overlay('/home/xilinx/dma_exp/fir_pl.bit')
P.codec_init(1)
c=MMIO(0x43C00000,0x1000)
c.write(0,0x07); time.sleep(0.02); c.write(0,0x103); time.sleep(0.005); c.write(0,0x03); time.sleep(0.3); c.write(0,0)
print('READY', time.strftime('%H:%M:%S')); sys.stdout.flush()
for k in range(3):
    c.write(0,0x03)
    print('ON  %d %s' % (k, time.strftime('%H:%M:%S'))); sys.stdout.flush()
    time.sleep(4.0)
    c.write(0,0)
    print('OFF %d %s' % (k, time.strftime('%H:%M:%S'))); sys.stdout.flush()
    time.sleep(4.0)
print('DONE', time.strftime('%H:%M:%S')); sys.stdout.flush()
"'''


def board(cmd):
    q = "'" + cmd.replace("'", "'\\''") + "'"
    full = "echo %s | sudo -S bash -c %s" % (PASS, q)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=10,
              allow_agent=False, look_for_keys=False)
    _i, out, err = c.exec_command(full, timeout=300)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    c.close()
    return o, e


def band_ratio(x, f0=F0, half=25.0):
    x = x - x.mean()
    n = len(x)
    X = np.abs(np.fft.rfft(x * np.hanning(n)))
    f = np.fft.rfftfreq(n, 1.0 / FS)
    band = (f > f0 - half) & (f < f0 + half)
    ref = (f > 900) & (f < 2500)
    return float(X[band].max()) / (float(X[ref].max()) + 1e-12)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=32.0)
    ap.add_argument("--vol", type=float, default=0.25, help="PC 喇叭音量 0-1")
    ap.add_argument("--lead", type=float, default=2.0)
    args = ap.parse_args()

    fs = int(sd.query_devices(kind="input")["default_samplerate"])
    global FS
    FS = fs
    print("  输入设备：", sd.query_devices(kind="input")["name"])
    print("  输出设备：", sd.query_devices(kind="output")["name"])

    n = int(args.secs * fs)
    t = np.arange(n) / fs
    tone = (args.vol * np.sin(2 * np.pi * F0 * t)).astype(np.float32)

    box = {}

    def run_board():
        time.sleep(args.lead)
        try:
            box["o"], box["e"] = board(BOARD_CMD)
        except Exception as ex:            # 板子掉线也要把录音跑完
            box["o"], box["e"] = "", repr(ex)

    th = threading.Thread(target=run_board)
    th.start()
    rec = sd.playrec(tone, samplerate=fs, channels=1, dtype="float32", blocking=True)
    th.join()

    o = box.get("o", "")
    print("  板子：", " | ".join(o.strip().split("\n")))
    err = box.get("e", "").strip()
    if err and "password" not in err:
        print("  stderr:", err[:300])

    x = rec[:, 0].astype(np.float64)
    np.save("C:/Users/XIA/_pl_ab.npy", x)
    print("  PC 录音 rms = %.5f  peak = %.4f" % (np.sqrt(np.mean(x ** 2)), np.abs(x).max()))
    if np.abs(x).max() < 1e-4:
        print("  ==> 录音全零，PC 麦克风没工作，这次作废")
        return 1

    win = int(0.5 * fs)
    m = len(x) // win
    vals = np.array([band_ratio(x[i * win:(i + 1) * win]) for i in range(m)])
    print("\n  窗口(0.5s)  440 比值")
    for i, v in enumerate(vals):
        print("   %5.1fs  %6.2f  %s" % (i * 0.5, v, "#" * min(50, int(v * 3))))

    # 板子 4 秒开、4 秒关 -> 8 个窗口一轮，一轮 16 个窗口。
    # 两台机器的时钟不同步，所以 16 个相位全试一遍，取分离度最好的那个。
    skip = int((args.lead + 1.5) / 0.5)
    best = None
    for p in range(16):
        mask = np.array([(i - p) % 16 < 8 for i in range(m)])
        mask[:skip] = False
        if mask.sum() < 8 or (~mask).sum() < 8:
            continue
        on, off = vals[mask], vals[~mask]
        r = float(np.median(on) / (np.median(off) + 1e-9))
        if best is None or r > best[1]:
            best = (p, r, float(np.median(on)), float(np.median(off)))
    if best is None:
        print("\n  数据不够，无法判定")
        return 1
    p, r, on, off = best
    print("\n  最佳相位 p=%d：ON 中位 %.2f / OFF 中位 %.2f -> 比值 %.2f" % (p, on, off, r))
    if r > 1.5:
        print("  ==> 耳机在出声（板子贡献明显）")
    elif r > 1.15:
        print("  ==> 有贡献但弱，可能音量小或摆位不对")
    else:
        print("  ==> 没测到板子的贡献")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
