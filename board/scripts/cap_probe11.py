# cap_probe11.py -- 加载 overlay 之后什么都不配就收 + 左右声道都打 + 逐块打电平
#                   + **同一份数据同时按"原样 / 符号修正"两个域算 rms 和 min/max**
#
# 2026-10-13 深夜,接 cap_probe10。§11.9 到这里只剩两条待查,这是其中 a 条的探针。
#
# 写这个探针的过程中,回头核 probe10 / cap_probe2 的**原始数字**,核出一条一直没人
# 核过的算术:
#
#   板上 I2S 收到的那个 32 位寄存器,低 24 位是二进制补码,而且**没有做符号扩展**
#   (bits[31:24] = 0)。两组已有的原始打印就是证据:
#
#     probe10 的 raw dump:  ... 6  65525  8  0  65530  0  65535  65534 ...
#     cap_probe2 段2:       min=7815  max=16777199
#
#   按 `content/256` 读(项目里所有脚本现在的读法),65525 是个"大正数";
#   按 24 位补码读,0x00FFFF00 = -256 -> -1。cap_probe2 的 min/max 同理:
#   7815 -> +7815,16777199 -> -17 —— **两个都贴着 0**。也就是说那段被记成
#   "在 0~2^24 整幅乱撞"的原始数据,真实含义是"一直贴着数字零、幅度十几个 LSB"。
#
#   后果是一步算术假象:一个**跨零**的近零信号,按 content/256 会一半落在 0 附近、
#   一半落在 65535 附近,于是 rms = sqrt(65535^2/2) = **46341**。
#   回看 probe8/9/10 那些"满"的数:42974 45605 43285 44964 46625 45838 43947 43189
#   —— 全都贴着 46341。probe10 dump 里那 8 个采样,手算:原样域 46337,修正域 5.7。
#
#   而 fir_live.py 的 adc_to_i16() 把同一份 content 拿去**去直流**(减均值)再除 256、
#   clip 到 ±32767:一个跨零的近零信号,均值必落在 0 与 2^24 之间,减完就成**满幅
#   方波** —— 这正好能解释"进核中位 13905 峰 32767 贴满",也能解释耳机里那声很响的
#   噪声(那是这步算术做出来的方波,不是麦克风的声音)。
#   注意 i32_to_i16_chan() 的 `>> 8` 再 astype(int16) 其实**是对的** ——
#   int16 的截断正好把符号位搬对了。错的只有 `/256.0` 那条浮点路径。
#
# 所以这一轮其实要问的是:
#
#   ① **同一个块、同一份数据**,两个域各算一遍。看"满"的那些块在修正域里到底是
#      几千几万(真信号),还是个位数(近零)。
#   ② 顺带补上 a 条要的对照:**加载 overlay 之后什么都不配**就收(no_cfg)
#      vs `configure()` 之后收(after_cfg) —— 差别就是"codec 配置有没有关系"。
#   ③ 左右声道都打、每块都打,并 dump 若干块的**头 4 / 尾 4 个十六进制原值**。
#      十六进制是这轮的底牌,任何"表示法"之争看它。
#
# 判读表(**上板之前写死**,老规矩):
#
#   三种条件下,修正域 rms 整个 3 秒都是个位数~几十,而原样域贴着 46000
#       -> **坐实**:收到的数据一直贴着数字零;"爬满"是"24 位没做符号扩展"的算术
#          假象,§11.9 那个故障在**数据里不存在**。方向改成两件事:
#          (i) 修 adc_to_i16 的读法(先在补码域上算,别在 content 上减均值);
#          (ii) 回到"麦克风为什么没信号" —— 已知的 U41 插孔检测那条线。
#          probe12(噪声门)不用做了。
#
#   修正域也爬到几千以上(某个块 min/max 真到 ±2 万)
#       -> 数据是真在爬,§11.9 成立,符号修正只是顺带查出的另一个问题。
#          再看 min/max 是不是填满 ±32767:"单调从 0 涨到 32767" 和 "来回乱撞"
#          要分开。继续按 a/b 两条查,probe12 照做。
#
#   no_cfg 与 after_cfg 曲线明显不同
#       -> codec 那套 configure()/set_volume()/select_microphone() 有关系,记下来。
#          两条一样 -> 和配置无关。
#
#   no_cfg 一块都搬不满(capture_block 返回 < 480)
#       -> codec 没被点亮 / I2S 没在跑,这一档无效 —— **别当成"不爬"**。
#          看第 0 节打出来的驱动源码判断 Audio.__init__ 有没有顺手 configure()。

import fcntl, inspect, os, time
import numpy as np
from pynq import Overlay

HERE = "/home/xilinx/jupyter_notebooks"
L = 480                 # 10 ms 一块
NB = 300                # 300 块 = 3 秒
PERBLOCK = 100          # 前 100 块逐块打;之后每 20 块给一组中位
ADDR = 0x3B
SNAP_REGS = (0x11, 0x12, 0x13, 0x14, 0x15, 0x16)
DUMP_BLOCKS = (0, 1, 2, 19, 20, 21, 199, 200, 201, 299)

COND = [
    ("no_cfg   ", "cap"),   # 加载 overlay 后什么都不配,只收      <- a 条的主角
    ("after_cfg", "cap"),   # 配过那一套之后只收                    <- 对照
    ("str_zero ", "str"),   # stream_begin + duplex_block,TX 全 0   <- 已知"会爬"的对照
]

FULL24 = 1 << 24
HALF24 = 1 << 23


def hr(s):
    print()
    print("=" * 78)
    print(s)
    print("=" * 78)


fd = os.open("/dev/i2c-1", os.O_RDWR)
fcntl.ioctl(fd, 0x0703, ADDR)


def rd(reg):
    try:
        os.write(fd, bytes([0x40, reg]))
        return "%02X" % os.read(fd, 1)[0]
    except Exception:
        return "??"


def snap():
    return " ".join("%02X=%s" % (r, rd(r)) for r in SNAP_REGS)


def s24(x):
    """24 位补码 -> 有符号整数。这个函数就是这一轮的主角。"""
    return x - FULL24 if x >= HALF24 else x


hr("0. 加载 overlay —— 到这行为止一次 I2C 写都没做")
ol = Overlay(os.path.join(HERE, "fir.bit"))
print("   IP:%s" % sorted(ol.ip_dict.keys()))
print("   加载后 codec %s : %s" % (SNAP_REGS, snap()))

print()
print("   取 ffi / mmio / uio / iic 只能从 Audio 驱动对象上拿。")
print("   为了知道『碰它』会不会顺手把 codec 配一遍,把驱动源码原样打出来 ——")
print("   别再靠记忆猜(audio_duplex_probe.py 那一节的老办法):")
try:
    import pynq.lib.audio as _pa
    for fn in (_pa.Audio.__init__, _pa.Audio.configure):
        print("   ---- %s ----" % fn.__qualname__)
        for line in inspect.getsource(fn).splitlines():
            print("   | " + line)
except Exception as e:
    print("   (驱动源码取不到:%r —— 那就只看下面的寄存器前后对照)" % (e,))

audio = ol.audio_codec_ctrl_0
print()
print("   碰了 audio 驱动之后 %s : %s" % (SNAP_REGS, snap()))
print("   前后一样 => 碰驱动本身不写寄存器,下面 no_cfg 才是干净的『什么都不配』")

# uio_index / iic_index 这两个字段是 configure() 里才填上的 —— 而这轮偏偏不能
# configure()。上一版就死在这儿:print 那行 "TypeError: %d format: a number is
# required, not NoneType"。所以指数不许问驱动要,自己找:
# overlay 一加载,uio 设备就按名字绑好了,扫 /sys/class/uio/uio*/name 就能拿到
# (C 库就是 open("/dev/uio%d", uio_index),见 audio_stream.cpp 的 new_handle)。
# 找不到就把整张表打出来 —— 别猜名字。
UIO_NAME = "audio-codec-ctrl"
IIC = 1                 # pynq 默认 iic_index=1(见 fir_audio_loop.py:225 那行注释)
MMIO_LEN = 0x10000      # overlay 给 audio IP 的 range,见 board/overlay/build_fir.tcl:201


def find_uio(name):
    """-> (index 或 None, [(i, 名字), ...] 整张表)"""
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
            print("   (没有叫 '%s' 的,按含 audio 的取 uio%d '%s')" % (name, i, n))
            return i, table
    return None, table


UIO, UIO_TABLE = find_uio(UIO_NAME)
print("   uio 设备表:%s" % (UIO_TABLE,))
print("   uio '%s' -> %s ; MMIO_LEN=0x%X ; IIC=%d"
      % (UIO_NAME, UIO, MMIO_LEN, IIC))
if UIO is None:
    print("   [X] overlay 没把 audio IP 绑到 uio 上 —— 这轮全靠它,到此为止")
    raise SystemExit(1)

try:
    ffi = audio._ffi
except AttributeError:
    import cffi
    ffi = cffi.FFI()

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


def channels():
    """-> (content 原样[L,2], 24 位补码修正后[L,2]),都是 int64"""
    a = np.frombuffer(bytes(ffi.buffer(rbuf, 4 * 2 * L)), dtype=np.int32)
    c = a.reshape(-1, 2).astype(np.int64) & 0xFFFFFF
    s = np.where(c >= HALF24, c - FULL24, c)
    return c, s


def rms_f(x):
    x = x.astype(np.float64)
    return float(np.sqrt(np.mean(x * x)))


def stat(c_col, s_col):
    """-> (原样域 rms, 修正域 rms, 修正域 min, 修正域 max),都在 int16 域"""
    v = s_col.astype(np.float64) / 256.0
    return (rms_f(c_col.astype(np.float64) / 256.0), rms_f(v),
            float(v.min()), float(v.max()))


def hx(v):
    return " ".join("%08X" % int(x) for x in v)


def run_cond(kind):
    h = (lib.capture_begin(MMIO_LEN, UIO) if kind == "cap"
         else lib.stream_begin(MMIO_LEN, 40, UIO, IIC))
    if h == ffi.NULL:
        return [], {}, "!! 句柄开不出来,这一档作废", (0, 0)
    rows, dumps, bad = [], {}, ""
    cmin, cmax = FULL24, -1
    try:
        for b in range(NB):
            got = (lib.capture_block(h, rbuf, L) if kind == "cap"
                   else lib.duplex_block(h, rbuf, wbuf, L))
            if got != L:
                bad = "块 %d 只搬到 %d 个(等 I2S 状态超时)—— 提前收" % (b, got)
                break
            c, s = channels()
            cmin = min(cmin, int(c.min()))
            cmax = max(cmax, int(c.max()))
            rows.append((b, stat(c[:, 0], s[:, 0]), stat(c[:, 1], s[:, 1])))
            if b in DUMP_BLOCKS:
                dumps[b] = (c[:4, 0], c[-4:, 0], c[:4, 1], c[-4:, 1])
    finally:
        if kind == "cap":
            lib.capture_end(h)
        else:
            lib.stream_end(h)
    return rows, dumps, bad, (cmin, cmax)


def show(name, rows, dumps, bad, crange):
    print()
    print("---- %s ----" % name)
    if bad:
        print("   [X] %s" % bad)
    if not rows:
        print("   一块都没收到")
        return
    cmin, cmax = crange
    print("   全场 content(寄存器原值) %d .. %d  ->  24 位补码 %d .. %d"
          % (cmin, cmax, s24(cmin), s24(cmax)))
    print("                                        ->  int16 域 %.1f .. %.1f  (满量程 ±32767)"
          % (s24(cmin) / 256.0, s24(cmax) / 256.0))
    print()
    print("   块     L 原样/修正      L 修正域 min..max     R 原样/修正      R 修正域 min..max")
    for b, Ls, Rs in rows:
        if b >= PERBLOCK:
            continue
        print("  %3d  %9.0f %9.0f   %9.0f..%9.0f   %9.0f %9.0f   %9.0f..%9.0f"
              % (b, Ls[0], Ls[1], Ls[2], Ls[3], Rs[0], Rs[1], Rs[2], Rs[3]))
    for g in range(PERBLOCK, NB, 20):
        grp = [r for r in rows if g <= r[0] < g + 20]
        if not grp:
            continue
        m = lambda i, j: float(np.median([r[i + 1][j] for r in grp]))
        print("  %3d-%3d  %9.0f %9.0f   %9.0f..%9.0f   %9.0f %9.0f   %9.0f..%9.0f  (中位)"
              % (g, g + 19, m(0, 0), m(0, 1), m(0, 2), m(0, 3),
                 m(1, 0), m(1, 1), m(1, 2), m(1, 3)))
    print()
    for b in DUMP_BLOCKS:
        if b not in dumps:
            continue
        Lh, Lt, Rh, Rt = dumps[b]
        print("  raw blk%3d  L head %s  tail %s" % (b, hx(Lh), hx(Lt)))
        print("              R head %s  tail %s" % (hx(Rh), hx(Rt)))


hr("1. no_cfg —— 加载 overlay 之后什么都不配,直接收(最要紧的一档,必须第一个跑)")
print("   此刻寄存器 %s   —— 全程零 I2C 写" % snap())
rows, dumps, bad, cr = run_cond("cap")
show("no_cfg", rows, dumps, bad, cr)

hr("2. 现在才正式配 codec(PYNQ 那套)")
audio.configure()
audio.set_volume(62)
audio.select_microphone()
time.sleep(0.5)
print("   配完 %s : %s" % (SNAP_REGS, snap()))

hr("3. after_cfg / str_zero / after_cfg(第二遍)")
for name, kind in ((COND[1][0], COND[1][1]), (COND[2][0], COND[2][1]),
                   ("after_cfg*", "cap")):
    rows, dumps, bad, cr = run_cond(kind)
    show(name, rows, dumps, bad, cr)

audio.configure()
audio.set_volume(62)
audio.select_microphone()
os.close(fd)
print()
print("=" * 78)
print("restored PYNQ default codec config")
print("=" * 78)
print("""
怎么读这张表(先在脑子里定好,再对数字):

  每行的 "原样" 就是项目里一直用的 content/256;"修正" 是先把低 24 位按补码
  解释、再除 256。两列差多少,就是这个探针的全部重点 —— 差到 4 个数量级,
  §11.9 就不是硬件故障,是读法。

  先看每个条件的 "全场 content .. -> int16 域 .." 那一行。它是整轮最硬的证据:
  真在自激,它应该铺满 ±32767;只有十几个 LSB,那就是数字零附近的抖动。

  no_cfg 与 after_cfg / str_zero 三条曲线是不是一样 —— 一样就说明 codec 那套
  configure()/set_volume()/select_microphone() 跟这件事**没关系**(那 a 条
  第 2 个分支就排掉了,只剩"读通路"这条)。

  dump 那几行是十六进制原值,不参与任何解释 —— 表示法上有争议时看它。
""")
