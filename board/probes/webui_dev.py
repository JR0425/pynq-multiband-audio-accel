#!/usr/bin/env python3
"""webui_dev.py —— 在本机（没有板子）跑起整套 PL 档网页后端 + 假数据。

为什么要有它：页面要显示电平/ENV/GR/健康这些活数，没板子的时候这些全是
"—"，页面根本调不出来；但接真板子又要求板子在线、位流在跑，改一行 CSS
就得等一圈。这个脚本用**和板上完全一样的后端**（fir_pl_webui.py）+ 一个
**每 200 ms 重写一次的假 status 文件**，让页面以为自己接在板子上。

它不碰真板子、不加载 overlay，全程只读写临时目录里的三个文件。

用法：
    python board/probes/webui_dev.py                 # 默认 8090，自动打开浏览器
    python board/probes/webui_dev.py --port 8091
    python board/probes/webui_dev.py --no-open

页面地址就是 http://127.0.0.1:8090/ 。改完 pl.html 按 Ctrl+F5 刷新即可
（后端每次都从磁盘读 HTML，不用重启）。

注意：这里喂的是**假数据**，只用来调版面和交互。判断"参数真的进了数据通路"
必须回板上跑 board/probes/pl_regs_check.py + pl_drc_ply_probe.py。
"""
import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS = os.path.join(ROOT, "board", "scripts")
WEBUI = os.path.join(SCRIPTS, "fir_pl_webui.py")


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


class FakeBoard(threading.Thread):
    """每 200 ms 刷一次 status，数值像真人对着话筒说话。"""

    def __init__(self, status_path, tune_path, run_path, scope_path=None, t0=None):
        super().__init__(daemon=True)
        self.status_path = status_path
        self.tune_path = tune_path
        self.run_path = run_path
        self.scope_path = scope_path
        self._scope_n = 0
        self.t0 = t0 or time.time()
        self.stop_flag = threading.Event()
        self.err = None
        self._phase = 0.0

    def run(self):
        while not self.stop_flag.is_set():
            try:
                self._tick()
            except Exception as ex:              # 别让假数据线程把服务带崩
                self.err = "%s: %s" % (type(ex).__name__, ex)
            self.stop_flag.wait(0.2)

    def _write_scope(self, run, gain_db):
        """假抓取缓冲：4096 点 @ 48.8 kHz，三根正弦 + 一点噪声。

        为什么要喂这个：页面的波形/频谱是独立于电平条的另一套绘制代码，
        没有数据时那两块永远是占位文字，改坏了也看不出来。
        真板上对应 fir_pl_live.read_scope() 写出来的同一份 json。
        """
        if not self.scope_path:
            return
        fs = 48828.0
        n = 4096
        g = 10 ** ((sum(gain_db) / 4.0) / 20.0)
        ins, outs = [], []
        for i in range(n):
            x = 0.0
            if run:
                for f, amp in ((250.0, 0.10), (750.0, 0.08), (1500.0, 0.06)):
                    x += amp * math.sin(2 * math.pi * f * i / fs)
                x += 0.004 * (random.random() - 0.5)
            v = int(max(-1.0, min(1.0, x)) * 32767)
            ins.append(v)
            outs.append(int(max(-32768, min(32767, v * g))))
        _write_json(self.scope_path, {
            "ok": True, "t": time.time(), "fs": fs, "n": n,
            "in": ins, "out": outs,
        })

    def _run_on(self):
        try:
            with open(self.run_path) as f:
                return f.read().strip() != "0"
        except Exception:
            return False

    def _tune(self):
        try:
            with open(self.tune_path) as f:
                return json.load(f)
        except Exception:
            return {"gain_db": [6.0] * 4, "bypass": False}

    def _tick(self):
        now = time.time()
        t = now - self.t0
        run = self._run_on()
        tune = self._tune()
        gains = tune.get("gain_db") or [0.0] * 4

        # 说话：每 ~3.2 s 一个音节包络，带随机起伏
        self._phase += 0.2
        syllable = max(0.0, math.sin(self._phase * 1.9)) ** 1.5
        jitter = 1.0 + 0.12 * random.random()
        level = (0.06 + 0.55 * syllable) * jitter if run else 0.0

        peak_in = int(min(32767, level * 32768))
        peak_out = int(min(32767, peak_in * (10 ** (sum(gains) / 4.0 / 20.0))))

        env, gr, env_r, gr_r = [], [], [], []
        for i in range(4):
            band = level * (1.0 - 0.12 * i)
            env.append(round(band, 5))
            # 压缩量：过阈值才压，这里给个形状像话的近似
            over = max(0.0, 20 * math.log10(band + 1e-9) + 20.0)
            gr.append(round(min(0.7, over / 40.0), 5))
            env_r.append(round(band * 1.05, 5))
            gr_r.append(round(min(0.7, over / 45.0), 5))

        def db(v, full=1.0):
            return None if v <= 0 else round(20 * math.log10(min(1.0, v / full)), 1)

        _write_json(self.status_path, {
            "t": now, "n": 0, "pid": os.getpid(), "running": run, "ok": True,
            "mode": "pl",
            "gain_db": [round(float(g), 2) for g in gains],
            "gain_applied_db": [round(float(g), 2) for g in gains],
            "ramp_busy": 0,
            "bypass": bool(tune.get("bypass")),
            "attack_ms": [5.0] * 4, "release_ms": [100.0] * 4,
            "peak_in": peak_in, "peak_out": peak_out,
            "peak_in_db": db(peak_in / 32768.0), "peak_out_db": db(peak_out / 32768.0),
            "env": env, "env_db": [db(v) for v in env],
            "gr": gr, "gr_db": [None if v <= 1e-6 else round(20 * math.log10(v), 1) for v in gr],
            "env_r": env_r, "env_r_db": [db(v) for v in env_r],
            "gr_r": gr_r, "gr_r_db": [None if v <= 1e-6 else round(20 * math.log10(v), 1) for v in gr_r],
            "dual": any(abs(env_r[i] - env[i]) > 1e-4 for i in range(4)),
            "in_rms": 0.0, "out_rms": 0.0, "rt": 1.0, "lost_blocks": 0,
            "i2s_stat0": 1, "i2s_stat1": 0,
            "l": 0, "nbuf": 0, "lat_ms": 2.3, "io_ms": 0.0, "dsp_ms": 0.0,
            "uptime_s": round(t, 2), "error": None,
        })

        self._scope_n += 1
        if self._scope_n % 5 == 0:                 # 200 ms × 5 = 1 Hz，和板上一致
            self._write_scope(run, gains)


def start(port=8090, keep=False):
    """起后端 + 假数据，返回 (base_url, stop_fn, tmpdir)。"""
    tmpdir = tempfile.mkdtemp(prefix="plwebui_dev_")
    tune = os.path.join(tmpdir, "fir_tune.json")
    status = os.path.join(tmpdir, "fir_status.json")
    run = os.path.join(tmpdir, "fir_pl_run.txt")
    scope = os.path.join(tmpdir, "fir_scope.json")
    bit = os.path.join(tmpdir, "fir_pl.bit")

    with open(bit, "wb") as f:                    # 假的位流文件，只为 health 报告 exists
        f.write(b"\x00" * 4096)
    with open(run, "w") as f:
        f.write("1")                              # 默认通路开着，不然页面一片空
    _write_json(tune, {"gain_db": [6.0] * 4, "thr_db": [-20.0] * 4,
                       "ratio": [0.7] * 4, "attack_ms": [5.0] * 4,
                       "release_ms": [100.0] * 4, "bypass": False})

    env = dict(os.environ,
               FIR_TUNE=tune, FIR_STATUS=status,
               FIR_PL_RUN=run, FIR_PL_BIT=bit, FIR_PL_SCOPE=scope,
               PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, WEBUI, "--port", str(port)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    fake = FakeBoard(status, tune, run, scope)
    fake.start()
    time.sleep(1.2)                               # 等 http 起来

    base = "http://127.0.0.1:%d/" % port

    def stop():
        fake.stop_flag.set()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if not keep:
            shutil.rmtree(tmpdir, ignore_errors=True)

    return base, stop, tmpdir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(WEBUI):
        print("找不到 %s" % WEBUI)
        return 2

    base, stop, tmpdir = start(a.port)
    print("PL 档网页（假数据）: %s" % base)
    print("临时文件: %s" % tmpdir)
    print("改 board/scripts/pl.html 后 Ctrl+F5 刷新；Ctrl-C 结束。")
    if not a.no_open:
        try:
            webbrowser.open(base)
        except Exception:
            pass
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
