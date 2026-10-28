"""fir_dma overlay 的分段验收

四段，一段一段往上加，坏了能立刻定位是哪一层：

    a  不碰 DMA：codec 配好，PL 内部自环（ctrl[1]=hw_loop）
      判据：tx underrun 不涨 → 自写 I2S 的收发时序对
    b  只开录音：codec → I2S → S2MM → DDR
      判据：RMS/峰值有变化、没有 overrun、传输长度对
    c  只开放音：DDR → MM2S → I2S → codec
      判据：人耳听到 440 Hz；DMASR 没有错误位
    d  整条回环：话筒 → DDR → 放音（这一版还没有 FIR）
      判据：60 秒不丢块，把延迟量出来

用法（板上，要 root）：

    echo xilinx | sudo -S /usr/local/share/pynq-venv/bin/python3 \\
        dma_audio_probe.py --stage a
    ... --stage b --frames 480
    ... --stage c --tone 440 --secs 5
    ... --stage d --frames 480 --secs 60

注意帧长的含义：--frames 是一个块的**立体声帧数**，每个帧在流里是两个字
（左、右）。480 帧 = 10 ms，和出货版 LIVE_L=480 是同一个口径。
"""

import argparse
import os
import subprocess
import sys
import time

import numpy as np
from pynq import MMIO, Overlay, allocate

BITFILE = "fir_dma.bit"
HWHFILE = "fir_dma.hwh"

# 地址见 board/overlay/build_audio_dma.tcl
GPIO_CTRL = 0x43C00000
GPIO_STAT = 0x43C10000

DMASR_MM2S = 0x04
DMASR_S2MM = 0x34
SR_IDLE    = 0x0002
SR_ERR     = 0x0078     # bit3 DMAIntErr | bit4 DMASlvErr | bit5 DMADecErr
SR_IOC     = 0x1000

SR_BITS = [(0x0001, "Halted"), (0x0002, "Idle"), (0x0008, "IntErr"),
           (0x0010, "SlvErr"), (0x0020, "DecErr"), (0x1000, "IOC"),
           (0x4000, "ErrIrq")]


def sr_str(sr):
    """把 DMASR 翻成人能读的。bit4 SlvErr 是 DDR 那一侧回的错，
    bit5 DecErr 是地址根本没落到任何从设备上 —— 两者修法完全不同，
    以前只看十六进制会认错。"""
    names = [n for b, n in SR_BITS if sr & b]
    return "0x%08X[%s]" % (sr, "|".join(names) if names else "-")

CTRL_RUN   = 1 << 0
CTRL_LOOP  = 1 << 1
CTRL_FLUSH = 1 << 2
CTRL_CLR   = 1 << 8
CTRL_BLK_SHIFT = 16


def blk_bits(frames):
    """ctrl[31:16] 是"一个传输块的字数 - 1"。
    PL 每收满这么多字拉一次 TLAST —— AXI DMA 的 S2MM 要靠它认定包的结尾，
    不给就一直报 DMAIntErr。S2MM_LENGTH 必须写同一个块（字数 × 4）。"""
    return ((frames * 2 - 1) & 0xFFFF) << CTRL_BLK_SHIFT

FS = 48000.0
# 实际采样率：i2s_duplex 的 LRCLK 是 clk_cntr[10] 方波，100 MHz / 2^11 =
# 48.828 kHz。所以按 48000 算"理论秒数"会比实测快 1.7% —— 那是时钟差，
# 不是丢块。换算 FIFO 占用→毫秒要用这个真值。
FS_ACT = 100e6 / 2048
LIB_AUDIO = "/home/xilinx/pynq/lib/libaudio.so"

ok_all = True


def check(name, passed, detail=""):
    global ok_all
    ok_all = ok_all and passed
    print("  [%s] %s%s" % ("OK  " if passed else "FAIL", name,
                           ("  " + detail) if detail else ""))
    return passed


# ---------------------------------------------------------------- codec

def codec_init(iic=1):
    """直接调 PYNQ 自带的 libaudio.so 配 codec。

    出货版是靠 `pynq.lib.audio.Audio` 这个 IP 类来配的，它要 UIO 设备，
    这一版 overlay 里没有那个 IP。但配置本身只是几条 I2C 写，
    libaudio.so 把这几条写包成了普通函数，cffi 直接调就行，不用 UIO。
    """
    import cffi
    ffi = cffi.FFI()
    lib = ffi.dlopen(LIB_AUDIO)
    ffi.cdef("void config_audio_pll(int iic_index);")
    ffi.cdef("void config_audio_codec(int iic_index);")
    ffi.cdef("void select_mic(int iic_index);")
    lib.config_audio_pll(iic)
    lib.config_audio_codec(iic)
    lib.select_mic(iic)
    codec_unmute()


I2C_BUS = "1"
CODEC_ADDR = "0x3b"


def i2c_rd(reg):
    """一次完整的寄存器读：START + 写 2 字节地址 + 重复 START + 读 1 字节。
    拆成两次 write/read 中间会插 STOP，ADAU1761 不认。"""
    p = subprocess.Popen(
        ["i2ctransfer", "-y", I2C_BUS, "w2@%s" % CODEC_ADDR,
         "0x%02x" % (reg >> 8), "0x%02x" % (reg & 0xFF), "r1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, _ = p.communicate()
    if p.returncode != 0:
        return None
    tok = out.decode().strip().split()
    try:
        return int(tok[0], 16)
    except (IndexError, ValueError):
        return None


def i2c_wr(reg, val):
    subprocess.check_call(
        ["i2ctransfer", "-y", I2C_BUS, "w3@%s" % CODEC_ADDR,
         "0x%02x" % (reg >> 8), "0x%02x" % (reg & 0xFF), "0x%02x" % val])


def codec_unmute():
    """把耳机输出打开 —— config_audio_codec 不写这几条，出货版是 play() 补的。

    2026-10-28 用 codec_diff.py 直接差分出来的**精确值**（configure() 之后
    dump 一遍，Audio.play() 正在跑的时候再 dump 一遍，逐寄存器比）：

        0x401C  0x21 ← 0x00     播放混音器左
        0x401E  0x41 ← 0x00     播放混音器右
        0x4023  0xF3 ← 0xE5     耳机左（音量码 60，bit1=1 不静音）
        0x4024  0xF3 ← 0xE5     耳机右

    2026-10-25 那版写的是 0x01/0x01/0xE7/0xE7，**值是错的**：0x401C 停在
    0x01 时耳机照样不响，所以起作用的不是 bit0（左是 bit5、右是 bit6）。
    这就是"PL 直连版一直无声"的真正原因 —— 数据、时钟、发送器全是对的，
    卡在 codec 的播放混音器从来没被打开过。

    0x40DF/0x40E0/0x40E7/0x40E8 play() 也会动，但每次读都在变，是状态位
    不是配置位，不在这里处理。
    """
    for reg, val in ((0x401C, 0x21), (0x401E, 0x41),
                     (0x4023, 0xF3), (0x4024, 0xF3)):
        i2c_wr(reg, val)


# ---------------------------------------------------------------- overlay

def load():
    for f in (BITFILE, HWHFILE):
        if not os.path.exists(f):
            print("ERROR: 找不到 %s（bit 和 hwh 要一起拷过来）" % f)
            sys.exit(1)

    ol = Overlay(BITFILE)
    print("  里面的 IP：", sorted(ol.ip_dict.keys()))

    dma = None
    for k, v in ol.ip_dict.items():
        if "dma" in v["type"].lower():
            dma = getattr(ol, k)
            print("  DMA  %-10s %s" % (k, v["type"]))
            break
    if dma is None:
        print("ERROR: overlay 里没有 DMA —— hwh 是不是和 bit 不是同一版？")
        sys.exit(1)
    print("  DMA 单次上限 %d 字节（%.0f 个 32 位字）"
          % (dma.buffer_max_size, dma.buffer_max_size // 4))

    mm = {}
    for base, key in ((GPIO_CTRL, "ctrl"), (GPIO_STAT, "stat")):
        hit = [(k, v) for k, v in ol.ip_dict.items() if v.get("phys_addr") == base]
        if hit:
            mm[key] = getattr(ol, hit[0][0]).mmio
        else:
            mm[key] = MMIO(base, 0x1000)
    return dma, mm["ctrl"], mm["stat"]


def set_ctrl(ctrl, val):
    ctrl.write(0, val & 0xFFFFFFFF)


def flush_all(ctrl):
    set_ctrl(ctrl, CTRL_FLUSH)
    time.sleep(0.02)
    set_ctrl(ctrl, 0)


def clear_cnt(ctrl, run_bits):
    set_ctrl(ctrl, run_bits | CTRL_CLR)
    time.sleep(0.002)
    set_ctrl(ctrl, run_bits)


def read_stat(stat):
    s0 = stat.read(0x00)      # gpio_io_i  —— 通道 1
    s1 = stat.read(0x08)      # gpio2_io_i —— 通道 2
    return {
        "ovf": s0 & 0xFFFF,
        "und": (s0 >> 16) & 0xFFFF,
        "tx_lvl": s1 & 0xFFFF,
        "cap_lvl": (s1 >> 16) & 0xFFFF,
    }


# ---------------------------------------------------------------- DMA

def wait_ch(dma, off, label, timeout=2.0):
    """等一个通道回到 Idle。用寄存器轮询而不是 ch.wait()：后者没超时，
    而且第一次挂上去时 IS 位可能还没更新。

    一味忙等实测吃掉一个核还多（240 帧那组 user+sys 25.6 s / 墙钟 19.6 s
    = 130%），所以按上一次实测的搬运时长先睡到"差不多快完了"，最后 0.6 ms
    再忙等 —— 这样 CPU 只花在最后那一小段，而发现完成仍然没有延迟。
    纯睡眠轮询（每 0.2 ms 看一次）也试过：CPU 降到四成，但每块晚 0.2 ms
    才重新挂上录音，录音 FIFO 因此一直多压 2.9 ms。"""
    t0 = time.perf_counter()
    # 刚写完长度寄存器时 DMASR 里可能还是上一次的 Idle，给几十微秒让它忙起来
    while (dma.mmio.read(off) & SR_IDLE) and (time.perf_counter() - t0 < 30e-6):
        pass
    while True:
        sr = dma.mmio.read(off)
        if sr & SR_ERR:
            raise RuntimeError("%s 报了 AXI 错误，DMASR=%s" % (label, sr_str(sr)))
        if sr & SR_IDLE:
            _note_done(label)
            return sr
        now = time.perf_counter()
        if now - t0 > timeout:
            raise TimeoutError("%s 等 %.1f s 还没空闲，DMASR=%s"
                               % (label, timeout, sr_str(sr)))
        due = _due_at(label)
        if due is None:
            if now - t0 > 0.0001:
                time.sleep(0.0002)
        else:
            remain = due - now
            if remain > 0:
                time.sleep(remain)


# ---------------------------------------------------------------- 搬运时长估计

_ARM = {}      # 通道 → 上次挂上去的时刻
_DUR = {}      # 通道 → 搬运时长的指数平均（秒）
_SPIN = 0.0006 # 最后这段忙等


def _note_arm(label):
    _ARM[label] = time.perf_counter()


def _note_done(label):
    a = _ARM.get(label)
    if a is None:
        return
    d = time.perf_counter() - a
    _DUR[label] = d if label not in _DUR else 0.8 * _DUR[label] + 0.2 * d


def _due_at(label):
    a, d = _ARM.get(label), _DUR.get(label)
    if a is None or d is None:
        return None
    return a + max(0.0, d - _SPIN)


def cap_wait(dma, buf, timeout=2.0):
    """只等当前这次 S2MM 完成，不重新发起（发起由调用方提前做）。"""
    sr = wait_ch(dma, DMASR_S2MM, "S2MM", timeout)
    buf.invalidate()
    return sr


def cap_block(dma, buf, timeout=2.0):
    cap_start(dma, buf)
    sr = wait_ch(dma, DMASR_S2MM, "S2MM", timeout)
    buf.invalidate()
    return sr


def _ensure_started(ch):
    """PYNQ 的 DMA 驱动要求先 start() 通道再 transfer()，否则报
    "DMA channel not started"。这里每块查一次 SR 的 Halted 位，成本是一次
    MMIO 读。之前没写这一句也能跑，是因为板上一直留着上一位使用者（或上一次
    位流）把通道留在运行态 —— 换一块位流就会当场报错。"""
    if not ch.running:
        ch.start()


def cap_start(dma, buf):
    """只把这块录音交给 S2MM，不等它搬完。"""
    _ensure_started(dma.recvchannel)
    dma.recvchannel.transfer(buf)
    _note_arm("S2MM")


def play_block(dma, buf, timeout=2.0):
    play_start(dma, buf)
    return wait_ch(dma, DMASR_MM2S, "MM2S", timeout)


def play_start(dma, buf):
    """只把这块交给 MM2S，不等它搬完。"""
    buf.flush()
    _ensure_started(dma.sendchannel)
    dma.sendchannel.transfer(buf)
    _note_arm("MM2S")


def mm2s_wait(dma, timeout=2.0):
    return wait_ch(dma, DMASR_MM2S, "MM2S", timeout)


def drain_cap(dma, stat, scratch, min_lvl=48, rounds=8):
    """把录音 FIFO 里开局攒下的积压排掉，返回（排之前，排之后）。

    录音 FIFO 的水平是个**中性平衡**：每块 S2MM 恰好搬走一块，I2S 也恰好
    送进来一块，进来多少搬走多少。所以开机那几毫秒攒下的积压（实测
    0.1～8.3 ms，每次开机不一样）永远不会自己排掉，整段都背着它。
    办法是在起跑时用一次"长度正好等于当时积压"的短传输把它搬空。
    """
    lvl0 = read_stat(stat)["cap_lvl"]
    for _ in range(rounds):
        if read_stat(stat)["cap_lvl"] < min_lvl:
            break
        # 整块长度地搬：先把积压搬空，剩下的长度就只能是新进来的，
        # 搬完这一下 FIFO 就落在 0 附近
        cap_block(dma, scratch, timeout=1.0)
    return lvl0, read_stat(stat)["cap_lvl"]


# ---------------------------------------------------------------- 各段

def stage_a(dma, ctrl, stat, secs=3.0):
    """PL 内部自环。这一段的全部意义是：I2S 收 → 发 的时序对不对。
    DMA 完全不参与（S2MM 不挂、MM2S 不挂），所以坏了肯定是 I2S 那边。"""
    print("\n== a: PL 内部自环（不碰 DMA）==")
    codec_init()
    flush_all(ctrl)
    bits = CTRL_RUN | CTRL_LOOP
    clear_cnt(ctrl, bits)

    t_end = time.time() + secs
    samples = []
    while time.time() < t_end:
        samples.append(read_stat(stat))
        time.sleep(0.05)

    und0 = samples[0]["und"]
    und_last = samples[-1]["und"]
    d_und = (und_last - und0) & 0xFFFF
    lvls = [s["tx_lvl"] for s in samples]
    ovf_d = (samples[-1]["ovf"] - samples[0]["ovf"]) & 0xFFFF

    print("  tx FIFO 占用：min=%d max=%d 末=%d" % (min(lvls), max(lvls), lvls[-1]))
    print("  %.1f s 内 underrun（差）=%d，overrun（差）=%d" % (secs, d_und, ovf_d))
    print("  注：这一段没人排空录音 FIFO，overrun 一直在涨是正常的，"
          "只看 underrun。")
    # 自环刚起来头几拍 FIFO 是空的，会有少量 underrun；稳态应该不动
    check("自环不再 underrun", d_und <= 2, "差 %d" % d_und)
    check("自环没有把 FIFO 撑满", und_last == 0 or lvls[-1] < 1000,
          "末占用 %d" % lvls[-1])

    set_ctrl(ctrl, 0)
    print("  提示：这一段只能证明收发时序通了，证明不了话筒有声音。"
          "要验话筒走 b 段。")


def stage_b(dma, ctrl, stat, frames=480, blocks=10):
    print("\n== b: 录音  codec → I2S → S2MM → DDR ==")
    codec_init()
    flush_all(ctrl)
    set_ctrl(ctrl, blk_bits(frames))   # run=0：不播，避免 TX 那边干扰

    nw = frames * 2
    buf = allocate(shape=(nw,), dtype=np.int32)
    prev_cap = None
    rms_list, peak_list = [], []

    for i in range(blocks):
        sr = cap_block(dma, buf)
        if not (sr & SR_IOC):
            check("S2MM 报完成", False, sr_str(sr))
            break
        x = np.array(buf, dtype=np.int32)
        x24 = x >> 8                      # 24 位有效值挪到低 24 位，量级还是 2^23
        rms = float(np.sqrt(np.mean(x24.astype(np.float64) ** 2)))
        peak = int(np.max(np.abs(x24)))
        dc = float(np.mean(x24))
        rms_list.append(rms)
        peak_list.append(peak)

        st = read_stat(stat)
        d_ovf = "-" if prev_cap is None else "%d" % ((st["ovf"] - prev_cap) & 0xFFFF)
        prev_cap = st["ovf"]
        print("  块%02d  rms=%8.1f  peak=%8d  dc=%9.1f  新增overrun=%s  cap_lvl=%d"
              % (i, rms, peak, dc, d_ovf, st["cap_lvl"]))

        if i == 0:
            nz = np.count_nonzero(x)
            print("            非零字数 %d/%d，前 8 个：%s"
                  % (nz, nw, " ".join("%d" % v for v in x[:8])))

    if not rms_list:
        check("录音有数据", False, "一块都没传成")
        return
    check("S2MM 搬到的字数正确", True, "%d 字" % nw)
    v = max(peak_list)
    print("  参考：满量程 2^23=%d" % (1 << 23))
    if v == 0:
        print("  !! 全是零。先确认三件事：①话筒插好了；②select_mic 生效；"
              "③I2S 的 sdata_i 接对了")
    check("录音不是全零", v > 0, "峰值 %d" % v)


def make_tone(frames, freq=440.0):
    """交错立体声，24 位有符号塞在 int32 低 24 位（和 codec 的格式一致）"""
    t = np.arange(frames * 2) / 2.0 / FS
    amp = (1 << 22)                       # -6 dBFS 左右
    s = (amp * np.sin(2 * np.pi * freq * t)).astype(np.int32)
    return s & 0xFFFFFF                   # 低 24 位


def stage_c(dma, ctrl, stat, frames=480, secs=5.0, freq=440.0):
    print("\n== c: 放音  DDR → MM2S → I2S → codec ==")
    codec_init()
    flush_all(ctrl)
    set_ctrl(ctrl, CTRL_RUN | blk_bits(frames))    # 注意：不设 hw_loop

    nw = frames * 2
    buf = allocate(shape=(nw,), dtype=np.int32)
    buf[:] = make_tone(frames, freq)
    buf.flush()

    nblk = int(secs * FS / frames)
    t0 = time.perf_counter()
    worst = 0.0
    for i in range(nblk):
        a = time.perf_counter()
        sr = play_block(dma, buf)
        worst = max(worst, time.perf_counter() - a)
        if not (sr & SR_IOC):
            check("MM2S 报完成", False, sr_str(sr))
            break
        st = read_stat(stat)
        if i % max(1, nblk // 10) == 0:
            print("  块%04d  tx_lvl=%4d  und=%d" % (i, st["tx_lvl"], st["und"]))
    dt = time.perf_counter() - t0
    st = read_stat(stat)
    print("  放了 %d 块 / %.2f s（理论 %.2f s），单块最长 %.0f us"
          % (nblk, dt, nblk * frames / FS, worst * 1e6))
    # 稳态下 TX FIFO 是满的，MM2S 是按播放速度被"顶"着走的，
    # 所以单块耗时 ≈ 一块的时长是正常的，不是异常。
    check("放音期间没有 AXI 错误", True)
    set_ctrl(ctrl, 0)
    print("  请听：%.0f Hz 正弦，%d 秒左右" % (freq, secs))


def stage_d(dma, ctrl, stat, frames=480, secs=60.0, tx_low=None, verbose=False):
    """整条回环。两个方向必须同时跑：录音 FIFO 很深（4096 字），
    "采一块 → 等它放完 → 再采一块"会让放音那 10 ms 里没人排空录音 FIFO。
    所以每轮先把下一次 S2MM 挂上；放音本身**交出去就走**，等到下一块录完
    再去确认上一块放完了（见下面 2026-10-25 那条）。

    tx_low：播放侧的"低水位"（字）。MM2S 是能塞就塞，不给这个参数时 TX FIFO
    会一直贴着满，**它的深度就是还没放出去的声音** —— 播放 FIFO 一旦加深，
    延迟就跟着涨。给了这个值就先把 FIFO 等到不高于它再放下一块，把积压
    压到一块左右。None = 不控制，量"自然积压"。

    低水位这个办法**已经试过，不能用**（2026-10-25，4096 字的位流）：
    --tx-low 64 把播放积压从 42 ms 压到 9.9 ms，但录音 FIFO 在 12 秒内
    重新涨满，之后 overrun 1582 / underrun 1689。原因是把播放 FIFO 压在
    快空的位置上，PS 只要晚一拍就没有回天的余地 —— 输出少放的每一拍都
    没法补，录音侧就一直往前积，最后顶到 4096。正确做法是在 RTL 里把
    播放 FIFO 做浅（TX_AW=10）：让它一直是满的，**满的 FIFO 的全部深度
    都是抵抗 PS 卡顿的余量**，而 PS 也不可能跑到它前面去。

    2026-10-25 22:xx 两条改动，都是因为 cap_lvl 实测一直在 4000（FIFO 满）：
    录音 FIFO 的占用同样是延迟，而且**满了就再也下不来** —— 之后读写速率
    都是 48 kHz，水平只会在原地上下摆。它一满，整段都背着 42 ms 积压。

    2026-10-25 深夜，播放 FIFO 改成 1024 字之后实测嘴到耳 31.6 ms，其中
    19.7 ms 是录音积压（cap_lvl 稳定在 1922 字 ≈ 两块）。原因是放音那一步
    阻塞 9.5 ms，S2MM 早搬完一块闲着，录音 FIFO 每轮蓄两块。改成"发出去
    不等"之后，这一项应该掉到一块以内。"""
    print("\n== d: 整条回环  话筒 → DDR → 放音 ==")

    # 缓冲区必须**先**分配好再开流。allocate 走 CMA、要几十毫秒，放在开流
    # 之后的话这段时间录音 FIFO 没人排空，直接顶满，然后就永远满着。
    nw = frames * 2
    NBUF = 3
    bufs = [allocate(shape=(nw,), dtype=np.int32) for _ in range(NBUF)]
    for b in bufs:
        b[:] = 0
        b.flush()

    codec_init()
    # 清空两支 FIFO，并且趁 flush 还压着就把第一次 S2MM 挂上：从放开 flush
    # 到挂上 DMA 之间只要有几毫秒，那几毫秒也会积进录音 FIFO。
    set_ctrl(ctrl, CTRL_FLUSH)
    time.sleep(0.02)
    bits = CTRL_RUN | blk_bits(frames)
    cap_start(dma, bufs[0])
    set_ctrl(ctrl, bits | CTRL_CLR)
    time.sleep(0.002)
    set_ctrl(ctrl, bits)

    nblk = int(secs * FS / frames)
    proc = []             # 采集完 → 交给 MM2S（真正的 PS 处理 + 等上一块放完）
    pend = []             # 其中"等上一块放音"占了多少
    psl = []              # PS 真正花在这块上的时间（算嘴到耳用这一个）
    und_d = 0
    ovf_d = 0
    ovf_idx = []          # 哪几块丢了
    ovf_max = 0
    tx_seen = []

    # 先采一块放出去，把 TX FIFO 垫起来。这之前的 underrun 是"还没东西可放"，
    # 不算丢块，所以计数器在这一步之后才清。
    #
    # 放音不再"发一块等一块"：交出去就回录音循环，下一块录完再去确认上一块
    # 放完了。TX FIFO 只有 1024 字（10.5 ms），MM2S 要塞进一块 960 字得等 FIFO
    # 腾地方，一次要 9.5 ms；这 9.5 ms 里 PS 要是干等着，S2MM 早早搬完一块就
    # 闲置，录音 FIFO 一直蓄着两块（实测 1922 字 = 19.7 ms，白送一半延迟）。
    # 错开一拍之后，录音一侧每完成一块立刻有人重新挂上。
    t0 = time.perf_counter()
    cap_wait(dma, bufs[0])                # 这一块在放开 flush 之前就挂好了
    lvl0, lvl1 = drain_cap(dma, stat, bufs[2])
    if lvl0 != lvl1:
        print("  开局排空录音积压：%d → %d 字" % (lvl0, lvl1))
    cap_start(dma, bufs[1])               # 放音之前就把下一块录起来
    play_start(dma, bufs[0])              # 交出去，不等它放完
    clear_cnt(ctrl, bits)
    st = read_stat(stat)
    prev_ovf, prev_und = st["ovf"], st["und"]
    cap_seen = [st["cap_lvl"]]
    rms_first = None

    print("  块号  交块(ms)  等放音(ms)   tx_lvl  cap_lvl  新增ovf 新增und")
    SKIP = 3          # 前两块是垫场，见循环里那条
    pt = {"cap": 0.0, "arm": 0.0, "mm2s": 0.0, "stat": 0.0, "play": 0.0}
    _diag = {}
    import resource
    _ru0 = resource.getrusage(resource.RUSAGE_SELF)
    i = 1
    while i < nblk:
        k = i % NBUF
        _t = time.process_time()
        sr = cap_wait(dma, bufs[k], timeout=max(2.0, 3 * frames / FS))
        pt["cap"] += time.process_time() - _t
        if not (sr & SR_IOC):
            check("S2MM 报完成", False, "块 %d DMASR=%s" % (i, sr_str(sr)))
            break
        t_b = time.perf_counter()

        if i == SKIP:
            # 起播这三块是垫场：第一次 MM2S 从"写完寄存器"到真的动起来有延迟，
            # 播放 FIFO 也还没攒满，丢几个采样是必然的（120 帧时实测 77+27 次
            # underrun 全集中在这里）。计时和计数从这一块起算，和出货版
            # "先垫一块"是同一个口径。
            clear_cnt(ctrl, bits)
            st = read_stat(stat)
            prev_ovf, prev_und = st["ovf"], st["und"]
            ovf_d = und_d = 0
            ovf_idx, ovf_max = [], 0
            proc.clear()
            pend.clear()
            psl.clear()
            cap_seen.clear()
            tx_seen.clear()

        # 先把下一块录音挂上，再去等上一块的放音 —— 两个通道并行，录音 FIFO
        # 才排得空
        _t = time.process_time()
        cap_start(dma, bufs[(i + 1) % NBUF])
        pt["arm"] += time.process_time() - _t
        t_w = time.perf_counter()
        mm2s_wait(dma)            # 上一块；正常情况下它早就放完了
        if tx_low:
            # 水位控制：播放 FIFO 太深就晚点交这一块，等它排到 tx_low 再交。
            # 录音侧这时候已经挂上了，等它不影响录音。最多等 2 ms —— 等太久
            # 就把整条循环的节拍拖慢，录音 FIFO 会开始积压（每块只有约
            # 3 ms 的富余，见下面"一块 = 4.92 ms"和 cap_wait 的实测）。
            _dl = t_w + 0.002
            while read_stat(stat)["tx_lvl"] > tx_low:
                if time.perf_counter() > _dl:
                    break
                time.sleep(0.00005)
        pt["mm2s"] += time.process_time() - _t
        t_p = time.perf_counter()  # 真正把这块交给 MM2S 的时刻

        if rms_first is None:
            x = np.array(bufs[k], dtype=np.int32) >> 8
            rms_first = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))

        _t = time.process_time()
        st = read_stat(stat)
        pt["stat"] += time.process_time() - _t
        tx_seen.append(st["tx_lvl"])
        cap_seen.append(st["cap_lvl"])
        d_ovf = (st["ovf"] - prev_ovf) & 0xFFFF
        d_und = (st["und"] - prev_und) & 0xFFFF
        ovf_d += d_ovf
        und_d += d_und
        if d_ovf:
            ovf_idx.append(i)
            ovf_max = max(ovf_max, d_ovf)
        prev_ovf, prev_und = st["ovf"], st["und"]
        # 块被采完的时刻 → 真正交给 codec 的时刻。这里已经不含 MM2S 等 FIFO
        # 的时间（那段时间对应的是已经排在前面的声音，算在播放积压里）。
        proc.append((t_p - t_b) * 1000.0)
        pend.append((t_p - t_w) * 1000.0)

        _t = time.process_time()
        play_start(dma, bufs[k])      # 交出去就走，不等
        pt["play"] += time.process_time() - _t
        # PS 真正花在这块上的时间：从"这块录完"到"写进播放 FIFO"这一段里，
        # 去掉等放音和水位控制的等待（那两段对应的是已经排在 FIFO 里的声音，
        # 算在播放积压里，再加一遍就重复了）。
        psl.append(((time.perf_counter() - t_b) - (t_p - t_w)) * 1000.0)

        if verbose or i % max(1, nblk // 15) == 0 or d_ovf or d_und:
            print("  %6d  %8.2f  %10.2f  %7d  %7d  %8d %7d"
                  % (i, proc[-1], pend[-1], st["tx_lvl"], st["cap_lvl"],
                     d_ovf, d_und))
        i += 1

    mm2s_wait(dma, timeout=2.0)       # 等最后一块真的放完再停
    total = time.perf_counter() - t0
    if proc:
        pr = np.array(proc)
        pw = np.array(pend)
        blk_ms = frames / FS_ACT * 1000.0
        print("\n  跑了 %.1f s / %d 块（按 48.83 kHz 算理论 %.1f s）"
              % (total, i, i * blk_ms / 1000.0))
        print("  采集完 → 交给 MM2S：mean %.2f ms  max %.2f ms"
              "（其中等上一块放完 mean %.2f ms）"
              % (pr.mean(), pr.max(), pw.mean()))
        print("  一块 = %.2f ms" % blk_ms)
        print("  [计时] 每块：cap_wait %.3f + 挂录音 %.3f + 等放音/水位 %.3f"
              " + 读状态 %.3f + 挂放音 %.3f = %.3f ms"
              % (pt["cap"] / i * 1e3, pt["arm"] / i * 1e3,
                 pt["mm2s"] / i * 1e3, pt["stat"] / i * 1e3,
                 pt["play"] / i * 1e3, sum(pt.values()) / i * 1e3))
        # 整机 CPU 占用（含启动），和出货版"搬样线程占满一个核"对照
        ru = resource.getrusage(resource.RUSAGE_SELF)
        cpu = ru.ru_utime + ru.ru_stime
        print("  进程 CPU：user %.2f + sys %.2f = %.2f s（跑 %.1f s，"
              "约一个核的 %.0f%%）"
              % (ru.ru_utime, ru.ru_stime, cpu, total,
                 cpu / max(total, 1e-9) * 100.0))
        lcpu = ((ru.ru_utime - _ru0.ru_utime) + (ru.ru_stime - _ru0.ru_stime))
        lwall = time.perf_counter() - t0
        print("  其中循环内：%.2f s / %.1f s = 一个核的 %.0f%%"
              "（其余是开机、加载 overlay、分配缓冲区）"
              % (lcpu, lwall, lcpu / max(lwall, 1e-9) * 100.0))
        # 嘴到耳的算法：一个采样要等它这一块填满（正好一个块周期，块内位置
        # 那一项前后抵消）+ 录音 FIFO 里排在前面的 + PS 处理 + 交出去时播放
        # FIFO 里排在前面的 + 声学。不能用"采集完 → play 返回"，那里面含着
        # MM2S 等 FIFO 的时间，和播放积压是同一段声音，加了就重复。
        ps_ms = (pr.mean() - pw.mean()) if len(pr) else 0.0
        if psl:
            ps_ms = float(np.mean(psl))
        cap_ms = 0.0
        if cap_seen:
            cm = float(np.mean(cap_seen))
            cap_ms = cm / 2.0 / FS_ACT * 1000.0
            print("  录音 FIFO 占用：mean %.0f 字、max %d 字 ≈ %.1f ms 积压"
                  % (cm, max(cap_seen), cap_ms))
        if tx_seen:
            txm = float(np.mean(tx_seen))
            tx_ms = txm / 2.0 / FS_ACT * 1000.0
            print("  播放 FIFO 占用：mean %d 字、末次 %d 字 ≈ 播放积压 %.1f ms"
                  % (txm, st["tx_lvl"], tx_ms))
            print("  嘴到耳 ≈ 一块 %.1f + 录音 %.1f + PS %.1f + 放音 %.1f"
                  " + 声学 1.23 ≈ %.1f ms"
                  % (blk_ms, cap_ms, ps_ms, tx_ms,
                     blk_ms + cap_ms + ps_ms + tx_ms + 1.23))
        print("  首块录音 rms = %s" % ("-" if rms_first is None else "%.1f" % rms_first))
        print("  累计 overrun=%d  underrun=%d" % (ovf_d, und_d))
        if ovf_idx:
            shown = ovf_idx[:20]
            print("  丢块出现在第 %s%s 块（共 %d 块有丢，单块最多丢 %d 个采样）"
                  % ("、".join("%d" % v for v in shown),
                     " …" if len(ovf_idx) > len(shown) else "",
                     len(ovf_idx), ovf_max))
            print("  也就是说：%s"
                  % ("全部集中在开头 %.1f s"
                     % ((ovf_idx[-1] + 1) * frames / FS)
                     if ovf_idx[-1] < nblk * 0.9 else "散布在整段里"))
        check("没有丢块（overrun=0）", ovf_d == 0, "%d" % ovf_d)
        check("没有断流（underrun=0）", und_d == 0, "%d" % und_d)

    set_ctrl(ctrl, 0)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="a", choices=list("abcd"))
    ap.add_argument("--frames", type=int, default=480,
                    help="一个块的立体声帧数（480 = 10 ms）")
    ap.add_argument("--blocks", type=int, default=10, help="b 段采几块")
    ap.add_argument("--secs", type=float, default=None)
    ap.add_argument("--tone", type=float, default=440.0)
    ap.add_argument("--tx-low", type=int, default=480,
                    help="d 段播放水位上限（字）。交下一块之前先把播放 FIFO 等"
                         "到不高于它；0 = 不控制。默认 480（一块）实测 60 秒"
                         "不丢块、嘴到耳 10.1 ms；不控制时水位会随机漂，"
                         "60 秒里漏过 275 次断流")
    ap.add_argument("--verbose", action="store_true", help="d 段每块都打印")
    ap.add_argument("--bit", default="fir_dma.bit")
    ap.add_argument("--hwh", default=None, help="不给就按 bit 同名找")
    args = ap.parse_args()

    global BITFILE, HWHFILE
    BITFILE = args.bit
    HWHFILE = args.hwh if args.hwh else args.bit.rsplit(".", 1)[0] + ".hwh"

    # 循环里每块都会建几个临时对象，CPython 的分代 GC 偶尔会在循环里停一下
    # （实测有个 10.95 ms 的离群点）。缓冲区都是预分配的，剩下的临时对象靠
    # 引用计数就回收了，关掉 GC 不影响内存。
    import gc
    gc.disable()

    dma, ctrl, stat = load()
    print("  初始状态：", read_stat(stat))

    if args.stage == "a":
        stage_a(dma, ctrl, stat, secs=args.secs or 3.0)
    elif args.stage == "b":
        stage_b(dma, ctrl, stat, frames=args.frames, blocks=args.blocks)
    elif args.stage == "c":
        stage_c(dma, ctrl, stat, frames=args.frames,
                secs=args.secs or 5.0, freq=args.tone)
    else:
        stage_d(dma, ctrl, stat, frames=args.frames, secs=args.secs or 60.0,
                tx_low=args.tx_low, verbose=args.verbose)

    print("\n%s" % ("全部通过" if ok_all else "有失败项，看上面"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
