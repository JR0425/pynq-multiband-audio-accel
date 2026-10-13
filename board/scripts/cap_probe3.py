import fcntl, os, sys, time
import numpy as np
from pynq import Overlay

HERE = "/home/xilinx/jupyter_notebooks"
L, N = 480, 20
ADDR = 0x3B
R8, R9 = 0x0E, 0x0F
R22, R23, R24, R25, R29, R30 = 0x1C, 0x1D, 0x1E, 0x1F, 0x23, 0x24
VOL = 62
HVOL = (VOL << 2) | 3
PROBE = (R8, R9, R22, R23, R24, R25, R29, R30)

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
        return -1.0
    rs = []
    for k in range(N):
        if lib.capture_block(h, rbuf, L) != L:
            break
        a = np.frombuffer(bytes(ffi.buffer(rbuf, 4 * 2 * L)), dtype=np.int32)
        v = a.reshape(-1, 2)[:, 0]
        rs.append(float(np.sqrt(np.mean((v.astype(np.float64) / 256.0) ** 2))))
    lib.capture_end(h)
    return float(np.median(rs))
STEPS = [
    ("A nothing", []),
    ("B all 4", [(R22, 0x21), (R24, 0x41), (R29, HVOL), (R30, HVOL)]),
    ("C R22+R24", [(R22, 0x21), (R24, 0x41)]),
    ("D R29+R30", [(R29, HVOL), (R30, HVOL)]),
    ("E 4+R23R25=0", [(R23, 0x00), (R25, 0x00), (R22, 0x21), (R24, 0x41), (R29, HVOL), (R30, HVOL)]),
    ("F 4 vol low", [(R22, 0x21), (R24, 0x41), (R29, 0x03), (R30, 0x03)]),
    ("G 4+R8R9=0", [(R8, 0x00), (R9, 0x00), (R22, 0x21), (R24, 0x41), (R29, HVOL), (R30, HVOL)]),
]

print("=" * 74)
print("  step            rms16 |  08 09 22 23 24 25 29 30")
for name, writes in STEPS:
    reset()
    for reg, val in writes:
        w(reg, val)
    time.sleep(0.2)
    print("  %-14s %7.1f | %s" % (name, meas(), " ".join(rd(x) for x in PROBE)))
os.close(fd)
print("=" * 74)
