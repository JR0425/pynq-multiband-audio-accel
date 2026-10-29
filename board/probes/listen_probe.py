"""PC 侧麦克风对着耳机录音 -> 频谱分析，判断板子到底有没有出声。

替代人耳：耳机放在 PC 麦克风旁边，板子放 440 Hz，录音里应该能看到 440 Hz 的峰。

必须先用**已知会响**的出货位流做对照，否则测出"没有"也说明不了问题。

    python listen_probe.py --bit ship      # 对照：出货位流
    python listen_probe.py --bit otx       # 官方发送器位流
    python listen_probe.py --bit pl        # 全 PL 直连

节奏：录 18 秒，第 2 秒时让板子放 6 秒音，逐窗口看 440 Hz。
"""

import argparse
import time

import numpy as np
import paramiko
import sounddevice as sd

HOST, USER, PASS = "192.168.2.99", "xilinx", "xilinx"
FS = 48000


def board(cmd):
    """加载 overlay 需要 root，所以整条命令走 sudo（和 board_ssh.py --sudo 一样）。"""
    q = "'" + cmd.replace("'", "'\\''") + "'"
    full = "echo %s | sudo -S bash -c %s" % (PASS, q)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=10,
              allow_agent=False, look_for_keys=False)
    _in, out, err = c.exec_command(full, timeout=300)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    rc = out.channel.recv_exit_status()
    c.close()
    return rc, o, e


PLAY = {
    "ship": r'''cd /home/xilinx/dma_exp && env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -c "
import os,sys,time; os.environ.setdefault('XILINX_XRT','/usr'); sys.path.insert(0,'/home/xilinx/dma_exp')
import hearing_tone as HT
from pynq import Overlay
ol=Overlay('/home/xilinx/fir.bit'); a=ol.audio_codec_ctrl_0
a.configure(); a.set_volume(60)
HT._write_tone('/dev/shm/lp.wav',440.0,-1.0,10.0); a.load('/dev/shm/lp.wav')
print('PLAY',time.strftime('%H:%M:%S')); sys.stdout.flush(); a.play()
"''',
    "otx": r'''cd /home/xilinx/dma_exp && env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -c "
import os,sys,time; os.environ.setdefault('XILINX_XRT','/usr'); sys.path.insert(0,'/home/xilinx/dma_exp')
import dma_audio_probe as P
from pynq import Overlay, MMIO
ol=Overlay('/home/xilinx/dma_exp/fir_officialtx.bit'); P.codec_init(1)
c=MMIO(0x43C00000,0x1000); c.write(0,0); time.sleep(0.2)
print('PLAY',time.strftime('%H:%M:%S')); sys.stdout.flush()
c.write(0,1); time.sleep(10.0); c.write(0,0)
"''',
    # 不调 select_mic：出货那套 configure() 也不调它，这是两边唯一的初始化差异
    "nomik": r'''cd /home/xilinx/dma_exp && env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -c "
import os,sys,time; os.environ.setdefault('XILINX_XRT','/usr'); sys.path.insert(0,'/home/xilinx/dma_exp')
import cffi
from pynq import Overlay, MMIO
ol=Overlay('/home/xilinx/dma_exp/fir_officialtx.bit')
import dma_audio_probe as P
ffi=cffi.FFI(); lib=ffi.dlopen('/home/xilinx/pynq/lib/libaudio.so')
ffi.cdef('void config_audio_pll(int iic_index);')
ffi.cdef('void config_audio_codec(int iic_index);')
lib.config_audio_pll(1); lib.config_audio_codec(1)
P.codec_unmute()
print('unmute only, 401C=0x%02X 401E=0x%02X 4023=0x%02X' % (P.i2c_rd(0x401C) or 0, P.i2c_rd(0x401E) or 0, P.i2c_rd(0x4023) or 0)); sys.stdout.flush()
c=MMIO(0x43C00000,0x1000); c.write(0,0); time.sleep(0.2)
print('PLAY',time.strftime('%H:%M:%S')); sys.stdout.flush()
c.write(0,1); time.sleep(10.0); c.write(0,0)
"''',
    "i2sdbg": r'''cd /home/xilinx/dma_exp && env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -c "
import os,sys,time; os.environ.setdefault('XILINX_XRT','/usr'); sys.path.insert(0,'/home/xilinx/dma_exp')
import dma_audio_probe as P
P.BITFILE='i2sdbg.bit'; P.HWHFILE='i2sdbg.hwh'
dma,ctrl,stat=P.load(); P.codec_init(1); P.flush_all(ctrl)
print('PLAY',time.strftime('%H:%M:%S')); sys.stdout.flush()
P.set_ctrl(ctrl,1|(1<<3)); time.sleep(10.0); P.set_ctrl(ctrl,0)
"''',
    # 放音中抓到过 0x401C=0x21 / 0x401E=0x41（左右不对称），我们的 codec_unmute 写的是 0x01。
    # 这个模式就是试那两位到底管不管用。
    "reg21": r'''cd /home/xilinx/dma_exp && env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -c "
import os,sys,time; os.environ.setdefault('XILINX_XRT','/usr'); sys.path.insert(0,'/home/xilinx/dma_exp')
import dma_audio_probe as P
from pynq import Overlay, MMIO
ol=Overlay('/home/xilinx/dma_exp/fir_officialtx.bit'); P.codec_init(1)
P.i2c_wr(0x401C,0x21); P.i2c_wr(0x401E,0x41)
print('after write 401C=0x%02X 401E=0x%02X' % (P.i2c_rd(0x401C) or 0, P.i2c_rd(0x401E) or 0)); sys.stdout.flush()
c=MMIO(0x43C00000,0x1000); c.write(0,0); time.sleep(0.2)
print('PLAY',time.strftime('%H:%M:%S')); sys.stdout.flush()
c.write(0,1); time.sleep(10.0); c.write(0,0)
"''',
    # ---- fir_ipmux.bit：官方 IP + 我们的发送器，GPIO bit0 切换 ----
    # sel=0 -> 官方 IP 出数据（对照组，应该响）
    "ipmux0": r'''cd /home/xilinx/dma_exp && env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -c "
import os,sys,time; os.environ.setdefault('XILINX_XRT','/usr'); sys.path.insert(0,'/home/xilinx/dma_exp')
import hearing_tone as HT
from pynq import Overlay, MMIO
ol=Overlay('/home/xilinx/dma_exp/fir_ipmux.bit')
MMIO(0x43C10000,0x1000).write(0,0)
a=ol.audio_codec_ctrl_0
a.configure(); a.set_volume(60)
HT._write_tone('/dev/shm/ipm.wav',440.0,-1.0,10.0); a.load('/dev/shm/ipm.wav')
print('PLAY sel=0',time.strftime('%H:%M:%S')); sys.stdout.flush(); a.play()
"''',
    # sel=1 -> tone_ser（时钟来自官方 IP，发送器是 iis_ser，音源是我们的）
    "ipmux1": r'''cd /home/xilinx/dma_exp && env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -c "
import os,sys,time; os.environ.setdefault('XILINX_XRT','/usr'); sys.path.insert(0,'/home/xilinx/dma_exp')
from pynq import Overlay, MMIO
ol=Overlay('/home/xilinx/dma_exp/fir_ipmux.bit')
a=ol.audio_codec_ctrl_0
a.configure(); a.set_volume(60)
g=MMIO(0x43C10000,0x1000); g.write(0,0); time.sleep(0.2)
print('PLAY sel=1',time.strftime('%H:%M:%S')); sys.stdout.flush()
g.write(0,1); time.sleep(10.0); g.write(0,0)
"''',
}


def band_ratio(x, f0=440.0, half=30.0):
    """440 附近的绝对谱峰 / 500-1500 Hz 对照带的谱峰。比中位数稳得多。"""
    x = x - x.mean()
    n = len(x)
    X = np.abs(np.fft.rfft(x * np.hanning(n)))
    f = np.fft.rfftfreq(n, 1.0 / FS)
    band = (f > f0 - half) & (f < f0 + half)
    ref = (f > 500) & (f < 1500)
    return float(X[band].max()) / (float(X[ref].max()) + 1e-12)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default="ship", choices=list(PLAY))
    ap.add_argument("--secs", type=float, default=18.0)
    args = ap.parse_args()

    print("  输入设备：", sd.query_devices(kind="input")["name"])
    fs = int(sd.query_devices(kind="input")["default_samplerate"])
    global FS
    FS = fs

    print("  录 %.0f 秒，第 2 秒开始放音…" % args.secs)
    rec = sd.rec(int(args.secs * fs), samplerate=fs, channels=1,
                 dtype="float32", blocking=False)
    time.sleep(2.0)
    rc, o, e = board(PLAY[args.bit])
    print("  板子回了：", o.strip().replace("\n", " | ") or e.strip()[:200])
    sd.wait()
    x = rec[:, 0].astype(np.float64)
    np.save("C:/Users/XIA/_last_rec.npy", x)

    win = int(0.5 * fs)
    print("\n  时间    总 rms     440Hz 峰/中位")
    for i in range(len(x) // win):
        seg = x[i * win:(i + 1) * win]
        r = float(np.sqrt(np.mean(seg ** 2)))
        b = band_ratio(seg)
        print("  %5.1fs  %.5f   %6.2f  %s"
              % (i * 0.5, r, b, "#" * min(50, int(b * 3))))
    print("\n  对照读数：会响的话，放音那几秒的 440 比值应明显高出静音段（>3 倍）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
