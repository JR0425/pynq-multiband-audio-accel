"""同一个大音量 440，换成官方路径录 —— 判断这块板子的话筒本身能不能用。

官方：fir.bit + PYNQ Audio 类（configure + select_microphone + record）。
逐 0.5 秒打印 peak/rms（24 位域），看 PC 喇叭响的那几秒有没有抬升。
"""

import threading
import time

import numpy as np
import paramiko
import sounddevice as sd

HOST, USER, PASS = "192.168.2.99", "xilinx", "xilinx"
FS = 48000

BOARD = r'''cd /home/xilinx/dma_exp && env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -c "
import os,sys,time; os.environ.setdefault('XILINX_XRT','/usr'); sys.path.insert(0,'/home/xilinx/dma_exp')
import numpy as np
from pynq import Overlay
ol = Overlay('/home/xilinx/fir.bit')
a = ol.audio_codec_ctrl_0
a.configure(); a.select_microphone(); time.sleep(0.3)
print('REC ready'); sys.stdout.flush()
for i in range(12):
    a.record(0.5)
    x = (np.asarray(a.buffer, dtype=np.int64) & 0xFFFFFF)
    x = np.where(x >= (1<<23), x - (1<<24), x)
    print('  t=%.1fs peak=%9d rms=%10.1f dc=%9.1f'
          % (i*0.5, int(np.abs(x).max()), float(np.sqrt(np.mean(x.astype(np.float64)**2))), float(np.mean(x))))
    sys.stdout.flush()
"'''


def board():
    q = "'" + BOARD.replace("'", "'\\''") + "'"
    full = "echo %s | sudo -S bash -c %s" % (PASS, q)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=10,
              allow_agent=False, look_for_keys=False)
    _i, out, err = c.exec_command(full, timeout=300)
    print(out.read().decode("utf-8", "replace"))
    e = err.read().decode("utf-8", "replace")
    if e.strip() and "password" not in e:
        print("stderr:", e[:300])
    c.close()


def main():
    th = threading.Thread(target=board)
    th.start()
    time.sleep(2.5)
    n = int(4.0 * FS)
    t = np.arange(n) / FS
    tone = (0.9 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    print("  >>> PC 喇叭开始放 4 秒 440")
    sd.play(tone, samplerate=FS, blocking=True)
    print("  >>> 放完")
    th.join()


if __name__ == "__main__":
    main()
