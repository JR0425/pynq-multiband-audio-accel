#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fir_webui.py —— 浏览器调参页（在板子上跑，PC/手机浏览器打开）

    sh run_webui.sh                          # 板子上（会自己 sudo）
    浏览器打开 http://192.168.2.99:8080/

它只做三件事：

  1. 把 /dev/shm/fir_status.json 和 /dev/shm/fir_scope.bin 转成 JSON 给页面；
  2. 把页面的旋钮写进 /dev/shm/fir_tune.json（复用 fir_tune.write_tune）；
  3. 起停 fir_live.py（沿用 w5_tuning_ui.ipynb 里那套死机防护前置检查）。

为什么页面跑在板子上，而不是 PC 上跑一个小程序
------------------------------------------------
/dev/shm 和那条实时进程都只在板子上。PC 要拿数据就得挂网络文件系统或者
另起一条 socket 链路 —— 多一层就多一个演示时会坏的地方。板子这边只用
标准库 http.server：PC 打开的是一个静态页面 + 四个 JSON 接口，关掉浏览器
不影响音频，这条和 notebook 那一版完全一样。

**音频那条路一个字节都不碰。** 页面只做两件事：读状态文件、写旋钮文件。
写旋钮是原子替换；读状态本来就是"读不到就当没有"。

界面外观与布局参考了 AyQWERTY/hearing-test（MIT）：深色玻璃卡片、顶部
曲线、底部控件、纯 HTML/CSS/JS 无构建。原项目是听力测试页（Chart.js +
测试流程），这里把 Chart.js 和测试流程去掉，换成自己的 canvas 画法
（四段响应曲线 / 时域 / 频谱），控件换成我们的四段增益 + WDRC。

⚠️ 这个进程要 root（起 fir_live.py 需要 /dev/mem、SCHED_FIFO、音频设备）。
   只在局域网里用，别把 8080 端口映射到公网。
"""

import argparse
import json
import os
import re
import signal
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from fir_tune import read_tune, write_tune, read_status, clear_status, TUNE_PATH

try:                                    # 频谱要 numpy；没有就只画时域
    import numpy as np
except Exception:                       # noqa: BLE001
    np = None

SCOPE_PATH = os.environ.get("FIR_SCOPE", "/dev/shm/fir_scope.bin")
SCOPE_N    = int(os.environ.get("LIVE_SCOPE_N", "960"))
SCOPE_MAGIC = 0x504F4353                # b"SCOP" 小端，和 fir_live.py 一致
LIVE_LOG   = "/dev/shm/fir_webui_live.log"
LAST_PATH  = "/dev/shm/fir_webui_last.json"
FS         = 48000.0
GD         = 96                          # 核的群延迟，画图时先补掉

LOCK    = threading.Lock()
PROC    = {"p": None, "log": None, "stopping": False, "starting": False,
           "stop_msg": "", "started_at": 0.0, "last": None}
SEQ     = [0]


def _load_last():
    """上一次跑完的自检结论。存在 /dev/shm，重启页面服务不丢，重启板子会丢。"""
    try:
        with open(LAST_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(d, dict) and isinstance(d.get("ok"), bool):
        return {"ok": d["ok"], "t": float(d.get("t") or 0)}
    return None


def _save_last(ok, t=None):
    d = {"ok": bool(ok), "t": float(t or time.time())}
    with LOCK:
        PROC["last"] = d
    try:
        tmp = LAST_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, LAST_PATH)
    except OSError:
        pass


def _clear_last():
    """新的一趟起跑就作废旧结论 —— 否则中途被杀会留着上一趟的“自检通过”。"""
    with LOCK:
        PROC["last"] = None
    try:
        os.unlink(LAST_PATH)
    except OSError:
        pass


def _remember(st):
    """状态文件里出现 running=false + ok 时，记下这次的自检结论。"""
    if not st or st.get("running") is not False or st.get("ok") is None:
        return
    t = float(st.get("t") or 0)
    with LOCK:
        last = PROC["last"]
    if last is not None and t <= float(last.get("t") or 0):
        return
    _save_last(st["ok"], t)


def _selfcheck_result(st, pid, since=0.0):
    """这一趟自己的收尾结论。

    状态文件可能是上一趟留下的 —— 尤其在这一趟刚起、还没跑到写状态时就被
    停掉的情况下。pid 对不上（或时间早于起跑）就当没有结论，不能把上一趟的
    “自检通过”安到这一趟头上。
    """
    if not st or st.get("running") is not False or st.get("ok") is None:
        return None
    try:
        if int(st.get("pid") or 0) != int(pid):
            return None
        if float(st.get("t") or 0) < float(since or 0):
            return None
    except (TypeError, ValueError):
        return None
    return st.get("ok")


PROC["last"] = _load_last()


PAGE_FILE = os.path.join(HERE, "page.html")


def page_html():
    """换界面只换 page.html；没有这个文件就用内嵌的那一版兜底。

    每次请求都重读（十几 KB，代价可以忽略），这样改完页面刷新浏览器就生效，
    不用重启服务。
    """
    try:
        with open(PAGE_FILE, "rb") as f:
            return f.read()
    except OSError:
        return PAGE


# ---------------------------------------------------------------------------
# 状态 / 波形
# ---------------------------------------------------------------------------

def _read_stable(path, tries=2):
    """连读两次，一样才算数。

    /dev/shm/fir_scope.bin 是"同一个 fd 覆盖写"的定长记录，读的一边理论上
    会撞见写了一半的内容。长度检查挡不住这种情况（长度一直都对），所以再
    加一道：两次内容一致才返回。3.8 KB 的读，多读一次无所谓。
    """
    last = None
    for _ in range(tries):
        try:
            with open(path, "rb") as f:
                cur = f.read()
        except OSError:
            return None
        if last is not None and cur == last:
            return cur
        last = cur
    return last


def _log_tail(n=2000):
    try:
        with open(LIVE_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            sz = f.tell()
            f.seek(max(0, sz - n))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _state():
    st = read_status()
    _remember(st)
    with LOCK:
        p = PROC["p"]
        run = p is not None and p.poll() is None
        pid = p.pid if p is not None else None
        stopping = PROC["stopping"]
        starting = PROC["starting"]
        stop_msg = PROC["stop_msg"]
        started = PROC["started_at"]
        last = PROC["last"]
    age = None
    try:
        if st and st.get("t"):
            age = round(time.time() - float(st["t"]), 2)
    except (TypeError, ValueError):
        pass
    return {"status": st,
            "proc": {"running": run, "pid": pid, "starting": starting,
                     "stopping": stopping, "stop_msg": stop_msg,
                     "started_at": started,
                     "log": "" if run else _log_tail()},
            "last": last,
            "age": age}


def _scope_payload():
    raw = _read_stable(SCOPE_PATH)
    if raw is None:
        return {"ok": False,
                "msg": "读不到 %s —— 实时那条没在跑，或者起的时候没开 LIVE_SCOPE"
                       % SCOPE_PATH}
    want = 16 + 4 * SCOPE_N
    if len(raw) != want:
        return {"ok": False, "msg": "%s 长度 %d，期望 %d" % (SCOPE_PATH, len(raw), want)}
    magic, seqno, n, t_ms = struct.unpack("<4i", raw[:16])
    if magic != SCOPE_MAGIC or n != SCOPE_N:
        return {"ok": False, "msg": "波形文件头不认识（魔数 0x%08x，N=%d）" % (magic, n)}
    if np is None:
        return {"ok": False, "msg": "板端没有 numpy，画不了波形/频谱"}

    a = np.frombuffer(raw[16:16 + 2 * n], dtype="<i2")
    b = np.frombuffer(raw[16 + 2 * n:], dtype="<i2")
    # 先补回群延迟，不然两条波形横着错开 2 ms，看着像"声音变了"
    a = a[:a.size - GD].astype(np.float64)
    b = b[GD:].astype(np.float64)
    m = a.size
    step = max(1, m // 120)
    idx = np.arange(0, m, step)[:120]

    win = np.hanning(m)
    freqs = np.fft.rfftfreq(m, 1.0 / FS)
    keep = freqs <= 4000.0

    def spec(v):
        mag = np.abs(np.fft.rfft(v * win)) / (m / 4.0) / 32768.0
        return np.clip(20.0 * np.log10(np.maximum(mag[keep], 1e-9)), -100.0, 0.0)

    da, db = spec(a), spec(b)
    fs = freqs[keep]
    k = max(1, fs.size // 160)
    return {"ok": True, "seq": seqno, "t_ms": t_ms,
            "wave": {"a": a[idx].round().astype(int).tolist(),
                     "b": b[idx].round().astype(int).tolist()},
            "spec": {"f": fs[::k].round().astype(int).tolist(),
                     "a": np.round(da[::k], 2).tolist(),
                     "b": np.round(db[::k], 2).tolist()}}


# ---------------------------------------------------------------------------
# 写旋钮
# ---------------------------------------------------------------------------

def _clampf(x, lo, hi, dflt):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return dflt
    if v != v:                          # NaN
        return dflt
    return max(lo, min(hi, v))


def _four(v, lo, hi, dflt):
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        v = [dflt] * 4
    return [round(_clampf(x, lo, hi, dflt), 2) for x in v]


def _apply_tune(d):
    """只截断，不替用户改语义 —— 页面和 notebook 写的是同一套字段。

    gain 的 ±24 是**界面**上限（核能到 ±42），和 w5_tuning_ui.ipynb 一致。
    ratio 字段是"压掉多少"（1 - 1/压缩比），不是压缩比本身。
    """
    if not isinstance(d, dict):
        return False, "body 不是对象"
    g   = _four(d.get("gain_db"), -24.0, 24.0, 0.0)
    thr = _four(d.get("thr_db"), -60.0, 0.0, -10.0)
    rat = _four(d.get("ratio"), 0.0, 0.95, 0.0)
    atk = _four(d.get("attack_ms"), 0.0, 200.0, 0.0)
    # 680 是核里 Q1.15 平滑步进真正能到的最慢值（step=1 → 682.66 ms）。
    # 界面给到 1000 的话，拖过 680 之后声音就不再变，等于界面骗人。
    rel = _four(d.get("release_ms"), 0.0, 680.0, 0.0)
    with LOCK:
        SEQ[0] += 1
        seqno = SEQ[0]
        try:
            # 两个浏览器标签同时拖滑杆时，write_tune 里固定的 .tmp 会互相踩；
            # 加一把锁，代价是一毫秒。
            write_tune({"seq": seqno, "gain_db": g, "thr_db": thr, "ratio": rat,
                        "attack_ms": atk, "release_ms": rel,
                        "bypass": 1 if d.get("bypass") else 0})
        except OSError as e:
            return False, "写 %s 失败：%s" % (TUNE_PATH, e)
    return True, "ok"

# ---------------------------------------------------------------------------
# 起停实时那条
# ---------------------------------------------------------------------------

def _hang_fix_state():
    """和 w5_tuning_ui.ipynb 里那段一样：搬样线程 SCHED_FIFO 且永不阻塞，
    CPU0 上的 ksoftirqd / kworker 若是普通 CFS 会被饿死，约 60 秒后
    RCU stall → panic。判据用内核里的实际调度策略，发现一个提一个。"""
    def _ask(pid):
        q = subprocess.run(["chrt", "-p", str(pid)], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True)
        m = re.search(r"scheduling priority:\s*(\d+)", q.stdout)
        return q.stdout, (int(m.group(1)) if m else None)

    def _fifo(pid):
        txt, prio = _ask(pid)
        return ("SCHED_FIFO" in txt and prio is not None and prio > 20), txt

    def _promote(pid):
        q = subprocess.run(["chrt", "-f", "-p", "50", str(pid)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True)
        return q.returncode == 0, " ".join(q.stdout.split())[:80]

    notes = []
    promoted = 0
    for cpu in ("0", "1"):
        r = subprocess.run(["pgrep", "-f", "^ksoftirqd/%s$" % cpu],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True)
        pids = r.stdout.split()
        if not pids:
            return False, "找不到 ksoftirqd/%s" % cpu
        ok, txt = _fifo(pids[0])
        if not ok:
            ok2, why = _promote(pids[0])
            if not ok2:
                return False, "ksoftirqd/%s 提权失败：%s" % (cpu, why)
            promoted += 1
        notes.append("ksoftirqd/%s FIFO 50" % cpu)

    r = subprocess.run(["pgrep", "-f", "^kworker/[01]:"],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       universal_newlines=True)
    n_ok = 0
    for pid in r.stdout.split():
        ok, txt = _fifo(pid)
        if "No such process" in txt:
            continue
        if not ok:
            ok2, why = _promote(pid)
            if not ok2:
                if "No such process" in why:
                    continue
                return False, "kworker pid %s 提权失败：%s" % (pid, why)
            promoted += 1
        n_ok += 1
    notes.append("kworker %d 个 FIFO 50%s" % (
        n_ok, "（现场提权 %d）" % promoted if promoted else ""))
    return True, " / ".join(notes)


def _start(src):
    with LOCK:
        p = PROC["p"]
        if p is not None and p.poll() is None:
            return False, "已经在跑了（pid %d）" % p.pid, ""
        if PROC["stopping"]:
            return False, "上一条还在收尾自检，等它停下来再起", ""
        if PROC["starting"]:
            return False, "正在起，等这条起来再操作", ""
        PROC["starting"] = True

    try:
        # 也可能是 run_live.sh / notebook 起的：fir_live.py 自己有文件锁，
        # 这里先看一眼状态文件，省得用户对着一句"起跑就退了"猜。
        st = read_status()
        if st and st.get("running"):
            try:
                age = time.time() - float(st.get("t") or 0)
            except (TypeError, ValueError):
                age = 99.0
            if age < 1.5:
                return False, "已经有一条实时在跑（状态文件 %.1f 秒前还在更新），先把它停掉" % age, ""

        ok, why = _hang_fix_state()
        if not ok:
            return (False,
                    "死机防护没生效（%s）。先在板子上跑一次\n"
                    "  sudo sh %s/install_ksoftirqd_rt.sh\n"
                    "然后重启板子（这个服务开机时生效）。" % (why, HERE), "")

        t = read_tune() or {}
        gains = t.get("gain_db") or [0, 0, 0, 0]
        try:
            gtxt = ",".join("%.2f" % float(x) for x in gains)
        except (TypeError, ValueError):
            gtxt = "0,0,0,0"

        env = dict(os.environ)
        env.update({"XILINX_XRT": "/usr", "PYTHONUNBUFFERED": "1",
                    "LIVE_SRC": src, "LIVE_SECONDS": "0",
                    "LIVE_GAIN_DB": gtxt, "LIVE_SCOPE": "1"})
        try:
            lg = open(LIVE_LOG, "wb")
        except OSError as e:
            return False, "打不开日志 %s：%s" % (LIVE_LOG, e), ""

        with LOCK:
            if PROC["log"] is not None:
                try:
                    PROC["log"].close()
                except OSError:
                    pass
            PROC["log"] = lg
            PROC["stop_msg"] = ""
            try:
                # 经一层 sh 把 SIGINT 恢复成默认再 exec：这个页面自己是后台起的
                # （nohup/setsid），SIGINT 被继承成“忽略”，直接 Popen 的话
                # fir_live 在 interpreter 起来之前收到停止信号会被丢掉。
                PROC["p"] = subprocess.Popen(
                    ["/bin/sh", "-c", 'trap - INT; exec "$0" "$@"',
                     sys.executable, os.path.join(HERE, "fir_live.py")],
                    cwd=HERE, env=env, stdout=lg, stderr=subprocess.STDOUT,
                    start_new_session=True)
            except OSError as e:
                return False, "起不来：%s" % e, ""
            PROC["started_at"] = time.time()
            p = PROC["p"]

        # 新的一趟开始了，上一趟的自检结论作废
        _clear_last()
        # 状态文件也要立刻删：fir_live 自己会删，但它要几秒才走到那一步；
        # 这期间若被停掉，旧状态会被误当成本趟的自检结论。
        clear_status()

        # 起跑检查：最常见的两种失败（已经有一条在跑、加载 overlay 失败）
        # 都发生在一两秒内，它自己会退出，原因写在日志里。
        time.sleep(2.0)
        if p.poll() is not None:
            return False, "它起跑就退了（退出码 %s）" % p.returncode, _log_tail(1600)
        return True, "起来了，pid %d，音源 %s" % (p.pid, src), ""
    finally:
        with LOCK:
            PROC["starting"] = False


def _stop_wait(p):
    try:
        # SIGINT 走 KeyboardInterrupt 那条路，收尾会把音频通路正常关掉；
        # SIGTERM 是直接打死，耳机里会留一声。
        p.send_signal(signal.SIGINT)
        p.wait(timeout=60)
        msg = "停了（pid %d）" % p.pid
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        msg = "60 秒还没收完，强杀了 —— 报告会缺最后一段"
    except Exception as e:               # noqa: BLE001
        msg = "停的时候出错：%s" % e
    st = read_status()
    _remember(st)
    ok = _selfcheck_result(st, p.pid, PROC["started_at"])
    if ok is True:
        msg += "，自检通过"
    elif ok is False:
        msg += "，自检没过"
    with LOCK:
        PROC["stopping"] = False
        PROC["stop_msg"] = msg


def _pid_alive(pid):
    """僵尸进程不算活着 —— 没被 poll() 收掉的已退进程还挂在 /proc 里，
    拿它当"在跑"会让停止按钮白等 60 秒。"""
    try:
        with open("/proc/%d/stat" % pid) as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except OSError:
        return False


def _is_fir_live(pid):
    """只认真正的 fir_live 进程，不认命令行里恰好带这个文件名的 shell。

    `pgrep -f fir_live.py` 会连 `bash -c "... fir_live.py"`、编辑器、grep 一起
    返回；给它们发 SIGINT 就是误杀别人的终端。
    """
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            raw = f.read()
    except OSError:
        return False
    args = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
    if not args:
        return False
    head = os.path.basename(args[0])
    if head == "fir_live.py":
        return True
    if "python" not in head:
        return False
    return any(a.endswith("fir_live.py") for a in args[1:])


def _find_fir_live_pids():
    """板上所有 fir_live.py 进程，本进程（以及本进程起的那个）除外。"""
    try:
        r = subprocess.run(["pgrep", "-f", r"fir_live\.py"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True)
    except OSError:
        return []
    me = os.getpid()
    pids = []
    for s in r.stdout.split():
        try:
            pid = int(s)
        except ValueError:
            continue
        if pid != me and _pid_alive(pid) and _is_fir_live(pid):
            pids.append(pid)
    return pids


def _fresh(st, sec=1.5):
    try:
        return st is not None and (time.time() - float(st.get("t") or 0)) < sec
    except (TypeError, ValueError):
        return False


def _wait_external(pids):
    deadline = time.time() + 60
    while time.time() < deadline and any(_pid_alive(p) for p in pids):
        time.sleep(0.5)
    left = [p for p in pids if _pid_alive(p)]
    st = read_status()
    _remember(st)
    if left:
        msg = "60 秒还没退：%s" % ", ".join(str(x) for x in left)
    else:
        msg = "停了（pid %s，不是本页起的）" % ", ".join(str(x) for x in pids)
        res = [_selfcheck_result(st, x) for x in pids]
        if True in res:
            msg += "，自检通过"
        elif False in res:
            msg += "，自检没过"
    with LOCK:
        PROC["stopping"] = False
        PROC["stop_msg"] = msg


def _stop_async():
    with LOCK:
        p = PROC["p"]
        if PROC["stopping"]:
            return False, "已经在停了"
        mine = p is not None and p.poll() is None
        if PROC["starting"] and not mine:
            return False, "还在起，等它起来再停"
        if mine:
            PROC["stopping"] = True
            PROC["stop_msg"] = "已发停止信号，等它做完逐位自检（十几秒）"
    if mine:
        threading.Thread(target=_stop_wait, args=(p,), daemon=True).start()
        return True, "已发停止信号，等它做完逐位自检（十几秒）"

    # 不是本页起的（run_live.sh / notebook）：状态文件是同一份，停止也得管用。
    # 优先用状态文件里 fir_live 自己写的 pid —— 进程改名或跑在 Jupyter 内核里时，
    # pgrep 找不到，但 pid 仍然是对的。
    st = read_status()
    pids = []
    if _fresh(st) and st.get("running"):
        try:
            spid = int(st.get("pid") or 0)
        except (TypeError, ValueError):
            spid = 0
        if spid and spid != os.getpid() and _pid_alive(spid):
            pids = [spid]
    if not pids:
        pids = _find_fir_live_pids()

    if not pids:
        if _fresh(st) and st.get("running"):
            return False, ("状态文件说还在跑，但板上找不到可停的 fir_live 进程"
                           "（可能是 notebook 里 %run 的）。请到启动它的那个内核里停。")
        return True, "它已经停了"

    killed = []
    errors = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGINT)
            killed.append(pid)
        except ProcessLookupError:
            pass                        # 查完到发信号之间刚好退了
        except OSError as e:
            errors.append("pid %d：%s" % (pid, e))
    if not killed:
        if errors:
            return False, "给实时进程发信号失败：" + "；".join(errors)
        return True, "它已经停了"
    with LOCK:
        PROC["stopping"] = True
        PROC["stop_msg"] = "已给板子上已有的实时进程发停止信号（pid %s）" % \
                           ", ".join(str(x) for x in killed)
    threading.Thread(target=_wait_external, args=(killed,), daemon=True).start()
    return True, "已发停止信号，等它做完逐位自检（十几秒）"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            self._send(200, page_html(), "text/html; charset=utf-8")
        elif p == "/api/status":
            self._json(_state())
        elif p == "/api/tune":
            self._json({"ok": True, "tune": read_tune()})
        elif p == "/api/scope":
            self._json(_scope_payload())
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        p = self.path.split("?")[0]
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > 65536:
            self._json({"ok": False, "msg": "body 太大"}, 413)
            return
        raw = self.rfile.read(n) if n > 0 else b"{}"
        try:
            d = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            self._json({"ok": False, "msg": "JSON 解析失败"}, 400)
            return
        if not isinstance(d, dict):
            self._json({"ok": False, "msg": "body 要是对象"}, 400)
            return

        if p == "/api/tune":
            ok, msg = _apply_tune(d)
            self._json({"ok": ok, "msg": msg})
        elif p == "/api/start":
            src = str(d.get("src") or "mic")
            if src not in ("mic", "wav"):
                src = "mic"
            ok, msg, tail = _start(src)
            self._json({"ok": ok, "msg": msg, "log": tail})
        elif p == "/api/stop":
            ok, msg = _stop_async()
            self._json({"ok": ok, "msg": msg})
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _pin_cpu1():
    """搬样线程占满 CPU0，所以服务钉到 CPU1（和 notebook 的做法一致）。

    os.sched_setaffinity(0, …) 只钉当前线程，这里把 /proc/self/task 下所有
    线程一起钉；之后 HTTP 线程从主线程派生，会继承 CPU1。
    """
    n = 0
    try:
        tids = os.listdir("/proc/self/task")
    except OSError:                     # 不在 Linux 上（本地改页面时的冒烟测试）
        return 0
    for tid in tids:
        try:
            os.sched_setaffinity(int(tid), {1})
            n += 1
        except OSError:
            pass
    return n


def _board_ips():
    try:
        out = subprocess.check_output(["hostname", "-I"], universal_newlines=True)
        return [x for x in out.split() if x and not x.startswith("127.")]
    except Exception:                    # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>四段听力补偿 · 调参台</title>
<style>
:root{
  --fg:#e9eeff; --dim:#93a0c4; --line:rgba(255,255,255,.14);
  --card:rgba(255,255,255,.062); --card2:rgba(255,255,255,.10);
  --acc:#2ee6a8; --blue:#6aa9ff; --warn:#ffb454; --bad:#ff5d6c;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{min-height:100%}
body{
  font-family:"Segoe UI",system-ui,-apple-system,"Microsoft YaHei",sans-serif;
  color:var(--fg); font-size:14px;
  background:
    radial-gradient(1100px 720px at 12% -12%, #24386f 0%, transparent 60%),
    radial-gradient(900px 640px at 108% 112%, #43285f 0%, transparent 55%),
    linear-gradient(160deg,#0a1024,#0c1533 52%,#161033);
  background-attachment:fixed;
  padding:10px;
}
.glass{
  background:var(--card); border:1px solid var(--line); border-radius:16px;
  backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  box-shadow:0 8px 26px rgba(0,0,0,.28);
}
header{
  display:flex; align-items:center; gap:14px; flex-wrap:wrap;
  padding:10px 14px; margin-bottom:10px;
}
.brand h1{font-size:17px;font-weight:650;letter-spacing:.5px}
.brand span{font-size:11px;color:var(--dim)}
.center{display:flex;align-items:center;gap:10px;flex:1;min-width:260px}
.pill{
  font-size:12px;padding:4px 11px;border-radius:999px;white-space:nowrap;
  border:1px solid var(--line);background:rgba(255,255,255,.06);
}
.pill.ok{color:#08251b;background:var(--acc);border-color:transparent;font-weight:600}
.pill.bad{color:#fff;background:var(--bad);border-color:transparent;font-weight:600}
.pill.warn{color:#2a1a00;background:var(--warn);border-color:transparent;font-weight:600}
.pill.idle{color:var(--dim)}
.msg{font-size:12px;color:var(--dim);flex:1;min-width:120px;line-height:1.35}
.actions{display:flex;align-items:center;gap:8px}
select,button{font:inherit;color:var(--fg);border-radius:9px;border:1px solid var(--line);
  background:var(--card2);padding:7px 13px;cursor:pointer;outline:none}
select{padding:7px 8px}
option{background:#101a33;color:var(--fg)}
button:hover{background:rgba(255,255,255,.16)}
button:disabled{opacity:.42;cursor:default}
button.primary{background:var(--acc);color:#062018;border-color:transparent;font-weight:650}
button.primary:hover{filter:brightness(1.08);background:var(--acc)}
button.warn{background:rgba(255,180,84,.16);color:var(--warn);border-color:rgba(255,180,84,.45)}
button.ghost{background:transparent;padding:5px 10px;font-size:12px;color:var(--blue);
  border-color:rgba(106,169,255,.4)}

main{
  display:grid; gap:10px;
  grid-template-columns:minmax(0,1.08fr) minmax(0,1fr);
  grid-template-areas:"curve gains" "meters scope" "drc drc";
}
.card{padding:11px 13px 12px;min-width:0}
.curve{grid-area:curve}
.gains{grid-area:gains}
.meters{grid-area:meters}
.scope{grid-area:scope}
.drc{grid-area:drc}
.head{display:flex;align-items:baseline;gap:10px;margin-bottom:7px}
.head h2{font-size:13px;font-weight:600;letter-spacing:.4px}
.hint{font-size:11px;color:var(--dim)}
.head .hint{margin-left:auto}
canvas{display:block;width:100%}
#curve{height:190px}
#wave{height:104px}
#spec{height:104px}

/* 增益行 */
#gains{display:flex;flex-direction:column;gap:3px}
.gainrow{display:grid;grid-template-columns:104px 1fr 52px 56px;align-items:center;gap:8px}
.gainrow .band{font-size:12px;color:var(--dim);white-space:nowrap}
.gainrow .val{font-family:"Cascadia Mono",Consolas,monospace;font-size:13px;text-align:right}
.gainrow .applied{font-family:Consolas,monospace;font-size:10px;color:var(--dim);text-align:right}

input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:20px;
  background:transparent;cursor:pointer}
input[type=range]::-webkit-slider-runnable-track{height:5px;border-radius:3px;
  background:linear-gradient(90deg,rgba(106,169,255,.35),rgba(255,255,255,.18),rgba(46,230,168,.35))}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:15px;height:15px;
  border-radius:50%;background:#fff;border:2px solid #0a1024;margin-top:-5px;
  box-shadow:0 0 10px rgba(46,230,168,.55)}
input[type=range]::-moz-range-track{height:5px;border-radius:3px;
  background:linear-gradient(90deg,rgba(106,169,255,.35),rgba(255,255,255,.18),rgba(46,230,168,.35))}
input[type=range]::-moz-range-thumb{width:14px;height:14px;border-radius:50%;background:#fff;
  border:2px solid #0a1024;box-shadow:0 0 10px rgba(46,230,168,.55)}

.presets{margin-top:9px;border-top:1px solid rgba(255,255,255,.08);padding-top:8px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.chip{font-size:11.5px;padding:5px 9px;border-radius:8px;background:rgba(255,255,255,.07);
  border:1px solid var(--line);cursor:pointer;color:var(--fg)}
.chip:hover{background:rgba(255,255,255,.16)}
.chip.on{background:rgba(46,230,168,.18);border-color:rgba(46,230,168,.55);color:var(--acc)}

/* 电平 */
.meterrow{display:grid;grid-template-columns:44px 1fr 74px;align-items:center;gap:8px;
  margin-bottom:6px;font-size:12px}
.meterrow span{color:var(--dim)}
.bar{height:9px;border-radius:5px;background:rgba(255,255,255,.09);overflow:hidden;position:relative}
.bar i{display:block;height:100%;width:0%;background:var(--acc);border-radius:5px;
  transition:width .08s linear}
.bar i.blue{background:var(--blue)}
.bar i.warn{background:var(--warn)}
.bar i.bad{background:var(--bad)}
.meterrow b{font-family:Consolas,monospace;font-size:12px;font-weight:500;text-align:right}
.stats{margin-top:9px;font-size:11px;color:var(--dim);line-height:1.6;
  font-family:Consolas,monospace}

/* 波形 */
.scope canvas{margin-bottom:4px}

/* 压缩 */
details summary{cursor:pointer;font-size:12.5px;color:var(--dim);list-style:none}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"▸ ";color:var(--blue)}
details[open] summary::before{content:"▾ "}
.drcgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 18px;margin-top:9px}
.drcgrid label{display:grid;grid-template-columns:64px 1fr 84px;align-items:center;gap:8px;
  font-size:12px;color:var(--dim)}
.drcgrid label b{font-family:Consolas,monospace;font-size:11.5px;font-weight:500;
  color:var(--fg);text-align:right}
.drcgrid .chk{grid-template-columns:auto 1fr;justify-content:start}
.drcgrid .chk input{width:15px;height:15px;accent-color:#2ee6a8}
.drcgrid .chips{grid-column:1/-1;margin-top:0}

#logbox{margin-top:10px;padding:9px 12px;font-size:11px;line-height:1.5;
  color:#ffd9a8;background:rgba(255,180,84,.08);border:1px solid rgba(255,180,84,.28);
  border-radius:12px;max-height:150px;overflow:auto;white-space:pre-wrap;
  font-family:Consolas,monospace}
#logbox[hidden]{display:none}

@media (max-width:980px){
  main{grid-template-columns:minmax(0,1fr);
       grid-template-areas:"curve" "gains" "meters" "scope" "drc"}
  .drcgrid{grid-template-columns:minmax(0,1fr)}
}
</style>
</head>
<body>

<header class="glass">
  <div class="brand">
    <h1>四段听力补偿</h1>
    <span>PYNQ-Z2 · 实时音频通路</span>
  </div>
  <div class="center">
    <span id="pill" class="pill idle">未启动</span>
    <span id="msg" class="msg"></span>
  </div>
  <div class="actions">
    <select id="src" title="音源">
      <option value="mic">麦克风</option>
      <option value="wav">放 wav</option>
    </select>
    <button id="btnStart" class="primary">启动实时</button>
    <button id="btnStop" class="warn">停止</button>
  </div>
</header>

<main>
  <section class="glass card curve">
    <div class="head"><h2>四段增益曲线</h2><span class="hint">横轴对数频率，虚线是分段点</span></div>
    <canvas id="curve"></canvas>
  </section>

  <section class="glass card gains">
    <div class="head"><h2>每段增益</h2><button id="btnAB" class="ghost">A/B 对照</button></div>
    <div id="gainRows"></div>
    <div class="presets">
      <span class="hint">听力图预设</span>
      <div id="presets" class="chips"></div>
    </div>
  </section>

  <section class="glass card meters">
    <div class="head"><h2>电平与压缩</h2><span id="age" class="hint"></span></div>
    <div class="meterrow"><span>进核</span><div class="bar"><i id="barIn"></i></div><b id="dbIn">—</b></div>
    <div class="meterrow"><span>出核</span><div class="bar"><i id="barOut"></i></div><b id="dbOut">—</b></div>
    <div class="meterrow"><span>出/进</span><div class="bar"><i id="barComp"></i></div><b id="dbComp">—</b></div>
    <div class="stats" id="stats">还没跑过</div>
  </section>

  <section class="glass card scope">
    <div class="head"><h2>实时波形</h2><span class="hint">灰=进核　绿=出核　纵轴钉死 ±满量程</span></div>
    <canvas id="wave"></canvas>
    <div class="head"><h2>频谱</h2><span class="hint">0～4 kHz，虚线 500 / 1k / 2k</span></div>
    <canvas id="spec"></canvas>
  </section>

  <section class="glass card drc">
    <details>
      <summary>压缩门限 / 压缩比 / 起振释放（点开）</summary>
      <div class="drcgrid">
        <label>门限<input id="thr" type="range" min="-60" max="0" step="0.5" value="-10"><b id="thrV">-10.0 dBFS</b></label>
        <label>压缩比<input id="ratio" type="range" min="1" max="10" step="0.5" value="2"><b id="ratioV">2.0 : 1</b></label>
        <label>起振<input id="atk" type="range" min="0" max="200" step="1" value="0"><b id="atkV">0 ms</b></label>
        <label>释放<input id="rel" type="range" min="0" max="1000" step="5" value="0"><b id="relV">0 ms</b></label>
        <label class="chk"><input id="byp" type="checkbox">直通（跳过压缩）</label>
        <div class="chips" id="wdrcPresets"></div>
      </div>
    </details>
  </section>
</main>

<pre id="logbox" hidden></pre>

<script>
"use strict";
const $ = (id) => document.getElementById(id);
const BANDS = ["① 0–500 Hz", "② 500 Hz–1 kHz", "③ 1–2 kHz", "④ 2–24 kHz"];
const PRESETS = {
  "平坦：不动（0 dB）": [0, 0, 0, 0],
  "高频下降：典型老年性听力图": [-4, -2, 6, 14],
  "只抬辅音：清辅音都在 2 kHz 以上": [-10, -2, 6, 10],
  "压掉低频轰鸣": [-16, -6, 2, 4],
  "极端对照：一拖就听出区别": [-20, -10, 10, 20],
};
const WDRC_PRESETS = {
  "关（0 / 0）": [0, 0],
  "常用 5 / 100 ms": [5, 100],
  "慢 10 / 300 ms": [10, 300],
};

const S = { gain: [0, 0, 0, 0], thr: -10, ratio: 2, atk: 0, rel: 0, byp: false };
const GAIN_LIMIT = 24;

// ---------- 小工具 ----------
function fmtDB(x, nd) {
  if (x === null || x === undefined || !isFinite(x)) return "—";
  return (x >= 0 ? "+" : "") + x.toFixed(nd === undefined ? 1 : nd);
}
function dbOf(rms) {
  return (rms && rms > 0) ? 20 * Math.log10(rms / 32768) : -120;
}
function fitCanvas(c) {
  const r = c.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
  c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}
let msgTimer = null;
function note(text, bad) {
  const m = $("msg");
  m.textContent = text || "";
  m.style.color = bad ? "var(--bad)" : "var(--dim)";
  clearTimeout(msgTimer);
  if (text) msgTimer = setTimeout(() => { m.textContent = ""; }, 9000);
}
function setPill(text, cls) {
  const p = $("pill");
  p.textContent = text;
  p.className = "pill " + cls;
}

// ---------- 增益控件 ----------
const rows = [];
BANDS.forEach((name, i) => {
  const div = document.createElement("div");
  div.className = "gainrow";
  div.innerHTML =
    '<span class="band">' + name + '</span>' +
    '<input type="range" min="' + (-GAIN_LIMIT) + '" max="' + GAIN_LIMIT +
      '" step="0.5" value="0">' +
    '<span class="val">+0.0</span><span class="applied">—</span>';
  $("gainRows").appendChild(div);
  const inp = div.querySelector("input");
  const val = div.querySelector(".val");
  inp.addEventListener("input", () => {
    S.gain[i] = parseFloat(inp.value);
    paintGains();
    drawCurve();
    schedulePush();
  });
  rows.push({ inp, val, applied: div.querySelector(".applied") });
});

function paintGains() {
  rows.forEach((r, i) => {
    r.inp.value = S.gain[i];
    r.val.textContent = fmtDB(S.gain[i]);
    r.val.style.color = S.gain[i] > 0 ? "var(--acc)"
                        : (S.gain[i] < 0 ? "var(--blue)" : "var(--fg)");
  });
}

function setGains(vals, push) {
  S.gain = vals.slice();
  paintGains();
  drawCurve();
  if (push !== false) schedulePush();
}

Object.keys(PRESETS).forEach((name) => {
  const b = document.createElement("button");
  b.className = "chip";
  b.textContent = name;
  b.addEventListener("click", () => {
    document.querySelectorAll("#presets .chip").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    abSaved = null; $("btnAB").textContent = "A/B 对照";
    setGains(PRESETS[name]);
  });
  $("presets").appendChild(b);
});

let abSaved = null;
$("btnAB").addEventListener("click", () => {
  const flat = S.gain.every((v) => Math.abs(v) < 1e-9);
  if (!flat) {
    abSaved = S.gain.slice();
    setGains([0, 0, 0, 0]);
    $("btnAB").textContent = "A/B 还原";
    note("已切到 0 dB（另一只耳朵/对照），再点一次还原");
  } else if (abSaved) {
    setGains(abSaved);
    abSaved = null;
    $("btnAB").textContent = "A/B 对照";
    note("已还原到刚才那组增益");
  } else {
    note("先拖一组增益，再点 A/B 才有东西可还原", true);
  }
});

// ---------- 压缩控件 ----------
const drc = [
  ["thr", "thrV", (v) => v.toFixed(1) + " dBFS"],
  ["ratio", "ratioV", (v) => v.toFixed(1) + " : 1"],
  ["atk", "atkV", (v) => v.toFixed(0) + " ms"],
  ["rel", "relV", (v) => v.toFixed(0) + " ms"],
];
drc.forEach(([id, out, fmt]) => {
  const el = $(id);
  const upd = (e) => {
    S[id] = parseFloat(el.value);
    $(out).textContent = fmt(S[id]);
    if (e.type === "change") schedulePush();
  };
  el.addEventListener("input", upd);
  el.addEventListener("change", upd);
});
$("byp").addEventListener("change", () => { S.byp = $("byp").checked; schedulePush(); });

Object.keys(WDRC_PRESETS).forEach((name) => {
  const b = document.createElement("button");
  b.className = "chip";
  b.textContent = name;
  b.addEventListener("click", () => {
    const [a, r] = WDRC_PRESETS[name];
    S.atk = a; S.rel = r;
    $("atk").value = a; $("atkV").textContent = a + " ms";
    $("rel").value = r; $("relV").textContent = r + " ms";
    schedulePush();
  });
  $("wdrcPresets").appendChild(b);
});

// ---------- 写旋钮 ----------
let pushTimer = null, pushing = false, seq = 0;
function schedulePush() {
  clearTimeout(pushTimer);
  pushTimer = setTimeout(pushNow, 80);
}
async function pushNow() {
  if (pushing) { schedulePush(); return; }
  pushing = true;
  const g = S.gain.map((x) => Math.round(x * 100) / 100);
  const body = {
    seq: ++seq,
    gain_db: g,
    thr_db: [S.thr, S.thr, S.thr, S.thr],
    ratio: [0, 1, 2, 3].map(() => Math.max(0, 1 - 1 / Math.max(1, S.ratio))),
    attack_ms: [S.atk, S.atk, S.atk, S.atk],
    release_ms: [S.rel, S.rel, S.rel, S.rel],
    bypass: S.byp ? 1 : 0,
  };
  try {
    const r = await fetch("/api/tune", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!d.ok) note(d.msg || "写旋钮失败", true);
  } catch (e) {
    note("写旋钮失败：" + e, true);
  } finally {
    pushing = false;
  }
}

// ---------- 起停 ----------
$("btnStart").addEventListener("click", async () => {
  $("btnStart").disabled = true;
  setPill("启动中…", "warn");
  note("检查死机防护、起实时那条…（约 2 秒）");
  try {
    const r = await fetch("/api/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ src: $("src").value }),
    });
    const d = await r.json();
    note(d.msg, !d.ok);
    if (!d.ok && d.log) { $("logbox").textContent = d.log; $("logbox").hidden = false; }
    else if (d.ok) { $("logbox").hidden = true; }
  } catch (e) {
    note("启动失败：" + e, true);
  } finally {
    $("btnStart").disabled = false;
    pollState();
  }
});
$("btnStop").addEventListener("click", async () => {
  note("发停止信号…");
  try {
    const r = await fetch("/api/stop", { method: "POST" });
    const d = await r.json();
    note(d.msg, !d.ok);
  } catch (e) {
    note("停止失败：" + e, true);
  }
});

// ---------- 状态 ----------
let inflight = false, misses = 0;
async function pollState() {
  if (inflight) return;
  inflight = true;
  try {
    const r = await fetch("/api/status", { cache: "no-store" });
    const d = await r.json();
    misses = 0;
    renderState(d);
  } catch (e) {
    if (++misses >= 3) setPill("连不上板子", "bad");
  } finally {
    inflight = false;
  }
}

function renderState(d) {
  const st = d.status || null;
  const pr = d.proc || {};
  const age = d.age;
  // 实时那条也可能是 run_live.sh / notebook 起的：这时 proc.running 是 false，
  // 但状态文件是新鲜的 —— 以状态文件为准，别把"别人起的"误显示成"已停止"。
  const live = !!pr.running || !!(st && st.running && age !== null && age < 1.5);
  isRunning = live;

  if (pr.stopping) {
    setPill("停止中 · 自检", "warn");
  } else if (live) {
    if (age !== null && age > 1.5) setPill("运行中，但状态 " + age.toFixed(1) + " s 没更新", "bad");
    else setPill(pr.running ? "运行中" : "运行中（不是本页起的）", "ok");
  } else if (pr.running) {
    setPill("启动中…", "warn");
  } else if (st && st.ok === true) {
    setPill("已停止 · 自检通过", "ok");
  } else if (st && st.ok === false) {
    setPill("已停止 · 自检没过", "bad");
  } else if (st) {
    setPill("已停止", "idle");
  } else {
    setPill("未启动", "idle");
  }

  $("age").textContent = (isRunning && age !== null && age > 1.0)
    ? ("数据 " + age.toFixed(1) + " s 前") : "";

  // 每段的实际值
  const app = (st && st.gain_applied_db) || null;
  rows.forEach((r, i) => {
    r.applied.textContent = app && app[i] !== null && app[i] !== undefined
      ? ("实 " + fmtDB(app[i])) : "—";
  });

  // 电平
  if (st) {
    const di = dbOf(st.in_rms), dO = dbOf(st.out_rms);
    setBar($("barIn"), di, "lvl");
    setBar($("barOut"), dO, "lvl");
    $("dbIn").textContent = di <= -119 ? "—" : di.toFixed(1) + " dB";
    $("dbOut").textContent = dO <= -119 ? "—" : dO.toFixed(1) + " dB";
    const act = (st.in_rms && st.in_rms > 0) ? (dO - di) : null;
    setBar($("barComp"), act === null ? 0 : act, "comp");
    $("dbComp").textContent = act === null ? "—" : fmtDB(act);
  } else {
    ["barIn", "barOut", "barComp"].forEach((k) => { $(k).style.width = "0%"; });
    ["dbIn", "dbOut", "dbComp"].forEach((k) => { $(k).textContent = "—"; });
  }

  // 底部数字
  const bits = [];
  if (st) {
    bits.push("第 " + (st.n || 0) + " 块");
    bits.push("实时 " + (st.rt ? st.rt.toFixed(2) + "x" : "—"));
    bits.push("丢块 " + (st.lost_blocks || 0));
    bits.push("搬样 " + (st.io_ms ? st.io_ms.toFixed(2) + " ms" : "—"));
    bits.push("过核 " + (st.dsp_ms ? st.dsp_ms.toFixed(2) + " ms" : "—"));
    if (st.attack_ms && st.release_ms &&
        (st.attack_ms[0] > 0 || st.release_ms[0] > 0)) {
      bits.push("起振 " + st.attack_ms[0].toFixed(0) + " / 释放 " + st.release_ms[0].toFixed(0) + " ms");
    } else {
      bits.push("起振释放 关");
    }
    if (st.error) bits.push("报错：" + st.error);
  } else {
    bits.push("还没跑过 —— 点右上角「启动实时」");
  }
  $("stats").textContent = bits.join("　");

  // 日志：跑起来就收起；停下来的自检没过 / 有报错才把尾巴摊开
  if (pr.running) {
    $("logbox").hidden = true;
  } else {
    const bad = st && (st.ok === false || st.error);
    if (bad) {
      $("logbox").textContent = pr.log || "";
      $("logbox").hidden = !pr.log;
    }
  }
}

function setBar(el, db, kind) {
  let pct;
  if (kind === "lvl") {
    pct = Math.max(0, Math.min(100, (db + 60) / 60 * 100));
    el.style.width = pct.toFixed(1) + "%";
    el.className = db < -12 ? "" : (db < -3 ? "warn" : "bad");
  } else {
    // 压缩动作：压得越狠条越长（0 dB 动作 = 空条）
    pct = Math.max(0, Math.min(100, Math.abs(db) / 24 * 100));
    el.style.width = pct.toFixed(1) + "%";
    el.className = "blue";
  }
}

// ---------- 四段响应曲线 ----------
function drawCurve() {
  const { ctx, w, h } = fitCanvas($("curve"));
  ctx.clearRect(0, 0, w, h);
  const padL = 34, padR = 10, padT = 10, padB = 16;
  const x0 = padL, x1 = w - padR, y0 = padT, y1 = h - padB;
  const lo = 20, hi = 20000;
  const lx = (f) => x0 + (Math.log10(f) - Math.log10(lo)) /
                     (Math.log10(hi) - Math.log10(lo)) * (x1 - x0);
  const gy = (g) => (y0 + y1) / 2 - g / GAIN_LIMIT * ((y1 - y0) / 2 - 4);

  ctx.font = "10px Consolas,monospace";
  ctx.textBaseline = "middle";
  [-24, -12, 0, 12, 24].forEach((g) => {
    const y = gy(g);
    ctx.strokeStyle = g === 0 ? "rgba(255,255,255,.28)" : "rgba(255,255,255,.09)";
    ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    ctx.fillStyle = "rgba(160,175,210,.75)";
    ctx.textAlign = "right";
    ctx.fillText((g > 0 ? "+" : "") + g, x0 - 5, y);
  });
  [100, 1000, 10000].forEach((f) => {
    ctx.strokeStyle = "rgba(255,255,255,.07)";
    ctx.beginPath(); ctx.moveTo(lx(f), y0); ctx.lineTo(lx(f), y1); ctx.stroke();
    ctx.fillStyle = "rgba(160,175,210,.6)";
    ctx.textAlign = "center";
    ctx.fillText(f >= 1000 ? (f / 1000) + "k" : "" + f, lx(f), y1 + 8);
  });

  const edges = [20, 500, 1000, 2000, 20000];
  // 分段点
  [500, 1000, 2000].forEach((f) => {
    ctx.strokeStyle = "rgba(106,169,255,.42)";
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(lx(f), y0); ctx.lineTo(lx(f), y1); ctx.stroke();
    ctx.setLineDash([]);
  });

  // 阶梯线
  const pts = [];
  for (let i = 0; i < 4; i++) {
    pts.push([edges[i], S.gain[i]]);
    pts.push([edges[i + 1], S.gain[i]]);
  }
  ctx.beginPath();
  pts.forEach(([f, g], k) => {
    const X = lx(f), Y = gy(g);
    if (k === 0) ctx.moveTo(X, Y); else ctx.lineTo(X, Y);
  });
  ctx.lineWidth = 2.2;
  ctx.strokeStyle = "#2ee6a8";
  ctx.shadowColor = "rgba(46,230,168,.45)";
  ctx.shadowBlur = 8;
  ctx.stroke();
  ctx.shadowBlur = 0;

  // 面积填充到 0 dB
  ctx.beginPath();
  ctx.moveTo(lx(edges[0]), gy(0));
  pts.forEach(([f, g]) => ctx.lineTo(lx(f), gy(g)));
  ctx.lineTo(lx(edges[4]), gy(0));
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, y0, 0, y1);
  grad.addColorStop(0, "rgba(46,230,168,.22)");
  grad.addColorStop(0.5, "rgba(46,230,168,.05)");
  grad.addColorStop(1, "rgba(46,230,168,.22)");
  ctx.fillStyle = grad;
  ctx.fill();

  // 每段标注
  ctx.textAlign = "center";
  for (let i = 0; i < 4; i++) {
    const fmid = Math.sqrt(edges[i] * edges[i + 1]);
    const X = (lx(edges[i]) + lx(edges[i + 1])) / 2;
    const Y = gy(S.gain[i]) - (S.gain[i] >= 0 ? 11 : -15);
    ctx.fillStyle = "rgba(233,238,255,.86)";
    ctx.font = "11px Consolas,monospace";
    ctx.fillText(fmtDB(S.gain[i]), X, Y);
  }
}

// ---------- 波形 / 频谱 ----------
let lastScope = null;
let isRunning = false;
async function pollScope() {
  if (!isRunning) return;              // 停了就留着最后一帧，别拿错误提示覆盖它
  try {
    const r = await fetch("/api/scope", { cache: "no-store" });
    const d = await r.json();
    if (d.ok) { lastScope = d; drawWave(d); drawSpec(d); }
    else if (d.msg) { drawScopeError(d.msg); }
  } catch (e) { /* 下一轮再说 */ }
}

function drawScopeError(msg) {
  const { ctx, w, h } = fitCanvas($("wave"));
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "rgba(255,180,84,.8)";
  ctx.font = "11px system-ui";
  ctx.textAlign = "center";
  ctx.fillText(msg, w / 2, h / 2);
}

function drawWave(d) {
  const { ctx, w, h } = fitCanvas($("wave"));
  ctx.clearRect(0, 0, w, h);
  const mid = h / 2, amp = h / 2 - 3;
  ctx.strokeStyle = "rgba(255,255,255,.10)";
  ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(w, mid); ctx.stroke();
  const poly = (arr, color, lw) => {
    ctx.beginPath();
    arr.forEach((v, i) => {
      const X = i / (arr.length - 1) * w;
      const Y = mid - (v / 32768) * amp;
      if (i === 0) ctx.moveTo(X, Y); else ctx.lineTo(X, Y);
    });
    ctx.lineWidth = lw; ctx.strokeStyle = color; ctx.stroke();
  };
  poly(d.wave.a, "rgba(180,195,225,.55)", 1);
  poly(d.wave.b, "#2ee6a8", 1.5);
}

function drawSpec(d) {
  const { ctx, w, h } = fitCanvas($("spec"));
  ctx.clearRect(0, 0, w, h);
  if (!d.spec) return;
  const padB = 12, top = 4;
  const yOf = (db) => top + (0 - db) / 100 * (h - top - padB);
  ctx.font = "9px Consolas,monospace";
  [0, -50, -100].forEach((db) => {
    ctx.strokeStyle = "rgba(255,255,255,.08)";
    ctx.beginPath(); ctx.moveTo(0, yOf(db)); ctx.lineTo(w - 2, yOf(db)); ctx.stroke();
    ctx.fillStyle = "rgba(160,175,210,.55)";
    ctx.textAlign = "left";
    ctx.fillText(db + " dB", 2, yOf(db) - 2);
  });
  [0.5, 1, 2].forEach((k) => {
    const X = (k * 1000 / 4000) * w;
    ctx.strokeStyle = "rgba(106,169,255,.38)";
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(X, top); ctx.lineTo(X, h - padB); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(160,175,210,.55)";
    ctx.textAlign = "center";
    ctx.fillText(k + "k", X, h - 2);
  });
  const poly = (arr, color, lw) => {
    ctx.beginPath();
    d.spec.f.forEach((f, i) => {
      const X = (f / 4000) * w;
      const Y = yOf(arr[i]);
      if (i === 0) ctx.moveTo(X, Y); else ctx.lineTo(X, Y);
    });
    ctx.lineWidth = lw; ctx.strokeStyle = color; ctx.stroke();
  };
  poly(d.spec.a, "rgba(180,195,225,.55)", 1);
  poly(d.spec.b, "#2ee6a8", 1.4);
}

// ---------- 初始化 ----------
async function init() {
  paintGains();
  drawCurve();
  try {
    const d = await (await fetch("/api/tune", { cache: "no-store" })).json();
    if (d.ok && d.tune) {
      const t = d.tune;
      if (Array.isArray(t.gain_db) && t.gain_db.length === 4) S.gain = t.gain_db.map(Number);
      if (Array.isArray(t.thr_db) && t.thr_db.length) S.thr = Number(t.thr_db[0]);
      if (Array.isArray(t.attack_ms) && t.attack_ms.length) S.atk = Number(t.attack_ms[0]);
      if (Array.isArray(t.release_ms) && t.release_ms.length) S.rel = Number(t.release_ms[0]);
      if (Array.isArray(t.ratio) && t.ratio.length) {
        const q = Math.max(0, Math.min(0.95, Number(t.ratio[0])));
        S.ratio = q <= 0 ? 1 : Math.round((1 / (1 - q)) * 2) / 2;
      }
      S.byp = !!t.bypass;
      $("thr").value = S.thr; $("thrV").textContent = S.thr.toFixed(1) + " dBFS";
      $("ratio").value = S.ratio; $("ratioV").textContent = S.ratio.toFixed(1) + " : 1";
      $("atk").value = S.atk; $("atkV").textContent = S.atk.toFixed(0) + " ms";
      $("rel").value = S.rel; $("relV").textContent = S.rel.toFixed(0) + " ms";
      $("byp").checked = S.byp;
      paintGains(); drawCurve();
    }
  } catch (e) { /* 板子刚起，接口还没好；下一次刷新再说 */ }
  // 一定要写一次：/dev/shm 每次重启都是空的，而界面上已经显示了
  // 门限 -10 dBFS / 2:1。不写的话，核里用的还是 fir_live.py 自己的默认值，
  // 界面显示的和实际生效的对不上。notebook 那一版末尾也有 push_tune()。
  schedulePush();
  pollState();
}

window.addEventListener("resize", () => {
  drawCurve();
  if (lastScope) { drawWave(lastScope); drawSpec(lastScope); }
});
setInterval(pollState, 150);
setInterval(pollScope, 250);
init();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    srv = Server((args.host, args.port), Handler)
    n = _pin_cpu1()
    ips = _board_ips()
    print("调参页已就绪（%d 个线程钉在 CPU1%s）" % (
        n, "" if n else " —— 钉失败，界面可能滞后"))
    print("  http://%s:%d/" % (ips[0] if ips else "<板子IP>", args.port))
    for ip in ips[1:]:
        print("  http://%s:%d/" % (ip, args.port))
    print("音频通路不受这个进程影响：关掉浏览器照跑。Ctrl-C 退出页面服务（不停止音频）。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n页面服务退出。实时那条如果还在跑，用「停止」按钮或 kill 收掉。")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
