"""给板子话筒喂一个已知的大信号，判断我们的 I2S 接收有没有整体右移 8 位。

做法：PC 喇叭放 5 秒很响的 440（幅度 0.9），板子同时用 DMA 录音 6 秒，
逐秒打印原始 24 位字的最大峰值。

判据：
  peak 上到百万级（≈满量程 8388608 的几分之一）  -> 位对齐是对的，只是平时话筒音量小
  peak 卡在几万以内（≈ 上者 / 256）              -> 接收整体右移了 8 位（48 dB），
                                                    前端拿到的就是 1/256 的信号，所以听不见
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
import dma_audio_probe as P
P.BITFILE='fir_dma.bit'; P.HWHFILE='fir_dma.hwh'
dma,ctrl,stat = P.load()
P.codec_init(1)
P.flush_all(ctrl)
P.set_ctrl(ctrl, P.blk_bits(480))
buf = P.allocate(shape=(960,), dtype=np.int32)
t0=time.time()
sec=0; pk=0; rms=0.0; n=0
print('REC ready'); sys.stdout.flush()
while time.time()-t0 < 6.0:
    sr = P.cap_block(dma, buf)
    if not (sr & P.SR_IOC): continue
    x = (np.array(buf, dtype=np.int32) >> 8).astype(np.int64)
    pk = max(pk, int(np.abs(x).max()))
    rms += float(np.sum(x.astype(np.float64)**2)); n += x.size
    if time.time()-t0 >= sec+1.0:
        print('  t=%ds peak=%9d rms=%10.1f' % (sec, pk, (rms/max(1,n))**0.5)); sys.stdout.flush()
        sec += 1; pk=0; rms=0.0; n=0
P.set_ctrl(ctrl,0)
print('REC done')
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
    time.sleep(3.0)                       # 等板子 load + codec_init 完成
    n = int(5.0 * FS)
    t = np.arange(n) / FS
    tone = (0.9 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    print("  板子应该开始录了，PC 喇叭放 5 秒 440…")
    sd.play(tone, samplerate=FS, blocking=True)
    print("  放完了")
    th.join()


if __name__ == "__main__":
    main()
