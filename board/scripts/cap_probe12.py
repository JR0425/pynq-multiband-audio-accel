"""cap_probe12.py -- 话筒这一路到底活不活?寄存器全量回读 + 增益扫描。

2026-10-14。cap_probe11 坐实了前三个晚上的「自激」是 24 位补码没做符号扩展的
算术假象之后,剩下唯一的问题是:**话筒根本没有信号**。fir_live.py 跑 30 秒,
每秒一行电平表从头到尾贴在 rms 7~51(满量程 32767),说话也不动。

这一轮不去猜。做两件事:

  A. 把 codec 寄存器**全量回读**(0x00~0x3F),配置前 / 配置后各一次。
     当年 cap_probe6/7 那套「哪个旋钮能止住自激」是在假象上量出来的,
     寄存器地址和字段映射**从来没被真信号验过** —— 先把 PYNQ 到底写了什么
     看清楚,不照驱动名字猜。

  B. **扫一遍话筒增益**:差分输入音量从 -12 dB 一路扫到 +35.25 dB(0x03→0xFF),
     外加「LDBOOST 关掉」「静音位置 0」「差分使能位 0」三个控制点,
     每一档采一段,看 ADC 的噪声底跟不跟着动。

判据(**先写死在这,免得事后圆**):

  · 噪声底**单调跟着增益走**(相差 47 dB 的两个码差出几十 dB)
      -> 模拟前端(PGA + ADC)是活的、寄存器也真写进去了。
         那问题在话筒那一侧:换能器本体、插孔 U41、或者话筒偏置(MICBIAS)。
  · 噪声底**一条平线** -> 前端没通,或者写根本没落到芯片上(地址/字段错)。
  · 「静音位」那一行(b1=0)和 b1=1 **一模一样**
      -> 说明这个字段压根没接上,那么扫描的结论也不能信 —— 先解决"写没写对"。

耳机全程 set_volume(0) = 静音(R29/R30 = 0x03,bit1=0 是 mute),不会啸叫;
这个脚本只用 capture 那一路,**不开 I2S 输出**。

跑法(板上,和 fir_live.py 同一条 sudo 命令):
    echo xilinx | sudo -S sh -c "cd /home/xilinx/jupyter_notebooks && \
        XILINX_XRT=/usr PYTHONIOENCODING=utf-8 \
        /usr/local/share/pynq-venv/bin/python3 cap_probe12.py"
"""

import fcntl, os, time
import numpy as np
from pynq import Overlay

HERE = "/home/xilinx/jupyter_notebooks"
L = 480                 # 10 ms 一块
NB = 150                # 1.5 秒;前 0.5 秒丢掉(直流建立),后 1 秒才算
SKIP = 75
ADDR = 0x3B             # ADAU1761 的 7 位 I2C 地址
FULL24 = 1 << 24
HALF24 = 1 << 23


def hr(s):
    print()
    print("=" * 78)
    print(s)
    print("=" * 78)


# ---------------- I2C:寄存器读/写 ----------------
# 帧格式和 cap_probe11 用的那套一致(它已经跑通过):
#   写:[0x40, reg_lo, data]      读:[0x40, reg_lo] 然后读 1 字节
fd = os.open("/dev/i2c-1", os.O_RDWR)
fcntl.ioctl(fd, 0x0703, ADDR)


def rd(reg):
    try:
        os.write(fd, bytes([0x40, reg]))
        return os.read(fd, 1)[0]
    except Exception:
        return None


def wr(reg, val):
    try:
        os.write(fd, bytes([0x40, reg, val]))
        return True
    except Exception:
        return False


DUMP_REGS = list(range(0x00, 0x40))


def dump(tag):
    vals = [rd(r) for r in DUMP_REGS]
    print("   %s" % tag)
    for i in range(0, len(DUMP_REGS), 8):
        cells = []
        for j in range(8):
            r = DUMP_REGS[i + j]
            v = vals[i + j]
            cells.append("%02X:%s" % (r, "??" if v is None else "%02X" % v))
        print("     " + " ".join(cells))
    return vals


def diff_lines(a, b, tag):
    ch = [(DUMP_REGS[i], a[i], b[i]) for i in range(len(DUMP_REGS))
          if a[i] != b[i]]
    print("   %s:%s" % (tag,
                        ("没变" if not ch else "")))
    for r, x, y in ch:
        print("     %02X: %s -> %s" % (r, "??" if x is None else "%02X" % x,
                                       "??" if y is None else "%02X" % y))
    return ch


# ---------------- overlay / 驱动 ----------------
hr("0. 加载 overlay,读一遍 codec 寄存器(到这儿还没碰音频驱动)")
ol = Overlay(os.path.join(HERE, "fir.bit"))
print("   IP:%s" % sorted(ol.ip_dict.keys()))
d_loaded = dump("overlay 刚加载完")

audio = ol.audio_codec_ctrl_0

UIO_NAME = "audio-codec-ctrl"
IIC = 1
MMIO_LEN = 0x10000


def find_uio(name):
    table = []
    for i in range(64):
        try:
            with open("/sys/class/uio/uio%d/name" % i) as f:
                table.append((i, f.read().strip()))
        except IOError:
            pass
    for i, n in table:
        if n == name:
            return i, table
    for i, n in table:
        if "audio" in n.lower():
            return i, table
    return None, table


UIO, UIO_TABLE = find_uio(UIO_NAME)
print("   uio 设备表:%s -> 用 uio%s" % (UIO_TABLE, UIO))
if UIO is None:
    print("   [X] overlay 没把 audio IP 绑到 uio 上,到此为止")
    raise SystemExit(1)

try:
    ffi = audio._ffi
except AttributeError:
    import cffi
    ffi = cffi.FFI()

ffi.cdef("""
    void* capture_begin(unsigned int mmio, int uio);
    int   capture_block(void* h, unsigned int* buf, unsigned int n);
    void  capture_end(void* h);
""")
lib = ffi.dlopen(os.path.join(HERE, "libaudio_stream.so"))

# ---------------- 1. 配置前后对照 ----------------
hr("1. configure() / set_volume(0) / select_microphone() 各干了什么")
audio.configure()
time.sleep(0.3)
d_cfg = dump("audio.configure() 之后")
diff_lines(d_loaded, d_cfg, "  overlay->configure 变了")

audio.set_volume(0)
time.sleep(0.2)
d_vol = dump("set_volume(0) 之后(耳机静音)")
diff_lines(d_cfg, d_vol, "  configure->set_volume(0) 变了")

audio.select_microphone()
time.sleep(0.3)
d_mic = dump("select_microphone() 之后")
diff_lines(d_vol, d_mic, "  set_volume->select_microphone 变了")

for r, nm in ((0x23, "R29 耳机左音量"), (0x24, "R30 耳机右音量")):
    v = rd(r)
    print("   %s = %s (要静音应该是 03)" % (nm, "??" if v is None else "%02X" % v))

# ---------------- 2. 采集:增益扫描 ----------------
hr("2. 话筒增益扫描(只看 capture,不开 I2S 输出)")
rbuf = ffi.new("unsigned int[]", 2 * L)


def capture_sec():
    """采 1.5 秒,丢掉前 0.5 秒(直流建立),返回后 1 秒的统计。
    -> (去直流 rms, 峰, 直流估计) 都换算到 int16 域(满量程 32767)"""
    h = lib.capture_begin(MMIO_LEN, UIO)
    if h == ffi.NULL:
        return None
    seg, dc = [], None
    try:
        for b in range(NB):
            if lib.capture_block(h, rbuf, L) != L:
                break
            a = np.frombuffer(bytes(ffi.buffer(rbuf, 4 * 2 * L)), dtype=np.int32)
            c = a.reshape(-1, 2).astype(np.int64) & 0xFFFFFF
            s = np.where(c >= HALF24, c - FULL24, c)
            v = s[:, 0].astype(np.float64) / 256.0       # 左声道
            if b >= SKIP:
                if dc is None:
                    dc = float(v.mean())
                seg.append(v - dc)
    finally:
        lib.capture_end(h)
    if not seg:
        return None
    x = np.concatenate(seg)
    return (float(np.sqrt(np.mean(x * x))), float(np.abs(x).max()),
            0.0 if dc is None else dc)


# 每一档:(标签, 要写的寄存器)。标了 ★ 的是"当前 fir_live.py 在用的那一组"。
ROWS = [
    ("未选话筒(configure 之后的默认)", None),
    ("PYNQ 原样 select_microphone", []),
    ("输入全关 R5/R7=0x00", [(0x0B, 0x00), (0x0D, 0x00)]),
    ("vol -12.0 dB R8/R9=0x03", [(0x0B, 0x08), (0x0D, 0x08),
                                 (0x0E, 0x03), (0x0F, 0x03)]),
    ("vol   0.0 dB R8/R9=0x43", [(0x0B, 0x08), (0x0D, 0x08),
                                 (0x0E, 0x43), (0x0F, 0x43)]),
    ("vol +21.0 dB R8/R9=0xB3", [(0x0B, 0x08), (0x0D, 0x08),
                                 (0x0E, 0xB3), (0x0F, 0xB3)]),
    ("vol +27.0 dB R8/R9=0xD3 ★", [(0x0B, 0x08), (0x0D, 0x08),
                                   (0x0E, 0xD3), (0x0F, 0xD3)]),
    ("vol +35.2 dB R8/R9=0xFF", [(0x0B, 0x08), (0x0D, 0x08),
                                 (0x0E, 0xFF), (0x0F, 0xFF)]),
    ("boost+20 上 vol+27", [(0x0B, 0x10), (0x0D, 0x10),
                            (0x0E, 0xD3), (0x0F, 0xD3)]),
    ("vol+27 但差分位关 R8/R9=0xD2", [(0x0B, 0x08), (0x0D, 0x08),
                                      (0x0E, 0xD2), (0x0F, 0xD2)]),
    ("vol+27 但静音位置位 R8/R9=0xD1", [(0x0B, 0x08), (0x0D, 0x08),
                                        (0x0E, 0xD1), (0x0F, 0xD1)]),
]

print()
print("   %-32s %9s %9s %9s %s" % ("设置", "去直流rms", "峰", "直流", "相对首行"))
print("   " + "-" * 74)
ref = None
for label, writes in ROWS:
    if writes is None:
        audio.deselect_inputs()
        time.sleep(0.4)
    else:
        for r, v in writes:
            wr(r, v)
        time.sleep(0.4)
    g = capture_sec()
    if g is None:
        print("   %-32s   没采到" % label)
        continue
    rms, pk, dc = g
    if ref is None:
        ref = rms if rms > 0 else 1e-9
    db = 20.0 * np.log10(rms / ref) if rms > 0 else float("-inf")
    back = " ".join("%02X=%s" % (r, "%02X" % rd(r) if rd(r) is not None else "??")
                    for r, _ in writes) if writes else "(没写)"
    print("   %-32s %9.1f %9.0f %9.0f %+7.1f dB" % (label, rms, pk, dc, db))
    print("       回读:%s" % back)

# 收尾:把耳机也关掉那一路留着,不恢复 PYNQ 原样 —— 下一趟跑 fir_live.py
# 自己会重新写一遍,不留状态。
hr("3. 怎么读这张表")
print("""   · 第 1 行是"根本没接话筒那一路"的底噪。
   · 如果 vol 从 0x03 扫到 0xFF 那四行**几乎一样** -> 增益字段没生效,
     先查地址/字段,别急着怪话筒。
   · 如果那四行**每档差 6~12 dB 往上走** -> PGA+ADC 是活的,话筒那一段
     (换能器 / 插孔 U41 / MICBIAS)才是问题。
   · 「输入全关」「静音位」两行是控制组:它们要是和相邻档一样,说明
     这个字段压根没接上,前面所有扫描结论都不算数。
   · 「未选话筒」和「PYNQ 原样」差得多 -> 说明 select_microphone 才是把
     话筒接进来的那一下,值得把 configure() 里那几笔也翻出来看。
""")
print("   done")
