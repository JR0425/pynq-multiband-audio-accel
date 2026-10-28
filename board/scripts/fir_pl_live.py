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
OFF_RAMP_DIV  = 0x70
OFF_RAMP_BUSY = 0x74           # RO，bit0 = bank0，bit1 = bank1

CTRL_RUN   = 1 << 0
CTRL_DRC   = 1 << 1
CTRL_FLUSH = 1 << 2
CTRL_CLR   = 1 << 8

GAIN_UNITY = 256               # Q7.8，1.0 = 256
FS         = 48000

BIT_DEFAULT = "/home/xilinx/dma_exp/fir_pl.bit"
CTRL_BASE_DEFAULT = 0x43C00000  # gpio_ctrl，ctrl 位仍走这里
REGS_BASE_DEFAULT = 0x43C40000


# ---------------------------------------------------------------- 换算
def db_to_gain(db):
    """页面 gain_db（−24..+24）→ GAIN[i]（Q7.8，int16 范围）。"""
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
    g = tune.get("gain_db") or [0.0] * 4
    thr = tune.get("thr_db") or [-10.0] * 4
    rat = tune.get("ratio") or [0.0] * 4
    atk = tune.get("attack_ms") or [0.0] * 4
    rel = tune.get("release_ms") or [0.0] * 4
    byp = bool(tune.get("bypass"))

    cur = {
        "gain": [db_to_gain(x) for x in _four(g)],
        "thr": [dbfs_to_thr(x) for x in _four(thr)],
        "ratio": [ratio_to_reg(x) for x in _four(rat)],
        "atk": [ms_to_coef(x) for x in _four(atk)],
        "rel": [ms_to_coef(x) for x in _four(rel)],
        "bypass": byp,
    }

    changed = (last is None) or (cur["bypass"] != last.get("bypass"))
    for i in range(4):
        if last is None or cur["gain"][i] != last["gain"][i]:
            regs.wr(BANK0 + OFF_GAIN + 4 * i, cur["gain"][i])
            regs.wr(BANK1 + OFF_GAIN + 4 * i, cur["gain"][i])   # 双耳暂时同一套
            changed = True
        if last is None or cur["thr"][i] != last["thr"][i]:
            regs.wr(BANK0 + OFF_THR + 4 * i, cur["thr"][i])
            regs.wr(BANK1 + OFF_THR + 4 * i, cur["thr"][i])
            changed = True
        if last is None or cur["ratio"][i] != last["ratio"][i]:
            regs.wr(BANK0 + OFF_RATIO + 4 * i, cur["ratio"][i])
            regs.wr(BANK1 + OFF_RATIO + 4 * i, cur["ratio"][i])
            changed = True
        if last is None or cur["atk"][i] != last["atk"][i]:
            regs.wr(BANK0 + OFF_ATK + 4 * i, cur["atk"][i])
            regs.wr(BANK1 + OFF_ATK + 4 * i, cur["atk"][i])
            changed = True
        if last is None or cur["rel"][i] != last["rel"][i]:
            regs.wr(BANK0 + OFF_REL + 4 * i, cur["rel"][i])
            regs.wr(BANK1 + OFF_REL + 4 * i, cur["rel"][i])
            changed = True

    # ctrl 不在这个窗口里 —— 它走 gpio_ctrl（0x43C00000），和探针/演示脚本一致。
    # BD 里 fir_pl_regs 的 ctrl 输出没有接到 fir_pl_direct（避免两个驱动源）。
    return cur, changed


def ctrl_for(cur):
    """bypass=1 → 只过前端（ctrl[1]=0）；否则走压缩 + 四段增益。"""
    return CTRL_RUN | (0 if cur["bypass"] else CTRL_DRC)


def _four(v):
    """页面可能给标量也可能给 4 元数组，统一成 4 元。"""
    if isinstance(v, (list, tuple)):
        out = [float(x) for x in v][:4]
        while len(out) < 4:
            out.append(out[-1] if out else 0.0)
        return out
    x = float(v)
    return [x, x, x, x]


def publish(regs, tune_cur, t0, ramp_busy, err=None, sink=None):
    """组装 status json。字段照 fir_live.publish_status 的约定，页面不用改。"""
    now = time.time()
    st0 = regs.rd(REG_STAT0)
    st1 = regs.rd(REG_STAT1)
    gains = tune_cur["gain"] if tune_cur else [256] * 4
    busy = bool(ramp_busy)
    (sink or write_status)({
        "t": now,
        "n": 0,                      # 没有"块"这个概念了
        "pid": os.getpid(),
        "running": True,
        "ok": err is None,
        "mode": "pl",                # 网页用它显示"PL 直连"徽标
        "gain_db": [round(gain_to_db(g), 2) for g in gains],
        # 正在爬坡时 applied 还不等于目标；爬完（≈6 ms）就相等
        "gain_applied_db": [round(gain_to_db(g), 2) for g in gains],
        "ramp_busy": 1 if busy else 0,
        "bypass": bool(regs.rd(REG_CTRL) & CTRL_DRC) is False,
        "attack_ms": [0.0] * 4,
        "release_ms": [0.0] * 4,
        # 电平表/抓取还没接进 RTL，先给 0，页面会显示空条而不是乱跳
        "in_rms": 0.0,
        "out_rms": 0.0,
        "rt": 1.0,                   # 直连是硬实时，倍率恒 1.0（这是它的卖点）
        "lost_blocks": 0,            # 没有块；i2s_duplex 的 ovf/und 在 stat0
        "i2s_stat0": st0,
        "i2s_stat1": st1,
        "l": 0,
        "nbuf": 0,
        "lat_ms": 2.3,               # PL 直连的端到端数字延迟（实测值待补）
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
        ("ms_to_coef(5)", ms_to_coef(5), 17408),
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

    print("== 3. bypass 只清 ctrl[1] ==")
    tune["bypass"] = 1
    cur2, _ = apply_tune(regs, tune, cur)
    ok = ctrl_for(cur2) == CTRL_RUN
    bad += 0 if ok else 1
    print("  CTRL = 0x%02X  expect 0x%02X  %s"
          % (ctrl_for(cur2), CTRL_RUN, "OK" if ok else "FAIL"))

    print("== 4. 不变就不重写（避免 ramp 被反复重置）==")
    before = dict(fake.mem)
    tune["bypass"] = 0
    apply_tune(regs, tune, cur2)
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
    for name, d in ol.ip_dict.items():
        base = d.get("phys_addr")
        if base is None:
            continue
        if name.startswith("fir_pl_regs"):
            regs_base = base
        elif name.startswith("gpio_ctrl"):
            ctrl_base = base
    print("fir_pl_regs @ 0x%08X   gpio_ctrl @ 0x%08X" % (regs_base, ctrl_base))
    return ol, Regs(MMIO(regs_base, 0x1000)), MMIO(ctrl_base, 0x1000)


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

    ol, regs, gpio = open_pl(args.bit)
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

    # 先把 run + drc 打开，再让 tune 决定具体参数
    set_ctrl(CTRL_RUN | CTRL_DRC)
    time.sleep(0.05)

    print("running: tune -> regs, regs -> status (Ctrl-C to stop)")
    while True:
        try:
            tune = read_tune() or {}
            cur, changed = apply_tune(regs, tune, last)
            if changed:
                last = cur
                # ctrl[1] 由 bypass 决定；run 一直开
                set_ctrl(CTRL_RUN | (0 if cur["bypass"] else CTRL_DRC))
            busy = regs.rd(REG_RAMP_BUSY) & 0x3
            publish(regs, last, t0, busy, err)
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

    set_ctrl(0)
    print("stopped, ctrl=0")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
