# cap_probe6.py -- microphone self-oscillation: which knob breaks the loop?
#
# Background (2026-10-13): with the playback path open (R22=0x21, R24=0x41,
# R29/R30 = volume) the ADC rails at full scale even though the DAC is fed
# silence.
#
# Facts read out of the ADAU1761 Rev. F register map (Tables 42/43/50/51/92/93):
#   R4/R6 (0x0A/0x0C) Rec Mixer 0: LINPG/LINNG 000 = Mute, MX1EN bit0
#   R5/R7 (0x0B/0x0D) Rec Mixer 1: LDBOOST 00 = Mute, 01 = 0 dB, 10 = +20 dB
#   R8/R9 (0x0E/0x0F) diff input vol: [7:2] -12..+35.25 dB, bit1 MUTE 0=mute,
#                     bit0 ENABLE 1=differential.  With ENABLE=0 the LINP/LINN
#                     pins route AROUND the PGA as two single-ended inputs, and
#                     those reach the ADC only through LINPG/LINNG (muted here).
#   R29/R30 (0x23/0x24) HP vol: [7:2] -57..+6 dB, bit1 mute (0=mute),
#                     bit0 HP enable / HP-mode.  Default 0x02 = driver off.
#   R14 (0x14) ALC 3: NGTYP[1:0] NGEN NGTHR[4:0].  PYNQ writes 0x20 = noise
#                     gate ENABLED with threshold 0.
#
# So the PYNQ mic settings apply +20 dB (PGA boost) plus +21 dB (input volume)
# = +41 dB, and every in-chip bypass or sidetone path is muted.  The steps
# below cut the input, the output and the noise gate one at a time to find out
# which one actually feeds the oscillation.
#
# Each step runs several times, alternating order, because this fault has been
# seen to flip between runs at identical register values.

import fcntl, os, sys, time
import numpy as np
from pynq import Overlay

HERE = "/home/xilinx/jupyter_notebooks"
L, N = 480, 20
ADDR = 0x3B

R4, R6 = 0x0A, 0x0C                 # record mixer L/R control 0 (MX1EN + s.e. gains)
R5, R7 = 0x0B, 0x0D                 # record mixer L/R control 1 (LDBOOST + aux)
R8, R9 = 0x0E, 0x0F                 # differential input volume / mute / enable
R14 = 0x14                          # ALC control 3 (noise gate)
R22, R23, R24, R25, R29, R30 = 0x1C, 0x1D, 0x1E, 0x1F, 0x23, 0x24

VOL = 62
HVOL = (VOL << 2) | 3               # 0xFB -> +5 dB, driver on
OPENA = [(R22, 0x21), (R24, 0x41)]  # playback mixer open
BAD = OPENA + [(R29, HVOL), (R30, HVOL)]        # the known-bad state

# name, base writes, extra writes applied on top of a fresh PYNQ config
STEPS = [
    ("control_bad    ", BAD, []),
    ("playback_off   ", [(R22, 0x01), (R24, 0x01), (R29, HVOL), (R30, HVOL)], []),
    ("rec_mixer_off  ", BAD, [(R4, 0x00), (R6, 0x00)]),
    ("input_muted    ", BAD, [(R5, 0x00), (R7, 0x00)]),
    ("lden_off       ", BAD, [(R8, 0x00), (R9, 0x00)]),
    ("gain_m12dB     ", BAD, [(R5, 0x08), (R7, 0x08), (R8, 0x03), (R9, 0x03)]),
    ("gain_0dB       ", BAD, [(R5, 0x08), (R7, 0x08), (R8, 0x43), (R9, 0x43)]),
    ("gain_p21dB     ", BAD, [(R5, 0x08), (R7, 0x08), (R8, 0xB3), (R9, 0xB3)]),
    ("hp_driver_off  ", OPENA, [(R29, 0x02), (R30, 0x02)]),
    ("hp_vol_m57dB   ", OPENA, [(R29, 0x03), (R30, 0x03)]),
    ("nGate_off      ", BAD, [(R14, 0x00)]),
]

ol = Overlay(os.path.join(HERE, "fir.bit"))
audio = ol.audio_codec_ctrl_0
ffi = audio._ffi
audio.configure(); MMIO_LEN = audio.mmio.length
UIO = audio.uio_index
ffi.cdef("void* capture_begin(unsigned int n, int uio); int capture_block(void* h, unsigned int* b, unsigned int n); void capture_end(void* h);")
lib = ffi.dlopen(os.path.join(HERE, "libaudio_stream.so"))
rbuf = ffi.new("unsigned int[]", 2 * L)

fd = os.open("/dev/i2c-1", os.O_RDWR)
fcntl.ioctl(fd, 0x0703, ADDR)


def w(reg, val):
    os.write(fd, bytes([0x40, reg, val]))


def rd(reg):
    try:
        os.write(fd, bytes([0x40, reg]))
        return "%02X" % os.read(fd, 1)[0]
    except Exception:
        return "??"


def reset():
    audio.configure()
    audio.set_volume(VOL)
    audio.select_microphone()
    time.sleep(0.2)


def meas():
    h = lib.capture_begin(MMIO_LEN, UIO)
    if h == ffi.NULL:
        return -1.0, -1.0, 0, 0, 0.0
    rs = []
    raws = []
    for k in range(N):
        if lib.capture_block(h, rbuf, L) != L:
            break
        a = np.frombuffer(bytes(ffi.buffer(rbuf, 4 * 2 * L)), dtype=np.int32)
        v = a.reshape(-1, 2)[:, 0]
        raws.append(v)
        f = v.astype(np.float64) / 256.0
        rs.append(float(np.sqrt(np.mean(f ** 2))))
    lib.capture_end(h)
    if not rs:
        return -1.0, -1.0, 0, 0, 0.0
    cat = np.concatenate(raws)
    return (float(np.median(rs)), float(np.max(np.abs(cat / 256.0))),
            int(cat.min()), int(cat.max()), float(cat.mean()))


# Readback sanity check: if the read protocol is wrong the 0x columns below are
# meaningless, so test them against a value that was just written.
w(R29, 0xFB)
print("readback check: wrote 0x23=0xFB, read 0x23=%s (mismatch => ignore 0x columns)" % rd(R29))

PASSES = 3
res = {}
for p in range(PASSES):
    order = STEPS if p % 2 == 0 else list(reversed(STEPS))
    for name, base, extra in order:
        reset()
        for reg, val in base:
            w(reg, val)
        for reg, val in extra:
            w(reg, val)
        time.sleep(0.2)
        rms, pk, rmin, rmax, rmean = meas()
        res.setdefault(name, []).append((rms, rmean))
        print("  pass%d  %s rms16=%8.1f pk=%7.1f | raw24 min=%8d max=%8d mean=%9.0f"
              % (p, name, rms, pk, rmin, rmax, rmean))

print("=" * 78)
print("  step            median rms16   trials          mean raw24")
for name, base, extra in STEPS:
    vs = res[name]
    print("  %s %9.1f      %s  %9.0f"
          % (name, float(np.median([v[0] for v in vs])),
             " ".join("%.0f" % v[0] for v in vs),
             float(np.median([v[1] for v in vs]))))

reset()
print("restored PYNQ default codec config")
os.close(fd)
print("=" * 78)
