# cap_probe9.py -- the one lever cap_probe8 left standing: is the headphone driver
# the injection point?
#
# 2026-10-13 late night.  cap_probe8.py ran every condition for 3 s (15 x 0.2 s)
# instead of 0.2 s, with the DAC fed a real 1 kHz tone.  What it found:
#
#   * the four-register "fix" with TX silence  -> 286 74 20 2120 29070 ... 44211
#     i.e. it CLIMBS to full scale in ~0.8 s.  cap_probe6/7 called this state
#     quiet 3/3; they only ever looked at the first 0.2 s.  The fix is void.
#   * same state with the mic differential input muted (R8/R9 = 0x00) -> rails
#     anyway (~44000).  So the level does NOT come in through the mic chain.
#   * same state with the output at volume 0 (R29/R30 = 0x03, -57 dB, driver
#     still ENABLED) -> rails anyway (~48200).  So it is not output-amplitude
#     dependent either.
#
# cap_probe6 did have one row that pointed at the driver: R29/R30 = 0x02
# (driver OFF) was quiet -- but, again, over 0.2 s only.  That is the one lever
# probe8 did not retest, so this probe retests it over 3 s.
#
# Conditions (all 3 s, TX = 1 kHz tone unless noted, so the loop is excited):
#   drv_off          R29/R30 = 0x02 (driver off), PYNQ-default mic gains
#   drv_off_miclo    R29/R30 = 0x02, plus the four-register mic-gain change
#   drv_off_micmute  R29/R30 = 0x02, mic input muted
#   drv_on_mictgt    driver on (volume 40), four-register mic change, TX = 0
#   drv_on_ref       driver on (volume 40), PYNQ-default mic gains, TX = 0
#
# If drv_off stays flat at a few hundred for the whole 3 s while drv_on climbs,
# the headphone driver is where the signal re-enters, and every mic-side measure
# was aimed at the wrong end of the loop.

import fcntl, os, time
import numpy as np
from pynq import Overlay

HERE = "/home/xilinx/jupyter_notebooks"
L = 480
NCHUNK, NCHUNKS, NPASS = 20, 15, 2
ADDR = 0x3B
R5, R7, R8, R9 = 0x0B, 0x0D, 0x0E, 0x0F
R29, R30 = 0x23, 0x24
TONE_HZ, TONE_AMP = 1000.0, 8000

# name, boost, dv, tx, volume
COND = [
    ("drv_off         ", 0x10, 0xB3, "tone",  0, True),   # driver 0x02
    ("drv_off_miclo   ", 0x08, 0xD3, "tone",  0, True),
    ("drv_off_micmute ", 0x08, 0x00, "tone",  0, True),
    ("drv_on_mictgt   ", 0x08, 0xD3, "zero", 40, False),
    ("drv_on_ref      ", 0x10, 0xB3, "zero", 40, False),
]

ol = Overlay(os.path.join(HERE, "fir.bit"))
audio = ol.audio_codec_ctrl_0
ffi = audio._ffi
audio.configure()
MMIO_LEN = audio.mmio.length
UIO, IIC = audio.uio_index, audio.iic_index

ffi.cdef("""
    void* stream_begin(unsigned int mmio, unsigned int volume, int uio, int iic);
    int   duplex_block(void* h, unsigned int* in, unsigned int* out, unsigned int n);
    void  stream_end(void* h);
    int   stream_reg_write(unsigned char reg, unsigned char val, int iic);
""")
lib = ffi.dlopen(os.path.join(HERE, "libaudio_stream.so"))

rbuf = ffi.new("unsigned int[]", 2 * L)
wbuf = ffi.new("unsigned int[]", 2 * L)

fd = os.open("/dev/i2c-1", os.O_RDWR)
fcntl.ioctl(fd, 0x0703, ADDR)


def rd(reg):
    try:
        os.write(fd, bytes([0x40, reg]))
        return "%02X" % os.read(fd, 1)[0]
    except Exception:
        return "??"


def reset():
    audio.configure()
    audio.set_volume(62)
    audio.select_microphone()
    time.sleep(0.2)


def fill_tx(kind, phase):
    if kind == "zero":
        for i in range(2 * L):
            wbuf[i] = 0
        return
    t = (np.arange(L, dtype=np.float64) + phase) / 48000.0
    s = np.round(TONE_AMP * np.sin(2 * np.pi * TONE_HZ * t)).astype(np.int32)
    raw = (s << 8).astype(np.uint32)
    for i in range(L):
        wbuf[2 * i] = int(raw[i])
        wbuf[2 * i + 1] = int(raw[i])


def run_cond(boost, dv, tx, vol, drv_off):
    h = lib.stream_begin(MMIO_LEN, vol, UIO, IIC)
    if h == ffi.NULL:
        return None
    if drv_off:
        lib.stream_reg_write(R29, 0x02, IIC)
        lib.stream_reg_write(R30, 0x02, IIC)
    lib.stream_reg_write(R5, boost, IIC)
    lib.stream_reg_write(R7, boost, IIC)
    lib.stream_reg_write(R8, dv, IIC)
    lib.stream_reg_write(R9, dv, IIC)
    time.sleep(0.15)
    rb = "%s %s" % (rd(R29), rd(R30))
    chunks, phase = [], 0
    for _ in range(NCHUNKS):
        rs = []
        for _ in range(NCHUNK):
            fill_tx(tx, phase)
            phase += L
            if lib.duplex_block(h, rbuf, wbuf, L) != L:
                break
            a = np.frombuffer(bytes(ffi.buffer(rbuf, 4 * 2 * L)), dtype=np.int32)
            v = a.reshape(-1, 2)[:, 0].astype(np.float64) / 256.0
            rs.append(float(np.sqrt(np.mean(v ** 2))))
        chunks.append(float(np.median(rs)) if rs else -1.0)
    lib.stream_end(h)
    return rb, chunks


print("每格 = 20 块(0.2 秒)的 rms 中位,int16 域;满幅 32767,安静 ~200")
print("每行 15 格 = 3 秒,左到右是时间顺序")
print("R29/R30 读回值也打出来 —— 关功放那一档要看到 02 才算真关上了")
print()

res = {}
for p in range(NPASS):
    order = COND if p % 2 == 0 else list(reversed(COND))
    for name, boost, dv, tx, vol, drv_off in order:
        reset()
        out = run_cond(boost, dv, tx, vol, drv_off)
        if out is None:
            print("  pass%d %s FAILED" % (p, name))
            continue
        rb, ch = out
        res.setdefault(name, []).append(ch)
        print("  pass%d %s R29/R30=%s  %s"
              % (p, name, rb, " ".join("%6.0f" % c for c in ch)))

print("=" * 78)
for name, boost, dv, tx, vol, drv_off in COND:
    for ch in res.get(name, []):
        first, last = ch[0], ch[-1]
        tag = "↑ 在爬" if last > 4 * max(first, 1.0) else ("满" if last > 20000 else "平")
        print("  %s %s   %s" % (name, " ".join("%6.0f" % c for c in ch), tag))

reset()
os.close(fd)
print("restored PYNQ default codec config")
print("=" * 78)
print("""
怎么读:
  drv_off / drv_off_miclo / drv_off_micmute 三档都是「功放关掉」,只差话筒那一侧。
  它们要是**3 秒里都平在几百**,而 drv_on_* 往上爬/贴顶 —— 信号是从**耳机功放**
  那一头注进来的,之前所有关于话筒增益的做法都打错了方向。
  它们要是有哪一档照样爬满,那连"功放关掉"都不是解,得往数据通路 / 时钟那边想。
""")
