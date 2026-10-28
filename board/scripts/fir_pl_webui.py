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

页面复用 `page.html`，接口只实现它在用的那几个：

    GET  /                 page.html
    GET  /api/status       读 fir_status.json（超过 stale 秒没更新就报 stale）
    GET  /api/tune         读 fir_tune.json（没有则 null，页面会填默认值）
    POST /api/tune         写 fir_tune.json（这里做范围钳制，和第一档一致）
    GET  /api/scope        PL 直连没有波形缓冲（RTL 里是桩），返回 ok=false
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

VERSION = "pl-webui 2026-10-28"
STALE_S = 3.0            # status 超过这么久没更新 = 后端进程不在

# 和 fir_webui.py 第一档完全一致的取值范围，页面滑杆也按这个画
R_GAIN = (-24.0, 42.0)
R_THR = (-60.0, 0.0)
R_RATIO = (0.0, 0.95)
R_ATK = (0.0, 50.0)
R_REL = (0.0, 500.0)


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
    """页面 → 文件。字段名、范围与第一档逐条对齐，换算仍然留给 fir_pl_live。"""
    t = body or {}
    return {
        "gain_db":    [round(x, 2) for x in _four(t.get("gain_db"), R_GAIN[0], R_GAIN[1], 0.0)],
        "thr_db":     [round(x, 2) for x in _four(t.get("thr_db"), R_THR[0], R_THR[1], -10.0)],
        "ratio":      [round(x, 4) for x in _four(t.get("ratio"), R_RATIO[0], R_RATIO[1], 0.5)],
        "attack_ms":  [round(x, 2) for x in _four(t.get("attack_ms"), R_ATK[0], R_ATK[1], 5.0)],
        "release_ms": [round(x, 2) for x in _four(t.get("release_ms"), R_REL[0], R_REL[1], 100.0)],
        "bypass":     bool(t.get("bypass")),
    }


def status_age(st):
    if not st:
        return None
    t = st.get("t")
    if not isinstance(t, (int, float)):
        return None
    return round(time.time() - t, 2)


def health():
    st = _read_json(STATUS_PATH)
    age = status_age(st)
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
    }


# ---------------------------------------------------------------- HTTP
def page_html():
    with open(os.path.join(HERE, "page.html"), "r", encoding="utf-8") as f:
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
            if p in ("/", "/index.html", "/page.html"):
                return self._send(200, page_html(), "text/html; charset=utf-8")
            if p == "/api/status":
                st = _read_json(STATUS_PATH)
                if not st:
                    return self._json({"ok": False, "running": False,
                                       "error": "no status file (%s)" % STATUS_PATH})
                st = dict(st)
                st["age"] = status_age(st)
                st["stale"] = (st["age"] is None) or (st["age"] >= STALE_S)
                st["mode"] = "pl"
                return self._json(st)
            if p == "/api/tune":
                return self._json(_read_json(TUNE_PATH))
            if p == "/api/scope":
                # 抓取缓冲是 RTL 里的桩（C3/C4 未做），这里不编数据骗页面
                return self._json({"ok": False,
                                   "msg": "PL 直连版没有波形缓冲（C3/C4 未实现）"})
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
            if p == "/api/hearing/tone":
                return self._json({"ok": False,
                                   "msg": "听力测试要按频率放音，PL 直连版暂时没有这条路径"})
            if p == "/api/hearing/end":
                return self._json({"ok": True})
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

    global TUNE_PATH, STATUS_PATH, RUN_PATH, BIT_PATH
    tmpdir = tempfile.mkdtemp(prefix="plwebui_")
    TUNE_PATH = os.path.join(tmpdir, "fir_tune.json")
    STATUS_PATH = os.path.join(tmpdir, "fir_status.json")
    RUN_PATH = os.path.join(tmpdir, "fir_pl_run.txt")
    BIT_PATH = os.path.join(tmpdir, "fir_pl.bit")
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
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    bad = 0

    def chk(name, cond, extra=""):
        nonlocal bad
        bad += 0 if cond else 1
        print("  %-34s %s%s" % (name, "OK" if cond else "FAIL", (" " + str(extra)) if extra else ""))

    print("== 1. 页面 ==")
    with urllib.request.urlopen(base + "/", timeout=5) as r:
        html = r.read().decode("utf-8")
    chk("GET / 返回 page.html", r.status == 200 and "html" in html[:200].lower()
        and "/api/status" in html)

    print("== 2. 没有后端时 ==")
    code, j = get("/api/status")
    chk("status 无文件 -> ok=false", j.get("ok") is False and j.get("running") is False)
    chk("tune 无文件 -> null", get("/api/tune")[1] is None)
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
    chk("写进去能读回", get("/api/tune")[1] == t)

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
    code, j = post("/api/start")
    chk("start 能看到 backend_alive", j.get("backend_alive") is True)

    print("== 7. 没实现的接口要明说 ==")
    code, j = get("/api/scope")
    chk("scope ok=false 且有 msg", j.get("ok") is False and j.get("msg"))
    code, j = post("/api/hearing/tone", {"hz": 1000})
    chk("hearing/tone 明确拒绝", j.get("ok") is False and j.get("msg"))
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
