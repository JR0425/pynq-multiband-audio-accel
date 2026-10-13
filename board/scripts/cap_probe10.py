# cap_probe10.py -- is the ramp there when NOTHING is written to the codec?
#
# 2026-10-13 late night, after cap_probe9.  Where we stand:
#
#   cap_probe8 (3 s window) killed the "cut the mic gain" fix: the same register
#   state that cap_probe7 called quiet for 3/3 runs climbs 286 -> 74 -> 20 ->
#   2120 -> 29070 -> full scale.  Muting the mic input entirely still rails.
#   cap_probe9 (3 s window) killed the last codec-side lever: with the headphone
#   driver fully OFF (R29/R30 = 0x02, read back as 02 02, so it really landed)
#   the level still climbs to ~46000 in about 0.4 s.  With the driver off AND
#   the mic input muted it still climbs from the third cell on.
#
# So both ends of the codec are cut and it still rails.  Every register VALUE in
# stream_begin has now been shown not to matter.
#
# That leaves the FOUNDATION of the whole story, which was never measured with a
# window longer than 0.2 s either.  cap_probe2.py measured, in one process:
#
#     seg1  write nothing, capture only           rms  229.7   <- quiet
#     seg2  do stream_begin's 4 I2C writes, then capture only   rms 46550.8
#     seg3  duplex with TX all zero                             rms 45842.8
#
# Every probe since has been built on "the 4 I2C writes are the trigger".  But
# seg1 ran 0.2 s and first.  If the ramp is intrinsic (something that starts
# when the codec/IP is running and takes ~0.7 s to climb), then seg1 being quiet
# and seg2 being loud says nothing about the writes -- it says seg1 was early.
#
# THIS PROBE answers exactly one question, with the window wide enough to answer
# it:  does capture-only, with NO I2C transaction of any kind, ramp over 3 s?
#
# Conditions (15 chunks x 20 blocks x 10 ms = 3 s each, rms printed per chunk):
#   cap_none    capture_begin/capture_block, ZERO I2C writes          <- the test
#   cap_i2c     same capture path, after stream_begin's 4 writes      <- probe2 seg2
#   str_zero    stream_begin + duplex_block, TX all zero              <- control
#
# What each outcome means (decide this BEFORE looking at the numbers):
#   cap_none climbs, str_zero climbs        -> the writes are INNOCENT.  probe2's
#                                              seg1 was a window artifact.  The
#                                              ramp is intrinsic to capture, so
#                                              look at ALC / the ADC / the read
#                                              path -- not at any register.
#   cap_none flat, cap_i2c climbs           -> the writes really are the trigger.
#                                              Their VALUES are all ruled out, so
#                                              it is the transaction itself, or a
#                                              bit none of probe6/8/9 touched ->
#                                              bisect the 4 writes one at a time.
#   cap_none flat, cap_i2c flat, str climbs -> it is duplex itself (TX path / the
#                                              shared I2S handshake), not the I2C
#                                              -> compare TX-only vs RX-only.
#   str_zero flat                           -> the rig is not reproducing the
#                                              fault at all; ignore the rest.
#
# Also, for each condition, dump the FIRST 8 raw RX words of chunk 0, 5 and 12.
# That separates "real noise" (random-looking samples) from "a counter / FIFO
# fill level / staircase" (monotone or stepped), which no rms number can do.
#
# And read R11..R16 back before and after each condition -- read-only, safe --
# to see whether the codec itself is moving any of them while the level climbs.

import fcntl, os, time
import numpy as np
from pynq import Overlay

HERE = "/home/xilinx/jupyter_notebooks"
L = 480                 # 10 ms per block
NCHUNK = 20             # blocks per printed sample point
NCHUNKS = 15            # 15 x 20 x 10 ms = 3 s per condition
NPASS = 2
ADDR = 0x3B

R22, R24 = 0x1C, 0x1E   # playback mixer, stream_begin writes 0x21 / 0x41
R29, R30 = 0x23, 0x24   # headphone volume, stream_begin writes (vol<<2)|3
WRITES = [(R22, 0x21), (R24, 0x41), (R29, 0xA3), (R30, 0xA3)]   # vol = 40
ALC_REGS = (0x11, 0x12, 0x13, 0x14, 0x15, 0x16)

COND = [
    ("cap_none   ", "cap",  False),
    ("cap_i2c    ", "cap",  True),
    ("str_zero   ", "str",  False),
]

DUMP_AT = (0, 5, 12)    # chunk indices whose first block gets dumped raw

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
    void* capture_begin(unsigned int mmio, int uio);
    int   capture_block(void* h, unsigned int* buf, unsigned int n);
    void  capture_end(void* h);
""")
lib = ffi.dlopen(os.path.join(HERE, "libaudio_stream.so"))

rbuf = ffi.new("unsigned int[]", 2 * L)
wbuf = ffi.new("unsigned int[]", 2 * L)
for i in range(2 * L):
    wbuf[i] = 0

fd = os.open("/dev/i2c-1", os.O_RDWR)
fcntl.ioctl(fd, 0x0703, ADDR)


def rd(reg):
    try:
        os.write(fd, bytes([0x40, reg]))
        return "%02X" % os.read(fd, 1)[0]
    except Exception:
        return "??"


def wr(reg, val):
    os.write(fd, bytes([0x40, reg, val]))


def alc_snapshot():
    return " ".join("%02X=%s" % (r, rd(r)) for r in ALC_REGS)


def reset():
    audio.configure()
    audio.set_volume(62)
    audio.select_microphone()
    time.sleep(0.5)          # generous: give any slow ramp a chance to show up


def rms_of_buf():
    a = np.frombuffer(bytes(ffi.buffer(rbuf, 4 * 2 * L)), dtype=np.int32)
    v = a.reshape(-1, 2)[:, 0].astype(np.float64) / 256.0
    return float(np.sqrt(np.mean(v ** 2)))


def raw_of_buf():
    a = np.frombuffer(bytes(ffi.buffer(rbuf, 4 * 2 * L)), dtype=np.int32)
    v = a.reshape(-1, 2)[:, 0].astype(np.float64) / 256.0
    return " ".join("%8.0f" % x for x in v[:8])


def run_cap(h, do_i2c):
    """capture-only: never opens i2c through the library.  If do_i2c, the 4
    stream_begin writes are done with a raw /dev/i2c-1 write instead, so the
    capture path itself stays exactly the same in both conditions."""
    if do_i2c:
        for reg, val in WRITES:
            wr(reg, val)
        time.sleep(0.15)
    chunks, dumps = [], {}
    for c in range(NCHUNKS):
        rs = []
        for b in range(NCHUNK):
            if lib.capture_block(h, rbuf, L) != L:
                break
            rs.append(rms_of_buf())
            if b == 0 and c in DUMP_AT:
                dumps[c] = raw_of_buf()
        chunks.append(float(np.median(rs)) if rs else -1.0)
    return chunks, dumps


def run_str(h):
    """stream_begin + duplex_block with TX all zero (the known-bad path)."""
    chunks, dumps = [], {}
    for c in range(NCHUNKS):
        rs = []
        for b in range(NCHUNK):
            if lib.duplex_block(h, rbuf, wbuf, L) != L:
                break
            rs.append(rms_of_buf())
            if b == 0 and c in DUMP_AT:
                dumps[c] = raw_of_buf()
        chunks.append(float(np.median(rs)) if rs else -1.0)
    return chunks, dumps


def run_cond(kind, do_i2c):
    h = lib.capture_begin(MMIO_LEN, UIO) if kind == "cap" else \
        lib.stream_begin(MMIO_LEN, 40, UIO, IIC)
    if h == ffi.NULL:
        return None, None, None, None
    before = alc_snapshot()
    try:
        if kind == "cap":
            ch, dumps = run_cap(h, do_i2c)
        else:
            ch, dumps = run_str(h)
    finally:
        before_after = (before, alc_snapshot())
        if kind == "cap":
            lib.capture_end(h)
        else:
            lib.stream_end(h)
    return ch, dumps, before_after[0], before_after[1]


print("每个数是 20 块(0.2 秒)的 rms 中位,int16 域;满幅 32767,安静 ~200")
print("每行 15 个数 = 3 秒 —— 要看的正是它会不会往上爬")
print("cap_none 从开始到结束一次 I2C 写都不做;cap_i2c 只多 4 次裸 i2c 写")
print()

res = {}
for p in range(NPASS):
    order = COND if p % 2 == 0 else list(reversed(COND))
    for name, kind, do_i2c in order:
        reset()
        out = run_cond(kind, do_i2c)
        if out[0] is None:
            print("  pass%d %s MEASUREMENT FAILED" % (p, name))
            continue
        ch, dumps, alc0, alc1 = out
        res.setdefault(name, []).append((ch, dumps, alc0, alc1))
        print("  pass%d %s %s" % (p, name, " ".join("%6.0f" % c for c in ch)))
        for c in DUMP_AT:
            if c in dumps:
                print("               raw[%2d] %s" % (c, dumps[c]))
        print("               ALC %s  ->  %s" % (alc0, alc1))

print("=" * 78)
print("  条件            各段 rms(每段 0.2 s,共 3 s)")
for name, kind, do_i2c in COND:
    for ch, dumps, _, _ in res.get(name, []):
        first, last = ch[0], ch[-1]
        grow = "↑ 在爬" if last > 4 * max(first, 1.0) else \
               ("满" if last > 20000 else "平")
        print("  %s %s   %s" % (name, " ".join("%6.0f" % c for c in ch), grow))

reset()
os.close(fd)
print("restored PYNQ default codec config")
print("=" * 78)
print("""
怎么读这张表(先在脑子里定好,再对数字):
  cap_none 在爬、str_zero 在爬  -> **那 4 次 I2C 写是清白的**。cap_probe2 的段1
                                  也是窗口假象;这个爬升是**采集本身**自带的
                                  -> 去查 ALC / ADC / 读通路,别再看任何寄存器。
  cap_none 平、cap_i2c 在爬      -> 写真的是扳机。但写的**值**已经全被排掉,
                                  所以是"这次 I2C 事务本身",或者某个 probe6/8/9
                                  都没碰到的位 -> 把 4 次写一个一个拆开重测。
  cap_none 平、cap_i2c 平、str 爬 -> 是 duplex 自己(写 TX 那条路 / 共用的 I2S
                                  握手),不是 I2C -> 再分 TX-only 和 RX-only。
  str_zero 也平                  -> 这次根本没复现出故障,上面三条都别信。
""")
