# cap_probe7.py -- verify the fix on the real duplex path (the one the demo uses)
#
# cap_probe6.py found two things that each kill the mic self-oscillation, 3/3 runs:
#   * R29/R30 = 0x02  -> headphone driver OFF          (mute the output)
#   * R5/R7  = 0x08   -> mic PGA boost 0 dB, not +20 dB (cut the input gain)
# Everything else still railed at least once.
#
# The fix we want is the second one, because it keeps the headphones working.
# It costs 20 dB of mic gain, so this probe asks how much of that we can buy
# back on the other input stage (R8/R9, differential input volume, 0.75 dB
# steps from -12 to +35.25 dB).
#
# Measured through stream_begin/duplex_block -- the same path fir_live.py uses,
# with the DAC fed silence.  cap_probe2.py showed this rails just like the
# capture-only path, so the loop does not need any signal going out to the
# headphone: it is enough for the driver to be enabled.
#
# Register fields (ADAU1761 Rev. F):
#   R5/R7 [4:3] LDBOOST: 00 = mute, 01 = 0 dB, 10 = +20 dB
#   R8/R9 [7:2] LDVOL:   (dB + 12) / 0.75, bit1 = unmute, bit0 = differential on
#     0xB3 = +21 dB, 0xD3 = +27, 0xE3 = +30, 0xF3 = +33, 0xFF = +35.25
#
# Three passes, alternating order, because this fault flips between runs.

import os, time
import numpy as np
from pynq import Overlay

HERE = "/home/xilinx/jupyter_notebooks"
L, N = 480, 20
VOL = 62

R5, R7 = 0x0B, 0x0D
R8, R9 = 0x0E, 0x0F

STEPS = [
    ("ctrl_boost20_dv21", 0x10, 0xB3),   # today's setting -- expect full rail
    ("fix_boost0_dv21 ", 0x08, 0xB3),   # the proposed fix
    ("fix_boost0_dv27 ", 0x08, 0xD3),
    ("fix_boost0_dv30 ", 0x08, 0xE3),
    ("fix_boost0_dv33 ", 0x08, 0xF3),
    ("fix_boost0_dv35 ", 0x08, 0xFF),
    ("worst20_dv35    ", 0x10, 0xFF),   # +20 dB boost AND max input volume
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
wbuf = ffi.new("unsigned int[]", 2 * L)      # all zero -> the DAC plays silence


def reset():
    audio.configure()
    audio.set_volume(VOL)
    audio.select_microphone()
    time.sleep(0.2)


def meas():
    h = lib.stream_begin(MMIO_LEN, VOL, UIO, IIC)
    if h == ffi.NULL:
        return None
    rs, raws = [], []
    for _ in range(N):
        if lib.duplex_block(h, rbuf, wbuf, L) != L:
            break
        a = np.frombuffer(bytes(ffi.buffer(rbuf, 4 * 2 * L)), dtype=np.int32)
        v = a.reshape(-1, 2)[:, 0]
        raws.append(v)
        f = v.astype(np.float64) / 256.0
        rs.append(float(np.sqrt(np.mean(f ** 2))))
    lib.stream_end(h)
    if not rs:
        return None
    cat = np.concatenate(raws)
    return (float(np.median(rs)), float(np.max(np.abs(cat / 256.0))),
            int(cat.min()), int(cat.max()), float(cat.mean()))


print("step order alternates every pass; rms16 is the 16-bit-domain rms of ch0")
PASSES = 3
res = {}
for p in range(PASSES):
    order = STEPS if p % 2 == 0 else list(reversed(STEPS))
    for name, boost, dv in order:
        reset()
        lib.stream_reg_write(R5, boost, IIC)
        lib.stream_reg_write(R7, boost, IIC)
        lib.stream_reg_write(R8, dv, IIC)
        lib.stream_reg_write(R9, dv, IIC)
        time.sleep(0.15)
        m = meas()
        if m is None:
            print("  pass%d  %s  MEASUREMENT FAILED" % (p, name))
            continue
        rms, pk, rmin, rmax, rmean = m
        res.setdefault(name, []).append(rms)
        print("  pass%d  %s rms16=%8.1f pk=%7.1f | raw24 min=%8d max=%8d mean=%9.0f"
              % (p, name, rms, pk, rmin, rmax, rmean))

print("=" * 78)
print("  step             median rms16    trials")
for name, boost, dv in STEPS:
    vs = res.get(name, [])
    if not vs:
        continue
    print("  %s %9.1f      %s"
          % (name, float(np.median(vs)), " ".join("%.0f" % v for v in vs)))

reset()
print("restored PYNQ default codec config")
print("=" * 78)
