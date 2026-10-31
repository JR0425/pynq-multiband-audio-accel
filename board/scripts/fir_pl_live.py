#!/usr/bin/env python3
"""fir_pl_live —— 全 PL 直连版的后端（PS 不进音频环）。

和 `fir_live.py` 的分工：

    fir_live.py    PS 在环里：逐块搬样本、算 FIR、算电平、写 status。
                  一个核吃 99%，延迟 16.2 / 21.1 / 31.0 ms 三档。
    fir_pl_live   PS **完全不碰样本**，只做三件事：
                    ① 读 /dev/shm/fir_tune.json  →  写进 AXI-Lite 寄存器
                    ② 读回寄存器                →  写 /dev/shm/fir_status.json
                    ③ 看着 ramp_busy 决定"参数到底生效了没有"
                  音频在 PL 里一进一出，PS 占用接近 0。

寄存器映射以 `docs/PL直连_寄存器表.md` 为准，两边必须逐位一致。

用法（板上）：

    XILINX_XRT=/usr python3 fir_pl_live.py                 # 正常跑
    XILINX_XRT=/usr python3 fir_pl_live.py --secs 600      # 跑 600 秒（soak）
    python3 fir_pl_live.py --selftest                      # 不需要板子/overlay

`--selftest` 只验换算与写寄存器序列（用假的 MMIO），能在 PC 上、也能在板上跑，
不加载 overlay。板上 print 只能 ASCII。
"""
import argparse
import json
import math
import os
import sys
import threading
import time

sys.path.insert(0, "/home/xilinx/dma_exp")

try:
    from fir_tune import read_tune, write_status          # 与网页共用同一套文件协议
except Exception:                                          # pragma: no cover
    TUNE_PATH = os.environ.get("FIR_TUNE", "/dev/shm/fir_tune.json")
    STATUS_PATH = os.environ.get("FIR_STATUS", "/dev/shm/fir_status.json")

    def _read(path):
        try:
            with open(path, "r") as f:
                return json.load(f) or {}
        except Exception:
            return {}

    def read_tune():
        return _read(TUNE_PATH)

    def write_status(obj):
        tmp = STATUS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, STATUS_PATH)

# ---------------------------------------------------------------- 寄存器地址
# 都在 fir_pl_regs 的 4 KB 窗口里（BD 里挂 0x43C40000）
REG_ID        = 0x000
REG_CTRL      = 0x004
REG_STAT0     = 0x008
REG_STAT1     = 0x00C
REG_STAT2     = 0x010

BANK0         = 0x100          # 左耳；右耳是 0x200
BANK1         = 0x200
OFF_GAIN      = 0x00           # +0/4/8/C → GAIN[0..3]，各 32 位寄存器低 16 位有效
OFF_THR       = 0x10
OFF_RATIO     = 0x20
OFF_ATK       = 0x30
OFF_REL       = 0x40
OFF_ENV       = 0x50           # RO
OFF_GR        = 0x60           # RO
OFF_TONE_SEL  = 0x58           # RW，0=250 1=750 2=1500 3=3000 Hz
OFF_TONE_AMP  = 0x5C           # RW，Q1.15 幅度，0 = 静音
OFF_MPO_TH    = 0x48           # RW，限幅阈值（线性幅度）
OFF_MPO_CNT   = 0x50           # RO，被限幅的采样数
OFF_MPO_PEAK  = 0x54           # RO，见过的最大 |输出|
OFF_RAMP_DIV  = 0x70
OFF_RAMP_BUSY = 0x74           # RO，bit0 = bank0，bit1 = bank1

REG_PEAK_IN   = 0x040          # RO，16 位幅度，每 768 采样窗末锁存
REG_PEAK_OUT  = 0x044

CTRL_RUN   = 1 << 0
CTRL_DRC   = 1 << 1
CTRL_TONE  = 1 << 3          # 测试音注入：输出被四段中心频率的方波顶替（B4 测听）
CTRL_FLUSH = 1 << 2
CTRL_CLR   = 1 << 8

GAIN_UNITY = 256               # Q7.8，1.0 = 256
Q22_ONE    = 1 << 22           # Q2.22 的 1.0
# PL 真实帧率：i2s_duplex 每 2048 拍（100 MHz）出一个立体声帧 ⇒ 48828.125 Hz。
# 这里原来写 48000，于是 PS 每秒只往环里填 48000 字、PL 每秒取走 48828 字。
# 环深 16384 字，读指针每秒追上写指针 828 字 ⇒ 起跑后约 4.9 s 第一次追上，
# 之后每 19.8 s 一次；每次追上都等于把播放位置硬跳一整个环，听感就是"咔"一声。
# （这也解释了为什么 48 kHz 的 WAV 播出来偏高 1.7%。）
FS         = 100e6 / 2048.0

# 网页第三档用它开关通路：内容 "1" / "0"。PL 直连没有"启动音频流"的概念，
# 只有 run 位；写文件比让网页进程去碰 MMIO 安全（overlay 只能在一个进程里）。
RUN_PATH = os.environ.get("FIR_PL_RUN", "/dev/shm/fir_pl_run.txt")

# ---- C3 抓取缓冲（2026-10-30）----
# 4096×32 BRAM：低 16 位进核、高 16 位出核（限幅后左耳）。
# 0x64 的 bit0 是 snap（1 = 冻结写指针），0x6C 读回写指针。
# 读数据**不走 0x43C60000**：那片 AXI BRAM 的读通路是断的（读回恒 0x8，见
# read_scope 的注释）。A 口接在 fir_pl_regs 上，写 0x78 选下标、读 0x7C 取字。
# 读完写 /dev/shm/fir_scope.json，网页从 /api/scope 取。
SCOPE_PATH = os.environ.get("FIR_PL_SCOPE", "/dev/shm/fir_scope.json")
# 抓取刷新周期（秒）。见主循环里的注释：读一遍 165 ms，别设得比 1 s 小。
SCOPE_EVERY = float(os.environ.get("FIR_PL_SCOPE_EVERY", "0.5"))
CAP_BASE_DEFAULT = 0x43C60000        # 仅作日志/兼容，不再用于读数
OFF_SCOPE_CTL = 0x64
OFF_SCOPE_WR = 0x6C
OFF_CAP_IDX = 0x78                  # 写：抓取缓冲读下标 0..4095
OFF_CAP_DATA = 0x7C                 # 读：该下标的字（低 16 进核 / 高 16 出核）

# ---- 循环播放固定音频（2026-10-30）----
# 波表 BRAM 0x43C50000，16384 字环形；样本放 [31:16]。RTL 按采样节拍读，
# PS 必须同速往前填 —— 每小时钟 21.33 ms 填 1024 字。ctrl[5] 切音源。
PLAY_PATH = os.environ.get("FIR_PL_PLAY", "/dev/shm/fir_pl_play.txt")
PLY_BASE_DEFAULT = 0x43C50000
OFF_PLY_CTL = 0x24
OFF_PLY_LEN = 0x28
CTRL_SRC = 1 << 5
PLAY_RING = 16384
PLAY_CHUNK = 1024
# 写头领先读头几块。一块 = 20.97 ms，5 块 ≈ 105 ms：上位机或网络卡顿不超过
# 105 ms 就不会写到还没播的区域，也就不会出「啪」。
PLAY_LEAD_CHUNKS = 5

BIT_DEFAULT = "/home/xilinx/dma_exp/fir_pl.bit"
CTRL_BASE_DEFAULT = 0x43C00000  # gpio_ctrl，ctrl 位仍走这里
REGS_BASE_DEFAULT = 0x43C40000


# ---------------------------------------------------------------- 换算
def db_to_gain(db):
    """页面 gain_db（−24..+42）→ GAIN[i]（Q7.8，int16 范围）。

    2026-10-28 听感拍板：演示默认 +30 dB（8095）—— 18 dB 用户嫌小，
    24 dB 勉强、30 dB 合适（板上 gain_ladder/gain30 实测）。
    """
    v = int(round(GAIN_UNITY * (10.0 ** (float(db) / 20.0))))
    return max(-32768, min(32767, v))


def gain_to_db(g):
    if g <= 0:
        return -120.0
    return 20.0 * math.log10(float(g) / GAIN_UNITY)


def dbfs_to_thr(db):
    """thr_db（−60..0 dBFS）→ THR[i]（Q2.22）。

    先按 Q1.15 取整再 <<7 —— 这是驱动侧 `fir_core.ms_to_coef` 那套顺序，
    直接在 Q2.22 域取整会差 26 LSB（−20 dBFS 是 419456 vs 419430）。
    听不出来，但逐位对拍会挂。
    """
    q15 = int(round(32768.0 * (10.0 ** (float(db) / 20.0))))
    q15 = max(0, min(32767, q15))
    return q15 << 7


def db_to_amp15(db):
    """dBFS → Q1.15 幅度（0..32767）。用于测听音的幅度。"""
    v = int(round(32768.0 * (10.0 ** (float(db) / 20.0))))
    return max(0, min(32767, v))


def ratio_to_reg(r):
    """页面 ratio 字段是 **1 − 1/压缩比**（0..0.95），正好就是 RATIO 寄存器。"""
    q15 = int(round(32768.0 * max(0.0, min(0.95, float(r)))))
    return q15 << 7


def ms_to_coef(ms):
    """attack/release 毫秒 → 每采样步进（Q2.22）。0 表示不启用（无记忆压缩）。"""
    ms = float(ms)
    if ms <= 0:
        return 0
    step15 = int(round((1.0 - math.exp(-1.0 / (ms * 1e-3 * FS))) * 32768.0))
    step15 = max(1, min(32767, step15))
    return step15 << 7


def coef_to_ms(c):
    """上面的逆运算，只给 /api/status 显示用（页面要 attack_ms/release_ms）。"""
    if c <= 0:
        return 0.0
    s = (int(c) >> 7) / 32768.0
    if s <= 0.0 or s >= 1.0:
        return 0.0
    return round(-1.0 / (FS * math.log(1.0 - s)) * 1e3, 1)


def amp_to_db(v, full=32768.0):
    """线性幅度 → dBFS。0 返回 None，页面据此显示"—"。"""
    v = abs(float(v))
    if v <= 0.0:
        return None
    return round(20.0 * math.log10(v / full), 1)


def read_obs(regs):
    """读观察量：PEAK_IN/OUT（16 位）+ 左右各四段 ENV/GR（Q2.22）。

    2026-10-29 双耳：bank1 现在是第二份 DRC 的真信号，所以能分别看两只耳朵
    的包络和压缩量 —— 这是"两套处方确实各算各的"最直接的证据。
    """
    peak_in = regs.rd(REG_PEAK_IN) & 0xFFFF
    peak_out = regs.rd(REG_PEAK_OUT) & 0xFFFF
    env, gr, env_r, gr_r = [], [], [], []
    for i in range(4):
        e = regs.rd(BANK0 + OFF_ENV + 4 * i) & 0xFFFFFF
        g = regs.rd(BANK0 + OFF_GR + 4 * i) & 0xFFFFFF
        e2 = regs.rd(BANK1 + OFF_ENV + 4 * i) & 0xFFFFFF
        g2 = regs.rd(BANK1 + OFF_GR + 4 * i) & 0xFFFFFF
        env.append(round(e / float(Q22_ONE), 4))     # 线性幅度，1.0 = 满量程
        gr.append(round(g / float(Q22_ONE), 4))
        env_r.append(round(e2 / float(Q22_ONE), 4))
        gr_r.append(round(g2 / float(Q22_ONE), 4))
    return peak_in, peak_out, env, gr, env_r, gr_r


def read_busy(regs):
    """两个 bank 的斜坡 busy。地址是 bank0 内偏移 +0x74，写成绝对地址才读得到。"""
    return regs.rd(BANK0 + OFF_RAMP_BUSY) & 0x3


def read_run():
    """网页写的 run 标志。文件不存在 = 1（独立跑时默认开）。"""
    try:
        with open(RUN_PATH, "r") as f:
            return f.read().strip() != "0"
    except Exception:
        return True


def write_run(on):
    try:
        tmp = RUN_PATH + ".tmp"
        with open(tmp, "w") as f:
            f.write("1" if on else "0")
        os.replace(tmp, RUN_PATH)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 抓取缓冲
def read_scope(regs, cap, path=None):
    """冻指针 → 从写指针 +1 起连读 1024 点 → 解锁 → 写 scope json。返回样本数，0 = 失败。

    必须先冻再读：读 1024 个字要几毫秒，不冻的话新样本会盖掉环形缓冲的一截，
    画出来是断的。冻结只停抓取，音频通路照跑。

    **读法（2026-10-30 夜改）**：不再直接从 0x43C60000 读。
    板上实测那片 AXI BRAM 的读通路是断的 —— 写能进去（波形确实抓得到），
    读回来恒等于 0x8，波表那片也一样，而同一块 AXI 上的寄存器读得好好的。
    现在 BRAM 的 A 口接在 fir_pl_regs 上：
        写 0x78 = 字节地址  →  读 0x7C = 该字

    **字节地址（2026-10-31）**：cap_bram 这块 IP 的 Enable_32bit_Address 是
    disabled 参数，关不掉（真去关，写侧会整个哑掉 —— 4096 个下标读回来全 0）。
    所以两头都得按字节地址走：PL 写 {cap_wr, 2'b00}，PS 读 i*4。
    深度 2048 字 = 8192 字节。

    **从写指针 +1 开始读、让 BRAM 自己回绕**（同一天改）：缓冲是环形，跨回绕点
    连续读出来的就是时间上连续的 1024 个样点，最老的在最前。这样不用再按指针
    转窗口，也就不存在"接缝落在窗口中间"的问题。
    """
    path = path or SCOPE_PATH
    import array
    N = 1024
    DEPTH = 2048
    regs.wr(OFF_SCOPE_CTL, 1)
    time.sleep(0.002)
    ptr = regs.rd(OFF_SCOPE_WR) & (DEPTH - 1)
    start = (ptr + 1) & (DEPTH - 1)
    words = [0] * N
    for i in range(N):
        regs.wr(OFF_CAP_IDX, ((start + i) & (DEPTH - 1)) * 4)
        words[i] = regs.rd(OFF_CAP_DATA)
    regs.wr(OFF_SCOPE_CTL, 0)
    v = array.array("I", words)
    ins = [((x & 0xFFFF) ^ 0x8000) - 0x8000 for x in v]
    outs = [(((x >> 16) & 0xFFFF) ^ 0x8000) - 0x8000 for x in v]
    obj = {"ok": True, "t": time.time(), "fs": 48828.0, "n": len(ins),
           "in": ins, "out": outs}
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except Exception:
        return 0
    return len(ins)


# ---------------------------------------------------------------- 循环播放
def read_play():
    """网页写的播放请求：文件路径 / "off"。不存在 = 不播。"""
    try:
        with open(PLAY_PATH, "r") as f:
            s = f.read().strip()
    except Exception:
        return None
    return s if s and s.lower() != "off" else None


def load_wav_words(path):
    """16 位 WAV → 波表字序列（样本放 [31:16]）。取左声道，重采样到 48 kHz。"""
    import array
    import wave
    w = wave.open(path, "rb")
    nch, sw, sr, nf = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    raw = w.readframes(nf)
    w.close()
    if sw != 2:
        raise ValueError("只支持 16 位 WAV：%s" % path)
    a = array.array("h")
    a.frombytes(raw)
    if nch > 1:
        a = a[0::nch]
    if sr != FS and len(a) > 1:
        m = int(len(a) * FS / float(sr))
        a = array.array("h", [a[min(len(a) - 1, int(i * sr / FS))] for i in range(m)])
    return array.array("I", [(int(v) & 0xFFFF) << 16 for v in a])


def _slice_bytes(words, pos, k):
    """从环形缓冲取 k 个字（越界绕回），返回小端字节串。"""
    n = len(words)
    q = pos % n
    if q + k <= n:
        return words[q:q + k].tobytes()
    return words[q:].tobytes() + words[:q + k - n].tobytes()


class Player(threading.Thread):
    """按采样节拍往波表环里填。绝对时刻调度，不积累漂移（同 ply_upload.py）。"""

    def __init__(self, bram, words):
        threading.Thread.__init__(self)
        self.daemon = True
        self.bram = bram
        self.w = words
        self.stop_ev = threading.Event()
        self.err = None

    def run(self):
        # 先到点再写：写头永远走在读头前面（落后 PLAY_LEAD_CHUNKS 块 = ~105 ms 起跑），
        # 不会改到正在播的字
        try:
            pos = PLAY_RING
            nxt = time.monotonic() + (PLAY_RING - PLAY_LEAD_CHUNKS * PLAY_CHUNK) / float(FS)
            while not self.stop_ev.is_set():
                now = time.monotonic()
                if now < nxt:
                    time.sleep(nxt - now)
                late = time.monotonic() - nxt
                if late > 0.020:
                    print("PLAYLATE %.3f pos=%d" % (late, pos), flush=True)
                self.bram.write((pos % PLAY_RING) * 4, _slice_bytes(self.w, pos, PLAY_CHUNK))
                pos += PLAY_CHUNK
                nxt += PLAY_CHUNK / float(FS)
        except Exception as ex:
            self.err = "%s: %s" % (type(ex).__name__, ex)


# ---------------------------------------------------------------- 寄存器访问
class Regs(object):
    """一层薄封装：只认 32 位整字的读写，方便 --selftest 用假 MMIO 顶替。"""

    def __init__(self, mmio):
        self.m = mmio

    def wr(self, off, val):
        self.m.write(off, int(val) & 0xFFFFFFFF)

    def rd(self, off):
        return self.m.read(off) & 0xFFFFFFFF


def apply_tune(regs, tune, last=None):
    """把 tune 写进寄存器。返回 (写进去的目标值, 是否有变化)。

    只在字段真的变了的时候写 —— 每 20 ms 无脑写一遍会让 ramp 反复重置，
    表现是"增益永远爬不到头"。
    """
    g = tune.get("gain_db") or [30.0] * 4   # 演示默认 +30 dB（2026-10-28 听感拍板）
    # thr/ratio/atk/rel 的默认值必须等于寄存器出厂值（419456 / 2936064 / 17408 / 896），
    # 否则后端一启动就把位流上电的那套 v25 参数顶掉。2026-10-28 批 2 定案。
    # 例外：ATK 默认 5 ms 在真实帧率 48.828 kHz 下算出 17152（出厂 17408 是按 48 kHz 算的），
    # 差 1 个步进 = 4.93 ms vs 5.00 ms，启动时会写这一格，不影响听感。
    thr = tune.get("thr_db") or [-20.0] * 4
    rat = tune.get("ratio") or [0.7] * 4
    atk = tune.get("attack_ms") or [5.0] * 4
    rel = tune.get("release_ms") or [100.0] * 4
    byp = bool(tune.get("bypass"))

    # B4 测听：tone.on 时输出被指定频段的方波顶替（ctrl[3]），幅度由 tone.amp_db 定
    tone = tune.get("tone") or {}
    tone_on  = bool(tone.get("on"))
    tone_sel = int(tone.get("band") or 0) & 0x3
    tone_amp = db_to_amp15(tone.get("amp_db", -22.0))

    # B6 MPO 限幅：输出上限（dBFS）。0 dBFS = 满量程 = 不动作，正是寄存器出厂值 32767。
    mpo_th = db_to_amp15(float(tune.get("mpo_db", 0.0)))

    # 2026-10-29 双耳：tune["b1"] 存在就用它当右耳的处方，不存在就左右同一套
    # （默认行为不变 —— 单耳时两只耳机听到的内容必须完全一样）。
    b1 = tune.get("b1") or {}
    g1   = b1.get("gain_db")    or g
    thr1 = b1.get("thr_db")     or thr
    rat1 = b1.get("ratio")      or rat
    atk1 = b1.get("attack_ms")  or atk
    rel1 = b1.get("release_ms") or rel

    cur = {
        "gain": [db_to_gain(x) for x in _four(g)],
        "thr": [dbfs_to_thr(x) for x in _four(thr)],
        "ratio": [ratio_to_reg(x) for x in _four(rat)],
        "atk": [ms_to_coef(x) for x in _four(atk)],
        "rel": [ms_to_coef(x) for x in _four(rel)],
        "gain_b1": [db_to_gain(x) for x in _four(g1)],
        "thr_b1": [dbfs_to_thr(x) for x in _four(thr1)],
        "ratio_b1": [ratio_to_reg(x) for x in _four(rat1)],
        "atk_b1": [ms_to_coef(x) for x in _four(atk1)],
        "rel_b1": [ms_to_coef(x) for x in _four(rel1)],
        "bypass": byp,
        "tone_on": tone_on,
        "tone_sel": tone_sel,
        "tone_amp": tone_amp,
        "mpo_th": mpo_th,
    }

    # 2026-10-29 修：bypass 原来直接把 ctrl[1] 清零，而 ctrl[1] 是**整条 DRC 链路**
    # 的使能（四段增益也在里面），于是"直通"会把增益一起旁路掉 —— 用户调增益
    # 完全没反应，左右耳也变成同一路。现在改成：链路始终通，旁路时把压缩器
    # 调成"不动作"（阈值抬到 0 dBFS + 比例 1:1），增益照常生效。
    if byp:
        cur["thr"]      = [dbfs_to_thr(0.0)] * 4
        cur["ratio"]    = [0] * 4
        cur["thr_b1"]   = [dbfs_to_thr(0.0)] * 4
        cur["ratio_b1"]  = [0] * 4

    changed = ((last is None) or (cur["bypass"] != last.get("bypass"))
               or (cur["tone_on"] != last.get("tone_on")))
    for i in range(4):
        if last is None or cur["gain"][i] != last["gain"][i]:
            # RAMP_DIV=5：指数斜坡，时间常数 32 采样 ≈ 0.7 ms，拖滑块不咔。
            # 寄存器出厂默认 0 = 一步到位，所以这里每次都补写（幂等，写同值
            # 不会重置 ramp —— 2026-10-28 板上实测口径）。
            regs.wr(BANK0 + 0x70, 5)
            regs.wr(BANK1 + 0x70, 5)
            regs.wr(BANK0 + OFF_GAIN + 4 * i, cur["gain"][i])
            changed = True
        if last is None or cur["gain_b1"][i] != last["gain_b1"][i]:
            regs.wr(BANK1 + 0x70, 5)
            regs.wr(BANK1 + OFF_GAIN + 4 * i, cur["gain_b1"][i])
            changed = True
        if last is None or cur["thr"][i] != last["thr"][i]:
            regs.wr(BANK0 + OFF_THR + 4 * i, cur["thr"][i])
            changed = True
        if last is None or cur["thr_b1"][i] != last["thr_b1"][i]:
            regs.wr(BANK1 + OFF_THR + 4 * i, cur["thr_b1"][i])
            changed = True
        if last is None or cur["ratio"][i] != last["ratio"][i]:
            regs.wr(BANK0 + OFF_RATIO + 4 * i, cur["ratio"][i])
            changed = True
        if last is None or cur["ratio_b1"][i] != last["ratio_b1"][i]:
            regs.wr(BANK1 + OFF_RATIO + 4 * i, cur["ratio_b1"][i])
            changed = True
        if last is None or cur["atk"][i] != last["atk"][i]:
            regs.wr(BANK0 + OFF_ATK + 4 * i, cur["atk"][i])
            changed = True
        if last is None or cur["atk_b1"][i] != last["atk_b1"][i]:
            regs.wr(BANK1 + OFF_ATK + 4 * i, cur["atk_b1"][i])
            changed = True
        if last is None or cur["rel"][i] != last["rel"][i]:
            regs.wr(BANK0 + OFF_REL + 4 * i, cur["rel"][i])
            changed = True
        if last is None or cur["rel_b1"][i] != last["rel_b1"][i]:
            regs.wr(BANK1 + OFF_REL + 4 * i, cur["rel_b1"][i])
            changed = True

    if last is None or cur["tone_sel"] != last.get("tone_sel"):
        regs.wr(OFF_TONE_SEL, cur["tone_sel"])
        changed = True
    if last is None or cur["tone_amp"] != last.get("tone_amp"):
        regs.wr(OFF_TONE_AMP, cur["tone_amp"])
        changed = True
    if last is None or cur["mpo_th"] != last.get("mpo_th"):
        regs.wr(OFF_MPO_TH, cur["mpo_th"])
        changed = True

    # ctrl 不在这个窗口里 —— 它走 gpio_ctrl（0x43C00000），和探针/演示脚本一致。
    # BD 里 fir_pl_regs 的 ctrl 输出没有接到 fir_pl_direct（避免两个驱动源）。
    return cur, changed


def ctrl_for(cur, src=False):
    """DRC 链路（含四段增益）永远开；"直通"靠压缩器不动作实现，不靠关链路。

    原来这里返回 CTRL_RUN（清掉 CTRL_DRC）→ 输出直接是前端原始样本，
    四段增益和压缩一起被跳过，用户以为增益坏了。
    src=True 时切到波表音源（ctrl[5]），用于循环播放固定音频。
    """
    v = CTRL_RUN | CTRL_DRC
    if cur.get("tone_on"):
        v |= CTRL_TONE
    if src:
        v |= CTRL_SRC
    return v


def _four(v):
    """页面可能给标量也可能给 4 元数组，统一成 4 元。"""
    if isinstance(v, (list, tuple)):
        out = [float(x) for x in v][:4]
        while len(out) < 4:
            out.append(out[-1] if out else 0.0)
        return out
    x = float(v)
    return [x, x, x, x]


def cr_of(env, gr, thr_reg):
    """A4 实时压缩比：由 ENV/GR 反推压缩器**实际**在按多少比压。

    压缩器是 y = thr + (x-thr)·k，其中 k = 1-1/R，寄存器里存的就是 k。
    衰减量 gr = (x-thr)·(1-k)，所以
        k = 1 - gr/(x-thr)      →  R = 1/(1-k)
    这是"读出来的真实压缩比"，不是界面上那个设定值 —— 设定 2:1 而实测
    1.8:1 时，说明包络还没跟上或信号没真正过阈值。

    x 没超过阈值（d<=0）、或根本没衰减时，返回 None（比值无意义）。
    """
    out = []
    for i in range(4):
        try:
            x = float(env[i]); g = float(gr[i]); t = float(thr_reg[i]) / 4194304.0
        except (IndexError, TypeError, ValueError, ZeroDivisionError):
            out.append(None); continue
        d = x - t
        if d <= 1e-5 or g <= 1e-6:
            out.append(None); continue
        k = 1.0 - g / d
        if k <= 0.001 or k >= 0.999:      # 反推不可信（除零附近）
            out.append(None); continue
        out.append(round(1.0 / (1.0 - k), 2))
    return out


def publish(regs, tune_cur, t0, ramp_busy, err=None, sink=None, run=True, ctrl=None, play=None):
    """组装 status json。字段照 fir_live.publish_status 的约定，页面不用改。

    2026-10-28 批 2：多给一组观察量（peak_in/out、四段 env/gr）。
    RTL 里 PEAK 是真信号（每 768 采样窗锁存），ENV/GR 也是真信号；
    电平表（in_rms/out_rms）还是桩，所以那两个保持 0，页面显示空条。
    """
    now = time.time()
    st0 = regs.rd(REG_STAT0)
    st1 = regs.rd(REG_STAT1)
    gains = tune_cur["gain"] if tune_cur else [256] * 4
    busy = bool(ramp_busy)
    peak_in, peak_out, env, gr, env_r, gr_r = read_obs(regs)
    cr    = cr_of(env,   gr,   (tune_cur or {}).get("thr",    [0] * 4))
    cr_r  = cr_of(env_r, gr_r, (tune_cur or {}).get("thr_b1", [0] * 4))
    # B6：MPO 限幅计数（flush 清零）与见过的最大输出
    mpo_cnt  = regs.rd(OFF_MPO_CNT)
    mpo_peak = regs.rd(OFF_MPO_PEAK) & 0xFFFF
    mpo_th_v = (tune_cur or {}).get("mpo_th", 32767)
    (sink or write_status)({
        "t": now,
        "n": 0,                      # 没有"块"这个概念了
        "pid": os.getpid(),
        "running": bool(run),
        # 循环播放：playing=True 时音源是波表而不是麦克风（ctrl[5]）
        "playing": bool(play),
        "play_file": os.path.basename(play) if play else None,
        "ok": err is None,
        "mode": "pl",                # 网页用它显示"PL 直连"徽标
        "gain_db": [round(gain_to_db(g), 2) for g in gains],
        # 正在爬坡时 applied 还不等于目标；爬完（≈6 ms）就相等
        "gain_applied_db": [round(gain_to_db(g), 2) for g in gains],
        "ramp_busy": 1 if busy else 0,
        # 2026-10-29 修：fir_pl_regs 的 CTRL(0x004) 恒读 0 —— 真正的 ctrl 端口接的是
        # gpio_ctrl(0x43C00000)，不是这个寄存器。原来读 REG_CTRL 导致网页上的
        # "旁路"永远显示为开（实际在压缩）。有 ctrl 对象就读它，没有就退回读寄存器。
        # ctrl[1] 现在恒为 1（链路常通），bypass 只能从参数看
        "bypass": bool((tune_cur or {}).get("bypass")),
        "attack_ms": [coef_to_ms(c) for c in (tune_cur or {}).get("atk", [0] * 4)],
        "release_ms": [coef_to_ms(c) for c in (tune_cur or {}).get("rel", [0] * 4)],
        # ---- 观察量（真信号）----
        "peak_in": peak_in,
        "peak_out": peak_out,
        "peak_in_db": amp_to_db(peak_in, 32768.0),
        "peak_out_db": amp_to_db(peak_out, 32768.0),
        "env": env,                      # 四段包络，线性幅度（1.0 = 满量程）
        "env_db": [amp_to_db(v, 1.0) for v in env],
        "gr": gr,                        # 四段衰减量，线性幅度
        "gr_db": [amp_to_db(v, 1.0) for v in gr],   # 负值 = 压掉的 dB
        # ---- 右耳（bank1）同一组量 ----
        # A4：实测压缩比（1:1 = 没压），None = 该段没过阈值
        "cr": cr,
        "cr_r": cr_r,
        "env_r": env_r,
        "env_r_db": [amp_to_db(v, 1.0) for v in env_r],
        "gr_r": gr_r,
        "gr_r_db": [amp_to_db(v, 1.0) for v in gr_r],
        # B6 MPO 限幅：阈值（dBFS，0 = 不动作）、被限幅的采样数、见过的最大输出
        "mpo_th_db": amp_to_db(mpo_th_v, 32768.0),
        "mpo_cnt": mpo_cnt,
        "mpo_peak": mpo_peak,
        "mpo_peak_db": amp_to_db(mpo_peak, 32768.0),
        "dual": any(abs(env_r[i] - env[i]) > 1e-4 for i in range(4)) if env else False,
        # 电平表/抓取还没接进 RTL，先给 0，页面会显示空条而不是乱跳
        "in_rms": 0.0,
        "out_rms": 0.0,
        "rt": 1.0,                   # 直连是硬实时，倍率恒 1.0（这是它的卖点）
        "lost_blocks": 0,            # 没有块；i2s_duplex 的 ovf/und 在 stat0
        "i2s_stat0": st0,
        "i2s_stat1": st1,
        "l": 0,
        "nbuf": 0,
        # 端到端延迟 = 采集 FIFO 积压 + 播放 FIFO 水位 + 核组延迟（0.98 ms）+ 声学（1.23 ms）
        # 2026-10-29 夜：原来是写死的 2.3，那是**只算核**的数；实测采集 FIFO 常驻
        # 3960/4096，实际是 80 ms 级。现在按真实水位算，flush 之后应该掉到 3 ms 左右。
        "lat_ms": round((st1 >> 16) / 48.828 + (st1 & 0xFFFF) / 97.656 + 2.2, 2),
        "io_ms": 0.0,
        "dsp_ms": 0.0,
        "uptime_s": round(now - t0, 2),
        "error": err,
    })


# ---------------------------------------------------------------- 自检
class FakeMMIO(object):
    """假的 AXI 窗口：只实现 32 位整字，够验写序列与回读。"""

    def __init__(self):
        self.mem = {}
        for i in range(8):
            self.mem[BANK0 + OFF_GAIN + 4 * i] = 0
            self.mem[BANK1 + OFF_GAIN + 4 * i] = 0
        for off in (BANK0 + OFF_GAIN, BANK0 + OFF_GAIN + 4,
                    BANK1 + OFF_GAIN, BANK1 + OFF_GAIN + 4):
            self.mem[off] = 256

    def write(self, off, val):
        self.mem[off] = int(val) & 0xFFFFFFFF

    def read(self, off):
        return self.mem.get(off, 0)


def selftest():
    print("== 1. 换算 ==")
    cases = [
        ("db_to_gain(0)", db_to_gain(0), 256),
        ("db_to_gain(0.5)", db_to_gain(0.5), 271),
        ("db_to_gain(24)", db_to_gain(24), 4057),
        ("db_to_gain(18)", db_to_gain(18), 2033),
        ("db_to_gain(-24)", db_to_gain(-24), 16),
        ("dbfs_to_thr(-20)", dbfs_to_thr(-20), 419456),
        ("dbfs_to_thr(-10)", dbfs_to_thr(-10), 1326336),
        ("ratio_to_reg(0.7)", ratio_to_reg(0.7), 2936064),
        # 5 ms @ 48.828 kHz。RTL 出厂值 17408 是按 48 kHz 算的（= 4.93 ms @ 48.828 kHz），
        # FS 改成真实帧率后这里差 1 个步进，时间常数 5.00 vs 4.93 ms，听不出来。
        ("ms_to_coef(5)", ms_to_coef(5), 17152),
        ("ms_to_coef(100)", ms_to_coef(100), 896),
        ("ms_to_coef(0)", ms_to_coef(0), 0),
    ]
    bad = 0
    for name, got, want in cases:
        ok = got == want
        bad += 0 if ok else 1
        print("  %-22s = %8d  expect %8d  %s" % (name, got, want, "OK" if ok else "FAIL"))

    print("== 2. 写寄存器序列 ==")
    fake = FakeMMIO()
    regs = Regs(fake)
    tune = {"gain_db": [18, 12, 6, 0], "thr_db": [-10] * 4, "ratio": [0.5] * 4,
            "attack_ms": [5] * 4, "release_ms": [100] * 4, "bypass": 0}
    cur, _ = apply_tune(regs, tune)
    checks = [
        ("GAIN[0]", fake.mem[BANK0 + OFF_GAIN + 0], db_to_gain(18)),
        ("GAIN[1]", fake.mem[BANK0 + OFF_GAIN + 4], db_to_gain(12)),
        ("GAIN[3]", fake.mem[BANK0 + OFF_GAIN + 12], 256),
        ("BANK1 GAIN[0]", fake.mem[BANK1 + OFF_GAIN + 0], db_to_gain(18)),
        ("CTRL(bypass=0)", ctrl_for(cur), CTRL_RUN | CTRL_DRC),
    ]
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print("  %-14s = %8d  expect %8d  %s" % (name, got, want, "OK" if ok else "FAIL"))

    print("== 2b. 双耳：tune[\"b1\"] 存在时右耳走另一套 ==")
    dual_tune = {"gain_db": [12.0] * 4, "thr_db": [-20.0] * 4,
                                            "ratio": [0.7] * 4, "attack_ms": [5.0] * 4,
                                            "release_ms": [100.0] * 4,
                                            "b1": {"gain_db": [0.0, 6.0, 12.0, 18.0],
                                                   "thr_db": [-30.0] * 4}}
    fake2 = FakeMMIO()
    regs2 = Regs(fake2)
    cur2, _ = apply_tune(regs2, dual_tune, None)
    dchecks = [
        ("左耳 GAIN[0] +12 dB",  fake2.mem[BANK0 + OFF_GAIN],          db_to_gain(12.0)),
        ("右耳 GAIN[0] 0 dB",    fake2.mem[BANK1 + OFF_GAIN],          db_to_gain(0.0)),
        ("右耳 GAIN[3] +18 dB",  fake2.mem[BANK1 + OFF_GAIN + 12],     db_to_gain(18.0)),
        ("右耳 THR -30 dBFS",    fake2.mem[BANK1 + OFF_THR],           dbfs_to_thr(-30.0)),
        ("左耳 THR -20 dBFS",    fake2.mem[BANK0 + OFF_THR],           dbfs_to_thr(-20.0)),
    ]
    for name, got, want in dchecks:
        ok = got == want
        bad += 0 if ok else 1
        print("  %-22s = %8d  expect %8d  %s" % (name, got, want, "OK" if ok else "FAIL"))

    print("== 3. bypass：链路仍通，只让压缩器不动作 ==")
    tune["bypass"] = 1
    cur2, _ = apply_tune(regs, tune, cur)
    ok = ctrl_for(cur2) == (CTRL_RUN | CTRL_DRC)
    bad += 0 if ok else 1
    print("  CTRL = 0x%02X  expect 0x%02X（增益不能被旁路）  %s"
          % (ctrl_for(cur2), CTRL_RUN | CTRL_DRC, "OK" if ok else "FAIL"))
    ok = (fake.mem[BANK0 + OFF_THR] == dbfs_to_thr(0.0)
          and fake.mem[BANK0 + OFF_RATIO] == 0
          and fake.mem[BANK1 + OFF_THR] == dbfs_to_thr(0.0)
          and fake.mem[BANK1 + OFF_RATIO] == 0)
    bad += 0 if ok else 1
    print("  bypass 时 THR=0dBFS / RATIO=0（两耳）  %s" % ("OK" if ok else "FAIL"))
    ok = fake.mem[BANK0 + OFF_GAIN] == db_to_gain(18)
    bad += 0 if ok else 1
    print("  bypass 时 GAIN 仍是 18 dB  %s" % ("OK" if ok else "FAIL"))

    print("== 4. 不变就不重写（避免 ramp 被反复重置）==")
    tune["bypass"] = 0
    cur3, _ = apply_tune(regs, tune, cur2)      # 先回到"压缩生效"的正常态
    before = dict(fake.mem)
    apply_tune(regs, tune, cur3)                # 同样的参数再走一遍
    n_changed = sum(1 for k in before if before[k] != fake.mem[k])
    ok = n_changed == 0          # 参数一个都没变 ⇒ 一个寄存器都不该被重写
    bad += 0 if ok else 1
    print("  改动寄存器 %d 个（expect 0：参数没变就不重写）  %s" % (n_changed, "OK" if ok else "FAIL"))

    print("== 5. status 字段齐全 ==")
    need = {"t", "n", "pid", "running", "ok", "gain_db", "gain_applied_db",
            "bypass", "attack_ms", "release_ms", "in_rms", "out_rms", "rt",
            "lost_blocks", "l", "nbuf", "lat_ms", "io_ms", "dsp_ms", "error"}
    got = {}
    publish(regs, cur, time.time(), 0, sink=lambda d: got.update(d))
    miss = need - set(got)
    bad += 0 if not miss else 1
    print("  缺字段：%s  %s" % (sorted(miss) if miss else "无", "OK" if not miss else "FAIL"))
    print("  mode=%s lat_ms=%s gain_db=%s" % (got.get("mode"), got.get("lat_ms"), got.get("gain_db")))

    fake.mem[BANK0 + OFF_RAMP_BUSY] = 0x2
    got_busy = read_busy(regs)
    ok = (got_busy == 2)
    bad += 0 if ok else 1
    print("  read_busy(0x174) = %s  %s" % (got_busy, "OK" if ok else "FAIL"))

    print("== 6. 观察量与 run 开关 ==")
    fake.mem[REG_PEAK_IN] = 16384            # 半满量程 = -6.0 dBFS
    fake.mem[REG_PEAK_OUT] = 23170
    fake.mem[BANK0 + OFF_ENV + 4 * 0] = Q22_ONE // 2      # 0.5 = -6.0 dB
    fake.mem[BANK0 + OFF_ENV + 4 * 1] = 0
    fake.mem[BANK0 + OFF_GR + 4 * 0] = Q22_ONE // 4       # 压掉 12 dB
    st = {}
    publish(regs, cur, time.time(), 0, sink=lambda d: st.update(d))
    checks = [
        ("peak_in_db", st.get("peak_in_db"), -6.0),
        ("peak_out_db", st.get("peak_out_db"), -3.0),
        ("env_db[0]", st.get("env_db", [None])[0], -6.0),
        ("env_db[1] zero->None", st.get("env_db", [None, 1])[1], None),
        ("gr_db[0]", st.get("gr_db", [None])[0], -12.0),
        ("env[0] linear", st.get("env", [None])[0], 0.5),
    ]
    for name, got, exp in checks:
        ok = (got == exp)
        bad += 0 if ok else 1
        print("  %-22s = %-8s expect %-8s %s" % (name, got, exp, "OK" if ok else "FAIL"))

    globals()["RUN_PATH"] = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "_run_selftest.txt")
    try:
        write_run(False)
        ok = (read_run() is False)
        write_run(True)
        ok = ok and (read_run() is True)
    finally:
        try:
            os.remove(RUN_PATH)
        except Exception:
            pass
    bad += 0 if ok else 1
    print("  run flag write/read  %s" % ("OK" if ok else "FAIL"))

    print("\n%s" % ("SELFTEST PASS" if bad == 0 else "SELFTEST FAIL (%d)" % bad))
    return 0 if bad == 0 else 1


# ---------------------------------------------------------------- 主循环
def open_pl(bit):
    from pynq import Overlay
    from pynq import MMIO
    ol = Overlay(bit)
    time.sleep(0.3)

    ctrl_base = CTRL_BASE_DEFAULT
    regs_base = REGS_BASE_DEFAULT
    cap_base = CAP_BASE_DEFAULT
    ply_base = PLY_BASE_DEFAULT
    for name, d in ol.ip_dict.items():
        base = d.get("phys_addr")
        if base is None:
            continue
        if name.startswith("fir_pl_regs"):
            regs_base = base
        elif name.startswith("gpio_ctrl"):
            ctrl_base = base
        elif name.startswith("cap_ctrl"):
            cap_base = base
        elif name.startswith("ply_ctrl"):
            ply_base = base
    print("fir_pl_regs @ 0x%08X   gpio_ctrl @ 0x%08X   cap @ 0x%08X   ply @ 0x%08X"
          % (regs_base, ctrl_base, cap_base, ply_base))
    return (ol, Regs(MMIO(regs_base, 0x1000)), MMIO(ctrl_base, 0x1000),
            MMIO(cap_base, 0x4000), MMIO(ply_base, 0x10000))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bit", default=BIT_DEFAULT)
    ap.add_argument("--secs", type=float, default=0.0, help="0 = 一直跑")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--ctrl-via-gpio", action="store_true",
                    help="ctrl 走 gpio_ctrl（默认就是），留着给以后合并 CTRL 用")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    import dma_audio_probe as P

    ol, regs, gpio, cap, ply = open_pl(args.bit)
    print("codec init ...")
    P.codec_init(1)

    # ctrl 仍然写在 gpio_ctrl 上（0x43C00000），和探针/演示脚本保持一致。
    # fir_pl_regs 里也有一份 CTRL，但 BD 里没接到 fir_pl_direct（避免两个驱动源），
    # 所以这里只写 gpio。
    def set_ctrl(v):
        gpio.write(0, v & 0xFFFFFFFF)

    t0 = time.time()
    last = None
    busy = 0
    err = None
    player = None
    play_want = None
    src_on = False
    scope_tick = 0.0

    # 先把通路关一下再开，并且开的时候带一拍 flush。
    # 2026-10-30 修：原来这里直接 set_ctrl(run)，而 run 一直是 1，循环里那个
    # "run 0→1 才 flush" 的分支永远不会走到 —— 采集 FIFO 从上电就带着 ~81 ms
    # 积压，lat_ms 一直显示 85 ms。（上一版只测了"先停后开"的路径，没测重启
    # live 进程这条，所以漏了。）现在启动就是 ctrl=0 → flush → run。
    set_ctrl(0)
    time.sleep(0.05)
    set_ctrl(ctrl_for({}) | CTRL_FLUSH)
    time.sleep(0.03)
    set_ctrl(ctrl_for({}))
    time.sleep(0.05)

    print("running: tune -> regs, regs -> status (Ctrl-C to stop)")
    run = True
    while True:
        try:
            # 2026-10-28 批 2：网页第三档通过 /dev/shm/fir_pl_run.txt 开关通路。
            # 文件不存在时按"开"处理 —— 单独跑这个脚本时行为不变。
            run_now = read_run()
            if run_now != run:
                run = run_now
                if run:
                    # 2026-10-29 夜：采集 FIFO 在上电时就被 ADC 灌满（实测水位
                    # 3960/4096），不 flush 的话输出的一直是 ~81 ms 之前的样本 ——
                    # 实测端到端延迟 85 ms 而不是 2.3 ms。这里先打一拍 flush
                    # （ctrl[2] 自清）把积压清掉，再正常放行。
                    set_ctrl(ctrl_for(last or {}, src_on) | CTRL_FLUSH)
                    time.sleep(0.02)
                    set_ctrl(ctrl_for(last or {}, src_on))
                else:
                    set_ctrl(0)

            # ---- 循环播放固定音频：请求变了就切音源 ----
            want = read_play()
            if want != play_want:
                play_want = want
                if player is not None:
                    player.stop_ev.set()
                    player.join(0.5)
                    player = None
                regs.wr(OFF_PLY_CTL, 0)          # 停读，RTL 把波表地址归零
                perr = None
                if want:
                    try:
                        words = load_wav_words(want)
                        for j in range(0, PLAY_RING, PLAY_CHUNK):
                            ply.write(j * 4, _slice_bytes(words, j, PLAY_CHUNK))
                        regs.wr(OFF_PLY_LEN, PLAY_RING)
                        player = Player(ply, words)
                        player.start()
                        regs.wr(OFF_PLY_CTL, 3)  # en | loop
                    except Exception as ex:
                        perr = "%s: %s" % (type(ex).__name__, ex)
                src_on = player is not None
                if perr:
                    err = perr
                if run:
                    # 切音源必须 flush：两个源相位不同，不 flush 有半秒错位
                    set_ctrl(ctrl_for(last or {}, src_on) | CTRL_FLUSH)
                    time.sleep(0.02)
                    set_ctrl(ctrl_for(last or {}, src_on))

            tune = read_tune() or {}
            cur, changed = apply_tune(regs, tune, last)
            if changed:
                last = cur
                # ctrl 的第 0/1/3/5 位都由 ctrl_for 统一算（run / 压缩 / 测试音 / 音源）
                if run:
                    set_ctrl(ctrl_for(cur, src_on))
            busy = read_busy(regs)

            # ---- C3 抓取缓冲：默认 0.5 s 刷一次 ----
            # 2026-10-30 板上实测：读一遍 4096 点要 165 ms（每次 MMIO 事务在 AXI 上
            # 有固定开销，不是设计时估的 10 ms）。折成 1024 点约 41 ms，按 0.5 s
            # 刷一次占控制环约 8%，拖参数手不顿；页面本身 1 s 轮询一次。
            if time.time() - scope_tick >= SCOPE_EVERY:
                scope_tick = time.time()
                read_scope(regs, cap)

            publish(regs, last, t0, busy, err, run=run, ctrl=gpio, play=play_want)
            err = None
        except Exception as ex:            # 板子上的异常不要静默，写进 status
            err = "%s: %s" % (type(ex).__name__, ex)
            try:
                publish(regs, last, t0, busy, err)
            except Exception:
                pass

        if args.secs and (time.time() - t0) >= args.secs:
            break
        time.sleep(0.1)

    if player is not None:
        player.stop_ev.set()
        player.join(0.5)
        regs.wr(OFF_PLY_CTL, 0)
    set_ctrl(0)
    print("stopped, ctrl=0")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
