# cap_probe8.py -- why does the fix work in the probe but not in the real live loop?
#
# 2026-10-13 late night.  cap_probe6/7 said "boost 0 dB + differential input
# +27 dB" is quiet 3/3.  That was installed in fir_live.py and the board's .so
# was rebuilt -- and the real 20-second live loop STILL railed:
#
#     sh run_live.sh 20   ->  进核 中位 18330  峰 31671   (int16 domain)
#
# (bit-exact self-check passed 960000/960000, so the data path is fine; only the
# input level is wrong.)
#
# This probe looks for the two differences between "that probe" and "the live
# loop", because either one alone explains the contradiction:
#
#   1. Duration.  cap_probe7 ran 20 blocks = 0.2 s per step.  This fault has been
#      seen to take ~0.7 s to climb from 100 to 15000, so a 0.2 s window cannot
#      tell "dead" from "slowly growing".  -> run 300 blocks = 3 s and print the
#      level every 20 blocks, so growth is visible.
#   2. The output signal.  cap_probe7 fed the DAC silence; the live loop feeds it
#      real audio.  If a second loop closes through the headphone -> air ->
#      microphone path, then a probe with no output STRUCTURALLY CANNOT SEE IT.
#      -> same register state, TX = 0 and TX = real tone.
#
# Conditions (all on stream_begin/duplex_block, the path fir_live.py uses):
#   fix_silence_v40    boost 0 / dv +27  / TX zero   / volume 40
#   fix_tone_v40       boost 0 / dv +27  / TX tone   / volume 40   <- the live case
#   fix_tone_v0        boost 0 / dv +27  / TX tone   / volume  0   (output -57 dB)
#   fix_tone_inmute    boost 0 / dv MUTE / TX tone   / volume 40   (mic input off)
#   ctrl_default_v40   boost +20 / dv +21 / TX zero  / volume 40   (control: must rail)
#
# Readback first: stream_reg_write(X) then read X back with a raw /dev/i2c-1
# transfer.  The return value of stream_reg_write is discarded in fir_live.py,
# so a silently failing I2C write would look exactly like this.

import fcntl, os, time
import numpy as np
from pynq import Overlay

HERE = "/home/xilinx/jupyter_notebooks"
L = 480                 # 10 ms per block
NCHUNK = 20             # blocks per printed sample point
NCHUNKS = 15            # 15 x 20 x 10 ms = 3 s per condition
NPASS = 2
ADDR = 0x3B

R5, R7 = 0x0B, 0x0D
R8, R9 = 0x0E, 0x0F

TONE_HZ = 1000.0
TONE_AMP = 8000         # int16 domain; the live run's output sat at ~9900

# name, boost(R5/R7), dv(R8/R9), tx ("zero"/"tone"), volume
COND = [
    ("fix_silence_v40  ", 0x08, 0xD3, "zero", 40),
    ("fix_tone_v40     ", 0x08, 0xD3, "tone", 40),
    ("fix_tone_v0      ", 0x08, 0xD3, "tone",  0),
    ("fix_tone_inmute  ", 0x08, 0x00, "tone", 40),
    ("ctrl_default_v40 ", 0x10, 0xB3, "zero", 40),
]

ol = Overlay(os.path.join(HERE, "fir.bit"))
audio = ol.audio_codec_ctrl_0
ffi = audio._ffi
audio.configure()
MMIO_LEN = audio.mmio.length
UIO = audio.uio_index
IIC = audio.iic_index

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
    audio.set_volume(62)          # PYNQ-side volume; the codec R29/R30 come from
    audio.select_microphone()     # stream_begin's volume argument
    time.sleep(0.2)


def fill_tx(kind, phase):
    """Write the TX buffer.  Samples are 24-bit left-justified in an int32,
    i.e. int16-domain value << 8 -- same convention cap_probe7 read RX with."""
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


def run_cond(boost, dv, tx, vol):
    """Returns a list of chunk rms (int16 domain) -- NCHUNKS numbers."""
    h = lib.stream_begin(MMIO_LEN, vol, UIO, IIC)
    if h == ffi.NULL:
        return None
    lib.stream_reg_write(R5, boost, IIC)
    lib.stream_reg_write(R7, boost, IIC)
    lib.stream_reg_write(R8, dv, IIC)
    lib.stream_reg_write(R9, dv, IIC)
    time.sleep(0.15)

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
    return chunks


print("每个数是 20 块(0.2 秒)的 rms 中位,int16 域;满幅 32767,安静 ~200")
print("每行 15 个数 = 3 秒,从左到右是时间顺序 —— 要看的正是它会不会往上爬")
print()

# ---- readback: does stream_reg_write actually land? ------------------------
reset()
pr = []
for reg, val in ((R5, 0x08), (R7, 0x08), (R8, 0xD3), (R9, 0xD3)):
    pr.append("%02X<-%02X(ret=%d)" % (reg, val, lib.stream_reg_write(reg, val, IIC)))
time.sleep(0.2)
print("readback: stream_reg_write returned: %s" % "  ".join(pr))
print("          raw i2c reads back: %s" % " ".join(
    "%02X=%s" % (r, rd(r)) for r in (R5, R7, R8, R9)))
print("          (读回来对不上 => stream_reg_write 没写进去,后面全都不用看了)")
print()

res = {}
for p in range(NPASS):
    order = COND if p % 2 == 0 else list(reversed(COND))
    for name, boost, dv, tx, vol in order:
        reset()
        ch = run_cond(boost, dv, tx, vol)
        if ch is None:
            print("  pass%d %s MEASUREMENT FAILED" % (p, name))
            continue
        res.setdefault(name, []).append(ch)
        print("  pass%d %s %s" % (p, name, " ".join("%6.0f" % c for c in ch)))

print("=" * 78)
print("  条件                 各段 rms(每段 0.2 s,共 3 s)")
for name, boost, dv, tx, vol in COND:
    for ch in res.get(name, []):
        first, last = ch[0], ch[-1]
        grow = "↑ 在爬" if last > 4 * max(first, 1.0) else ("满" if last > 20000 else "平")
        print("  %s %s   %s" % (name, " ".join("%6.0f" % c for c in ch), grow))

reset()
os.close(fd)
print("restored PYNQ default codec config")
print("=" * 78)
print("""
怎么读这张表:
  fix_silence_v40   应该平在几百      —— 这条 cap_probe7 已经报过
  fix_tone_v40      和上面同一套寄存器,只多了"有输出"。它要是往上爬/满,
                    **第二条环路就是靠输出成立的**,而且 cap_probe7 那个实验
                    结构上永远看不见它(它没送过输出)。
  fix_tone_v0       输出拧到 -57 dB(功放还开着)。它要是也平下来,
                    说明这条环路吃的是**输出幅度** —— 声学那条路的典型特征。
  fix_tone_inmute   话筒输入整个关掉。它要是照样满,说明根本**不是从话筒进来的**
                    (那前面所有关于话筒增益的结论都要重来)。
  ctrl_default_v40  对照组(原样 +41 dB),应该满 —— 不满说明这次实验没做到位。
""")
