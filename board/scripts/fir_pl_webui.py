#!/usr/bin/env python3
"""fir_pl_webui —— 网页调音台第三档：PL 直连版的后端。

和另外两档的分工：

    fir_webui.py     第一/二档：拉起 `fir_live.py`（PS 在环里逐块算），
                     有块长、延迟档、录音、听力测试那一套。
    fir_pl_webui.py  第三档：**不碰样本**，只读写两个 json 文件：
                        /dev/shm/fir_tune.json    ← 页面写的参数
                        /dev/shm/fir_status.json  → 页面读的状态（fir_pl_live.py 写）
                     实际把参数变成寄存器动作的是 `fir_pl_live.py`（独立进程，
                     它才持有 overlay）。这个进程只做网页和文件协议之间的翻译。

页面是 PL 档专用的 `pl.html`（2026-10-29 起），接口只实现它在用的那几个：

    GET  /                 pl.html（PL 专用页）
    GET  /page.html        第一/二档那份页面，留着对照用
    GET  /api/status       读 fir_status.json（超过 stale 秒没更新就报 stale）
    GET  /api/tune         读 fir_tune.json（没有则 null，页面会填默认值）
    POST /api/tune         写 fir_tune.json（这里做范围钳制，和第一档一致）
    GET  /api/scope        读抓取缓冲快照（fir_pl_live.py 每秒写一次）
    GET  /api/play         可播放的 WAV 列表 + 当前播放项
    POST /api/play         循环播放固定音频 {"file": "xxx.wav"}
    POST /api/play/stop    切回麦克风
    GET  /api/health       位流/进程/新鲜度
    POST /api/start        写 run=1
    POST /api/stop         写 run=0

用法（板上）：

    XILINX_XRT=/usr python3 fir_pl_webui.py                 # 默认 0.0.0.0:8080
    XILINX_XRT=/usr python3 fir_pl_webui.py --port 8081
    python3 fir_pl_webui.py --selftest                      # PC 上可跑，不碰板子

为什么 run 用文件而不是直接写 MMIO：overlay 只能在一个进程里加载，
网页进程去碰 MMIO 会和 fir_pl_live.py 抢。文件是唯一共享状态。
"""
import argparse
import json
import os
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
TUNE_PATH = os.environ.get("FIR_TUNE", "/dev/shm/fir_tune.json")
STATUS_PATH = os.environ.get("FIR_STATUS", "/dev/shm/fir_status.json")
RUN_PATH = os.environ.get("FIR_PL_RUN", "/dev/shm/fir_pl_run.txt")
BIT_PATH = os.environ.get("FIR_PL_BIT", "/home/xilinx/dma_exp/fir_pl.bit")
# C3 抓取缓冲 / 循环播放（2026-10-30）
SCOPE_PATH = os.environ.get("FIR_PL_SCOPE", "/dev/shm/fir_scope.json")
PLAY_PATH = os.environ.get("FIR_PL_PLAY", "/dev/shm/fir_pl_play.txt")
WAV_DIR = os.environ.get("FIR_PL_WAV_DIR", "/home/xilinx/dma_exp/wav")

VERSION = "pl-webui 2026-10-28"
STALE_S = 3.0            # status 超过这么久没更新 = 后端进程不在

# 和 fir_webui.py 第一档完全一致的取值范围，页面滑杆也按这个画
R_GAIN = (-24.0, 42.0)
R_THR = (-60.0, 0.0)
R_RATIO = (0.0, 0.95)
R_ATK = (0.0, 50.0)
R_REL = (0.0, 500.0)
R_MPO = (-40.0, 0.0)           # 输出上限（dBFS）；0 = 不动作

# 第一次打开页面时给什么。三个 DRC 时间参数 = 寄存器出厂值（419456 / 2936064 /
# 17408 / 896 换算成 dB、ratio、ms）；门限和输出限幅按演示口径改成 -10 / -6 dBFS。
# 增益默认 +22 dB：模拟 PGA 已经抬到 +35.25 dB（比出厂的 +21 高 14 dB），
# 数字这边同量减回去，总响度不变、底噪里能被数字抬起来的那部分少 14 dB。
DEFAULT_TUNE = {
    "gain_db": [22.0, 22.0, 22.0, 22.0],
    "thr_db": [-10.0, -10.0, -10.0, -10.0],
    "ratio": [0.7, 0.7, 0.7, 0.7],
    "attack_ms": [5.0, 5.0, 5.0, 5.0],
    "release_ms": [100.0, 100.0, 100.0, 100.0],
    "bypass": False,
    "mpo_db": -6.0,
}


# ---------------------------------------------------------------- 四档预设
# 为什么要有这个表：失真与压缩深度是同一条曲线上的两端，而"当前默认档到底测没测过"
# 这件事必须能一眼查到。10-31 做 THD+N 定位时演示默认还是 −20 dBFS / MPO 0 dBFS；
# 11-01 改成 −10 dBFS / MPO −6 dBFS；**11-02 已按新默认补测**（0.09%，压缩器未启动）。
# `soft` 档仍没有单独记 THD+N 总数，所以写 None（见 report/design_report.md §5.1）。
#
# 每一档的 thdn 字段只放**该档位自己的实测值**，测过的写数字，没测过的写 None + 原因。
# 复测命令见 data/results/thdn_20261102.md。
PRESETS = {
    "demo": {
        "label": "演示默认（2026-11-01 起）",
        "tune": dict(DEFAULT_TUNE),
        "thdn": 0.0009,
        "measured": "2026-11-02 实测 0.09%（+22 dB / −10 dBFS / 0.7，压缩器未启动）= 输入本底",
        "note": "开机默认。11-02 复测说明默认参数下压缩器并未启动，THD 等于本底；"
                "这里的「效果」来自 MPO −6 的限幅（另一机制，单独动作时 48.4%），不是压缩本身。"
                "要让人听见「它在压缩」请切 show 档。",
    },
    "show": {
        "label": "压缩确认动作档（+30 dB）",
        "tune": {"gain_db": [30.0] * 4, "thr_db": [-10.0] * 4, "ratio": [0.7] * 4,
                 "attack_ms": [5.0] * 4, "release_ms": [100.0] * 4,
                 "bypass": False, "mpo_db": 0.0},
        "thdn": 0.0236,
        "measured": "2026-11-02 实测 2.36%（+30 dB / −10 dBFS / 0.7，压缩器确认动作；"
                    "满幅音源、入核峰值 803 计数）",
        "note": "四档里唯一「压缩器确实在压」且失真仍可控的一档，现场 A/B 用它听压缩效果。"
                "2.36% 是满幅音源下的读数，换语音素材电平不同，听感要人耳确认一次；"
                "若出现削顶，把 mpo_db 降到 −6 再听，那一档的 THD 需重测（thdn.py）。",
    },
    "soft": {
        "label": "轻压（10-31 边界档）",
        "tune": {"gain_db": [14.0] * 4, "thr_db": [-20.0] * 4, "ratio": [0.7] * 4,
                 "attack_ms": [5.0] * 4, "release_ms": [100.0] * 4,
                 "bypass": False, "mpo_db": 0.0},
        "thdn": None,
        "measured": "10-31 实测：段增益降到 14 dB、包络正好压在门限上，谐波回到 −90 dBc 量级；"
                    "THD+N 总数未单独记",
        "note": "想压一点又不想出粗糙感的中间档。14 dB 是当时量到的边界，不是随便取的数。",
    },
    "clean": {
        "label": "低失真（压缩与限幅全关）",
        "tune": {"gain_db": [22.0] * 4, "thr_db": [0.0] * 4, "ratio": [0.7] * 4,
                 "attack_ms": [5.0] * 4, "release_ms": [100.0] * 4,
                 "bypass": False, "mpo_db": 0.0},
        "thdn": 0.0007,
        "measured": "10-31 实测 0.07%（第 1 组：门限抬到 0 dBFS、限幅器不动作）= 输入本底，"
                    "定点前端链不产生失真",
        "note": "A/B 的「未压缩」参照面：四段分频与 +22 dB 增益都在，只是不压缩。"
                "现场以音质优先时切这一档。",
    },
}


# ---------------------------------------------------------------- 文件协议
def _read_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def read_run():
    try:
        with open(RUN_PATH, "r") as f:
            return f.read().strip() != "0"
    except Exception:
        return False


def write_run(on):
    tmp = RUN_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.write("1" if on else "0")
    os.replace(tmp, RUN_PATH)


def _clamp(x, lo, hi, dflt):
    try:
        v = float(x)
    except Exception:
        return dflt
    if v != v:                     # NaN
        return dflt
    return max(lo, min(hi, v))


def _four(v, lo, hi, dflt):
    if isinstance(v, (list, tuple)):
        out = [_clamp(x, lo, hi, dflt) for x in v][:4]
        while len(out) < 4:
            out.append(out[-1] if out else dflt)
        return out
    return [_clamp(v, lo, hi, dflt)] * 4


def sanitize_tune(body):
    """页面 → 文件。字段名、范围与第一档逐条对齐，换算仍然留给 fir_pl_live。

    2026-10-29：多了可选的 b1（右耳处方）。没带 b1 就是"两只耳朵一样"，
    后端会把 bank0 镜像到 bank1；带了就各写各的。范围钳制跟左耳同一套。
    """
    t = body or {}
    out = {
        "gain_db":    [round(x, 2) for x in _four(t.get("gain_db"), R_GAIN[0], R_GAIN[1], 0.0)],
        "thr_db":     [round(x, 2) for x in _four(t.get("thr_db"), R_THR[0], R_THR[1], -10.0)],
        "ratio":      [round(x, 4) for x in _four(t.get("ratio"), R_RATIO[0], R_RATIO[1], 0.5)],
        "attack_ms":  [round(x, 2) for x in _four(t.get("attack_ms"), R_ATK[0], R_ATK[1], 5.0)],
        "release_ms": [round(x, 2) for x in _four(t.get("release_ms"), R_REL[0], R_REL[1], 100.0)],
        "bypass":     bool(t.get("bypass")),
        "mpo_db":     round(_clamp(t.get("mpo_db", 0.0), R_MPO[0], R_MPO[1], 0.0), 1),
    }
    # B4 测听：tone.on 时 PL 输出被四段中心频率的方波顶替
    tn = t.get("tone")
    if isinstance(tn, dict):
        out["tone"] = {
            "on":     bool(tn.get("on")),
            "band":   int(_clamp(tn.get("band", 0), 0, 3, 0)),
            "amp_db": round(_clamp(tn.get("amp_db", -22.0), -60.0, 0.0, -22.0), 1),
        }

    b = t.get("b1")
    if isinstance(b, dict):
        # 缺哪项就用左耳的值顶上，避免"只给增益"时右耳被写成 0
        out["b1"] = {
            "gain_db":    [round(x, 2) for x in _four(b.get("gain_db", out["gain_db"]),
                                                     R_GAIN[0], R_GAIN[1], 0.0)],
            "thr_db":     [round(x, 2) for x in _four(b.get("thr_db", out["thr_db"]),
                                                     R_THR[0], R_THR[1], -10.0)],
            "ratio":      [round(x, 4) for x in _four(b.get("ratio", out["ratio"]),
                                                     R_RATIO[0], R_RATIO[1], 0.5)],
            "attack_ms":  [round(x, 2) for x in _four(b.get("attack_ms", out["attack_ms"]),
                                                     R_ATK[0], R_ATK[1], 5.0)],
            "release_ms": [round(x, 2) for x in _four(b.get("release_ms", out["release_ms"]),
                                                     R_REL[0], R_REL[1], 100.0)],
        }
    return out


def patch_tune(upd):
    """在当前 tune 上打补丁再写回（测听接口用，不想覆盖用户其它参数）。"""
    t = _read_json(TUNE_PATH) or dict(DEFAULT_TUNE)
    t = dict(t)
    t.update(upd)
    t = sanitize_tune(t)
    _write_json(TUNE_PATH, t)
    return t


def status_age(st):
    if not st:
        return None
    t = st.get("t")
    if not isinstance(t, (int, float)):
        return None
    return round(time.time() - t, 2)


IIO_DIR = "/sys/bus/iio/devices/iio:device0"      # Zynq XADC，CPU 温度


def _read_txt(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except Exception:
        return ""


def _board_health():
    """和第一档 `fir_webui._health()` 同样的几个量，页面那块才不会是"—"。
    读不到就返回 None，不要瞎编。"""
    temp = None
    try:
        raw = int(_read_txt(IIO_DIR + "/in_temp0_raw").strip())
        off = int(_read_txt(IIO_DIR + "/in_temp0_offset").strip())
        sca = float(_read_txt(IIO_DIR + "/in_temp0_scale").strip())
        temp = round((raw + off) * sca / 1000.0, 1)
    except (OSError, ValueError):
        temp = None
    mem = None
    try:
        m = re.search(r"MemAvailable:\s+(\d+)", _read_txt("/proc/meminfo"))
        if m:
            mem = int(int(m.group(1)) / 1024)
    except Exception:
        mem = None
    up = None
    try:
        up = int(float(_read_txt("/proc/uptime").split()[0]))
    except Exception:
        up = None
    load = _read_txt("/proc/loadavg").split()[:3] or None
    return temp, mem, up, load


def health():
    st = _read_json(STATUS_PATH)
    age = status_age(st)
    temp_c, mem_mb, up_s, load = _board_health()
    bit_ok = os.path.exists(BIT_PATH)
    try:
        bit_mb = round(os.path.getsize(BIT_PATH) / 1048576.0, 2) if bit_ok else None
    except Exception:
        bit_mb = None
    return {
        "ok": bool(st) and age is not None and age < STALE_S,
        "mode": "pl",
        "ver": VERSION,
        "bit": {"path": BIT_PATH, "exists": bit_ok, "mb": bit_mb},
        "status_age_s": age,
        "status_path": STATUS_PATH,
        "tune_path": TUNE_PATH,
        "run": read_run(),
        "stale_s": STALE_S,
        "obs": "peak/env/gr are real signals" if st and st.get("env") else "no obs",
        # 页面右上角那块"板子健康"要这几个字段，和第一档同名同义
        "temp_c": temp_c,
        "mem_avail_mb": mem_mb,
        "uptime_s": up_s,
        "load": load,
        "guard": None,
    }


# ---------------------------------------------------------------- HTTP
def page_html(name="pl.html"):
    """PL 档专用页优先；文件不在就退回 page.html（板上没推新页时不至于 404）。"""
    path = os.path.join(HERE, name)
    if name == "pl.html" and not os.path.exists(path):
        path = os.path.join(HERE, "page.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class Handler(BaseHTTPRequestHandler):
    server_version = "fir-pl-webui/1.0"

    def log_message(self, fmt, *args):     # 板上刷屏没意义，只留错误
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        p = urlparse(self.path).path
        try:
            if p in ("/", "/index.html", "/pl.html"):
                return self._send(200, page_html("pl.html"), "text/html; charset=utf-8")
            if p == "/page.html":
                return self._send(200, page_html("page.html"), "text/html; charset=utf-8")
            if p == "/api/status":
                st = _read_json(STATUS_PATH)
                if not st:
                    return self._json({"ok": False, "running": False,
                                       "status": {"ok": False, "running": False, "mode": "pl"},
                                       "proc": {"running": False},
                                       "error": "no status file (%s)" % STATUS_PATH})
                st = dict(st)
                age = status_age(st)
                st["age"] = age
                st["stale"] = (age is None) or (age >= STALE_S)
                st["mode"] = "pl"
                # 页面(page.html/pl.html)读的是 {"status": {...}, "proc": {...}, "age": n}；
                # 我的探针/脚本按扁平字段读。两边都给，别二选一。
                # 2026-10-29：只给扁平的时候页面的 audioOn 永远是 false，
                # 于是"停止"按钮一直是灰的 —— 用户看到的就是"断开不了"。
                return self._json(dict(st,
                                       status=st,
                                       proc={"running": False, "starting": False,
                                             "stopping": False, "log": ""},
                                       age=age))
            if p == "/api/tune":
                # 结构必须和第一档一致：{"ok": true, "tune": {...}}。
                # 页面初始化是 `if (r.ok && r.tune) applyTune(r.tune); else flushTune()`
                # —— 少了这层包装它会走 flushTune，把页面自带的默认值（增益 0 dB、
                # 门限 −10 dBFS）写回来，演示一上来就是"听不见"。
                # 文件不存在时也不要给 null，给「寄存器出厂值 + 演示增益」。
                return self._json({"ok": True,
                                   "tune": _read_json(TUNE_PATH) or dict(DEFAULT_TUNE)})
            if p == "/api/presets":
                # 四档预设连同「哪一档测过、哪一档没测」一起给页面。
                # 这里把 measured 原文带出去，是为了让人在切档之前就知道数字的可信度。
                return self._json({"ok": True, "presets": [
                    {"name": k, "label": v["label"], "tune": sanitize_tune(v["tune"]),
                     "thdn": v["thdn"], "measured": v["measured"], "note": v["note"]}
                    for k, v in PRESETS.items()]})
            if p == "/api/scope":
                # C3：fir_pl_live.py 每秒把抓取缓冲写成 SCOPE_PATH。
                # 文件不在 = 后端没跑/位流还是旧的，别编数据骗页面。
                sc = _read_json(SCOPE_PATH)
                if not sc or not sc.get("in"):
                    return self._json({"ok": False,
                                       "msg": "还没有抓取数据（fir_pl_live.py 在跑吗？位流是不是新编的？）"})
                sc = dict(sc)
                sc["age"] = round(max(0.0, time.time() - (sc.get("t") or 0)), 2)
                return self._json(sc)
            if p == "/api/play":
                files = []
                try:
                    for fn in sorted(os.listdir(WAV_DIR)):
                        if fn.lower().endswith(".wav"):
                            files.append(fn)
                except Exception:
                    pass
                cur = None
                try:
                    cur = open(PLAY_PATH).read().strip() or None
                except Exception:
                    pass
                if cur and cur.lower() == "off":
                    cur = None
                return self._json({"ok": True, "files": files,
                                   "playing": os.path.basename(cur) if cur else None})
            if p == "/api/health":
                return self._json(health())
            if p == "/api/rec":
                return self._json({"ok": False, "msg": "PL 直连版没有录音缓冲"}, 501)
            return self._json({"ok": False, "msg": "no such endpoint: " + p}, 404)
        except Exception as ex:
            return self._json({"ok": False, "msg": "%s: %s" % (type(ex).__name__, ex)}, 500)

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            body = {}
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    return self._json({"ok": False, "msg": "body 不是 json"}, 400)

            if p == "/api/tune":
                t = sanitize_tune(body)
                _write_json(TUNE_PATH, t)
                return self._json({"ok": True, "tune": t})
            if p == "/api/preset":
                # 切预设 = 整体替换 tune（不是打补丁）。给演示/答辩时一键换档用。
                name = str(body.get("name") or "")
                if name not in PRESETS:
                    return self._json({"ok": False,
                                       "msg": "没有这个预设：%s（可选：%s）"
                                              % (name or "（空）", ", ".join(PRESETS))}, 400)
                t = sanitize_tune(PRESETS[name]["tune"])
                _write_json(TUNE_PATH, t)
                return self._json({"ok": True, "preset": name,
                                   "tune": t, "thdn": PRESETS[name]["thdn"],
                                   "measured": PRESETS[name]["measured"]})
            if p == "/api/start":
                # 页面的 src / l / nbuf / wav 在 PL 直连里没有意义，收下但不使用
                write_run(True)
                st = _read_json(STATUS_PATH)
                age = status_age(st)
                ok = bool(st) and age is not None and age < STALE_S
                return self._json({
                    "ok": True, "mode": "pl",
                    "msg": ("PL 直连：不看块长/来源，通路一直硬实时。"
                            + ("" if ok else " 注意：fir_status.json 没在更新，确认 fir_pl_live.py 在跑。")),
                    "backend_alive": ok,
                })
            if p == "/api/stop":
                write_run(False)
                return self._json({"ok": True, "msg": "已把 run 写 0（PL 通路停）"})
            if p == "/api/play":
                # 循环播放固定音频：只认 WAV_DIR 里的文件名，别把路径穿出去
                fn = os.path.basename(str(body.get("file") or ""))
                path = os.path.join(WAV_DIR, fn)
                if not fn or not os.path.isfile(path):
                    return self._json({"ok": False, "msg": "没有这个音频文件：%s" % (fn or "（空）")}, 400)
                tmp = PLAY_PATH + ".tmp"
                with open(tmp, "w") as f:
                    f.write(path)
                os.replace(tmp, PLAY_PATH)
                return self._json({"ok": True, "file": fn,
                                   "msg": "已请求循环播放（fir_pl_live 每 0.1 秒看一次）"})
            if p == "/api/play/stop":
                tmp = PLAY_PATH + ".tmp"
                with open(tmp, "w") as f:
                    f.write("off")
                os.replace(tmp, PLAY_PATH)
                return self._json({"ok": True, "msg": "已请求停止播放，切回麦克风"})
            if p == "/api/hearing/tone":
                # B4：按四段中心频率放测试音。band 0..3 → 250/750/1500/3000 Hz
                # 两种调用方式都认：{band:0..3} 或 {freq_hz:250}
                if body.get("band") is not None:
                    band = int(body["band"]) & 3
                elif body.get("freq_hz") is not None:
                    f = float(body["freq_hz"])
                    band = min(range(4), key=lambda i: abs([250, 750, 1500, 3000][i] - f))
                else:
                    band = 0
                amp = _clamp(body.get("amp_db", body.get("level_db", -22.0)),
                             -60.0, 0.0, -22.0)
                t = patch_tune({"tone": {"on": True, "band": band, "amp_db": amp}})
                return self._json({"ok": True, "tone": t.get("tone"),
                                   "hz": [250, 750, 1500, 3000][band]})
            if p == "/api/hearing/end":
                t = patch_tune({"tone": {"on": False, "band": 0, "amp_db": -22.0}})
                return self._json({"ok": True, "tone": t.get("tone")})
            return self._json({"ok": False, "msg": "no such endpoint: " + p}, 404)
        except Exception as ex:
            return self._json({"ok": False, "msg": "%s: %s" % (type(ex).__name__, ex)}, 500)


def serve(port, host="0.0.0.0"):
    srv = ThreadingHTTPServer((host, port), Handler)
    print("%s on http://%s:%d/  status=%s tune=%s" % (VERSION, host, port,
                                                      STATUS_PATH, TUNE_PATH))
    print("press Ctrl-C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


# ---------------------------------------------------------------- 自检
def selftest():
    """起一个临时服务，把页面用到的每个接口都过一遍。不加载 overlay。"""
    import urllib.error
    import urllib.request

    global TUNE_PATH, STATUS_PATH, RUN_PATH, BIT_PATH, SCOPE_PATH, PLAY_PATH, WAV_DIR
    tmpdir = tempfile.mkdtemp(prefix="plwebui_")
    TUNE_PATH = os.path.join(tmpdir, "fir_tune.json")
    STATUS_PATH = os.path.join(tmpdir, "fir_status.json")
    RUN_PATH = os.path.join(tmpdir, "fir_pl_run.txt")
    BIT_PATH = os.path.join(tmpdir, "fir_pl.bit")
    SCOPE_PATH = os.path.join(tmpdir, "fir_scope.json")
    PLAY_PATH = os.path.join(tmpdir, "fir_pl_play.txt")
    WAV_DIR = os.path.join(tmpdir, "wav")
    os.makedirs(WAV_DIR)
    with open(os.path.join(WAV_DIR, "demo.wav"), "wb") as f:
        f.write(b"RIFF")
    with open(BIT_PATH, "wb") as f:
        f.write(b"\x00" * 1024)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = "http://127.0.0.1:%d" % port

    def get(path):
        try:
            with urllib.request.urlopen(base + path, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:       # 4xx/5xx 也要能读到 body
            return e.code, json.loads(e.read().decode("utf-8"))

    def post(path, body=None):
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:       # 4xx/5xx 也要能读到 body
            return e.code, json.loads(e.read().decode("utf-8"))

    bad = 0

    def chk(name, cond, extra=""):
        nonlocal bad
        bad += 0 if cond else 1
        print("  %-34s %s%s" % (name, "OK" if cond else "FAIL", (" " + str(extra)) if extra else ""))

    def get_text(path):
        """拿 HTML 原文（get() 只吃 json）。"""
        with urllib.request.urlopen(base + path, timeout=5) as r:
            return r.status, r.read().decode("utf-8")

    print("== 1. 页面 ==")
    code, html = get_text("/")
    chk("GET / 返回 pl.html", code == 200 and "/api/status" in html
        and "pl.html" not in html[:200], code)
    chk("pl.html 有 PL 档的粘合层", 'id="gains-r"' in html and 'id="c-mpo"' in html)
    code2, old_html = get_text("/page.html")
    chk("GET /page.html 也还在", code2 == 200 and len(old_html) > 1000, code2)

    print("== 2. 没有后端时 ==")
    code, j = get("/api/status")
    chk("status 无文件 -> ok=false", j.get("ok") is False and j.get("running") is False)
    j = get("/api/tune")[1]
    t0 = j.get("tune") if isinstance(j, dict) else None
    chk("tune 无文件 -> ok/tune 包装 + 演示默认",
        j.get("ok") is True and isinstance(t0, dict)
        and t0.get("gain_db") == [22.0] * 4
        and t0.get("thr_db") == [-10.0] * 4 and t0.get("ratio") == [0.7] * 4, t0)
    code, j = get("/api/health")
    chk("health.ok=false", j.get("ok") is False and j.get("mode") == "pl")
    chk("health 报告位流存在", j.get("bit", {}).get("exists") is True)

    print("== 3. 写参数（范围钳制） ==")
    code, j = post("/api/tune", {"gain_db": [30, 100, -100, 6],
                                 "thr_db": [-20, 5, -80, -10],
                                 "ratio": [0.7, 2.0, -1, 0.5],
                                 "attack_ms": [5, 999, -1, 5],
                                 "release_ms": [100, 0, 999, 100],
                                 "bypass": 1})
    t = j.get("tune") or {}
    chk("gain 钳到 [-24,42]", t.get("gain_db") == [30.0, 42.0, -24.0, 6.0], t.get("gain_db"))
    chk("thr 钳到 [-60,0]", t.get("thr_db") == [-20.0, 0.0, -60.0, -10.0], t.get("thr_db"))
    chk("ratio 钳到 [0,0.95]", t.get("ratio") == [0.7, 0.95, 0.0, 0.5], t.get("ratio"))
    chk("attack 钳到 [0,50]", t.get("attack_ms") == [5.0, 50.0, 0.0, 5.0], t.get("attack_ms"))
    chk("release 钳到 [0,500]", t.get("release_ms") == [100.0, 0.0, 500.0, 100.0], t.get("release_ms"))
    chk("bypass 转 bool", t.get("bypass") is True)
    chk("写进去能读回", (get("/api/tune")[1] or {}).get("tune") == t)

    code, j = post("/api/tune", {"gain_db": [30] * 4, "mpo_db": -100})
    chk("mpo_db 钳到 [-40,0]", (j.get("tune") or {}).get("mpo_db") == -40.0,
        (j.get("tune") or {}).get("mpo_db"))
    code, j = post("/api/tune", {"gain_db": [30] * 4, "mpo_db": 3})
    chk("mpo_db 上界 0", (j.get("tune") or {}).get("mpo_db") == 0.0,
        (j.get("tune") or {}).get("mpo_db"))
    code, j = post("/api/tune", {"gain_db": [30] * 4})
    chk("不带 mpo_db 时默认 0", (j.get("tune") or {}).get("mpo_db") == 0.0)

    print("== 3b. 右耳处方 b1 ==")
    code, j = post("/api/tune", {"gain_db": [30] * 4, "b1": {"gain_db": [0, 6, 12, 18]}})
    tb = (j.get("tune") or {}).get("b1") or {}
    chk("b1 保留并被钳制", tb.get("gain_db") == [0.0, 6.0, 12.0, 18.0], tb.get("gain_db"))
    chk("b1 缺的字段继承左耳", tb.get("thr_db") == [-10.0] * 4, tb.get("thr_db"))
    chk("不带 b1 时文件里没有 b1", "b1" not in (post("/api/tune", {"gain_db": [30] * 4})[1].get("tune") or {}))

    print("== 3c. 四档预设 ==")
    j = get("/api/presets")[1]
    names = [q.get("name") for q in (j.get("presets") or [])]
    chk("presets 四档齐全", j.get("ok") is True and names == ["demo", "show", "soft", "clean"], names)
    code, j = post("/api/preset", {"name": "clean"})
    t = j.get("tune") or {}
    chk("clean 档 = 门限 0 dBFS + 限幅不动作", code == 200 and j.get("preset") == "clean"
        and t.get("thr_db") == [0.0] * 4 and t.get("mpo_db") == 0.0, t.get("thr_db"))
    chk("clean 档带得住自己的实测 THD+N", abs((j.get("thdn") or 0) - 0.0007) < 1e-9, j.get("thdn"))
    code, j = post("/api/preset", {"name": "demo"})
    chk("demo 档带得住自己的实测 THD+N",
        code == 200 and abs((j.get("thdn") or 0) - 0.0009) < 1e-9
        and (j.get("tune") or {}).get("thr_db") == [-10.0] * 4, j.get("thdn"))
    code, j = post("/api/preset", {"name": "show"})
    t = j.get("tune") or {}
    chk("show 档 = +30 dB 且压缩器门限 −10 dBFS", code == 200 and j.get("preset") == "show"
        and t.get("gain_db") == [30.0] * 4 and t.get("thr_db") == [-10.0] * 4, t.get("gain_db"))
    chk("show 档带得住自己的实测 THD+N",
        abs((j.get("thdn") or 0) - 0.0236) < 1e-9, j.get("thdn"))
    code, j = post("/api/preset", {"name": "soft"})
    chk("soft 档 thdn 必须是 None（没实测就不许填数）",
        code == 200 and j.get("thdn") is None, j.get("thdn"))
    code, j = post("/api/preset", {"name": "nope"})
    chk("未知预设返回 400", code == 400 and j.get("ok") is False, code)

    print("== 4. 短列表会补齐 ==")
    code, j = post("/api/tune", {"gain_db": [12]})
    chk("单值补成四个", (j.get("tune") or {}).get("gain_db") == [12.0] * 4)

    print("== 5. start / stop 走 run 文件 ==")
    code, j = post("/api/start", {"src": "mic", "l": 240, "nbuf": 4})
    chk("start ok=true", j.get("ok") is True)
    chk("run 文件 = 1", read_run() is True)
    chk("start 提示后端没在跑", j.get("backend_alive") is False)
    code, j = post("/api/stop")
    chk("stop 后 run 文件 = 0", read_run() is False)

    print("== 6. 后端在跑时的 status ==")
    _write_json(STATUS_PATH, {"t": time.time(), "ok": True, "running": True,
                              "mode": "pl", "peak_in": 16384, "peak_in_db": -6.0,
                              "env": [0.5, 0, 0, 0], "env_db": [-6.0, None, None, None],
                              "gr_db": [-12.0, None, None, None],
                              "gain_db": [30, 30, 30, 30], "rt": 1.0})
    code, j = get("/api/status")
    chk("status 透出观察量", j.get("peak_in_db") == -6.0 and j.get("env_db", [None])[0] == -6.0)
    chk("status 带 age/stale", j.get("age") is not None and j.get("stale") is False)
    code, j = get("/api/health")
    chk("health.ok=true", j.get("ok") is True, j.get("status_age_s"))
    need_keys = {"temp_c", "mem_avail_mb", "uptime_s", "load", "guard", "bit", "run"}
    chk("health 带页面要的字段", need_keys <= set(j), sorted(need_keys - set(j)))
    code, j = post("/api/start")
    chk("start 能看到 backend_alive", j.get("backend_alive") is True)

    print("== 7. 抓取缓冲 / 测听接口 ==")
    code, j = get("/api/scope")
    chk("没抓取数据时 scope 明说 ok=false", j.get("ok") is False and j.get("msg"))
    _write_json(SCOPE_PATH, {"ok": True, "t": time.time(), "fs": 48828, "n": 4,
                             "in": [0, 1, -1, 32767], "out": [0, 2, -2, 30000]})
    code, j = get("/api/scope")
    chk("scope 透传快照并带 age",
        j.get("ok") is True and j.get("in") == [0, 1, -1, 32767] and j.get("age") is not None)

    print("== 7b. 循环播放接口 ==")
    code, j = get("/api/play")
    chk("play 列出 WAV", "demo.wav" in (j.get("files") or []))
    code, j = post("/api/play", {"file": "../../etc/passwd"})
    chk("play 拒绝路径穿越", j.get("ok") is False, code)
    code, j = post("/api/play", {"file": "nope.wav"})
    chk("play 拒绝不存在的文件", j.get("ok") is False, code)
    code, j = post("/api/play", {"file": "demo.wav"})
    chk("play 接受已有文件", j.get("ok") is True)
    chk("play 写的是 WAV_DIR 里的绝对路径",
        open(PLAY_PATH).read().strip() == os.path.join(WAV_DIR, "demo.wav"))
    code, j = post("/api/play/stop")
    chk("play/stop 写 off", open(PLAY_PATH).read().strip() == "off")
    code, j = post("/api/hearing/tone", {"band": 2, "amp_db": -30})
    chk("hearing/tone 打开测听", j.get("ok") is True and j.get("hz") == 1500
        and (j.get("tone") or {}).get("band") == 2)
    chk("测听音幅度钳到 [-60,0]",
        (post("/api/hearing/tone", {"band": 0, "amp_db": 5})[1].get("tone") or {}).get("amp_db") == 0.0)
    code, j = post("/api/hearing/end", {})
    chk("hearing/end 关掉测听", j.get("ok") is True and (j.get("tone") or {}).get("on") is False)
    code, j = get("/api/nope")
    chk("未知接口 404", code == 404 and j.get("ok") is False, code)

    srv.shutdown()
    srv.server_close()
    print("")
    print("SELFTEST %s" % ("PASS" if bad == 0 else "FAIL (%d)" % bad))
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    return serve(args.port, args.host)


if __name__ == "__main__":
    sys.exit(main() or 0)
