#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_drop_matrix.py —— 主机侧：跑「配置 × 丢块」矩阵。**在 Windows 上跑。**

    python tools/board_debug/run_drop_matrix.py [--arms base,gb3,...]

每一臂在板子上跑 60 秒（由 board_debug/drop_probe.py 采集），主机只负责
推文件、对齐起跑相位（板子 uptime 相对 30 秒定时器）、开/关网页负载、
钉/还原非音频进程的 CPU 亲和性。

网页负载 = 主机上开一个 headless Chrome 指着 http://192.168.2.99:8080/ ，
和演示时人开着页面一样（页面每 150 ms 拉状态、250 ms 拉示波器）。

结果落在 tools/_tmp/drop_matrix_<时间>/，同时打印一张表。
"""

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

import paramiko

HOST = "192.168.2.99"
USER = "xilinx"
PASS = "xilinx"
BOARD_DIR = "/home/xilinx/jupyter_notebooks"
BOARD_PY = "/usr/local/share/pynq-venv/bin/python3"
CHROME = r"C:\Users\XIA\.agent-browser\browsers\chrome-155.0.8059.84\chrome.exe"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=15,
              banner_timeout=15, auth_timeout=15)
    return c


def run(c, cmd, timeout=240, sudo=False):
    if sudo:
        cmd = "echo xilinx | sudo -S " + cmd
    _in, out, err = c.exec_command(cmd, timeout=timeout)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    rc = out.channel.recv_exit_status()
    return rc, o, e


def push(c, local, remote):
    sftp = c.open_sftp()
    try:
        sftp.put(local, remote)
    finally:
        sftp.close()


def uptime(c):
    return float(run(c, "cat /proc/uptime")[1].split()[0])


def wait_phase(c, period=30.0, lo=1.0, hi=2.5, timeout=45.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        up = uptime(c)
        ph = up % period
        if lo <= ph <= hi:
            return up
        time.sleep(0.25)
    return None


def start_chrome(url):
    prof = tempfile.mkdtemp(prefix="drop-page-")
    p = subprocess.Popen([
        CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--user-data-dir=" + prof, url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(4.0)
    return p


def bpath(name):
    """板上路径。不能用 os.path.join：Windows 上它会给出反斜杠。"""
    return "%s/%s" % (BOARD_DIR, name)


def push_checked(c, local, remote):
    sftp = c.open_sftp()
    try:
        sftp.put(local, remote)
        size = sftp.stat(remote).st_size
    finally:
        sftp.close()
    local_size = os.path.getsize(local)
    if size != local_size:
        raise RuntimeError("push 不完整: %s (%d != %d)"
                           % (remote, size, local_size))
    print("   ok %s (%d bytes)" % (remote, size), flush=True)


def clean_shm(c):
    """删掉上一轮遗留的状态/探针文件。

    必须用 root：/dev/shm 是 sticky 目录，早先用 root 跑过的话留下的文件
    xilinx 删不掉；而 fs.protected_regular=2 又让 root 无法截断别人的文件。
    """
    run(c, "rm -f /dev/shm/drop_* /dev/shm/fir_status.json "
           "/dev/shm/fir_tune.json /dev/shm/fir_live.lock "
           "/dev/shm/fir_scope.bin /dev/shm/fir_webui_last.json "
           "/dev/shm/non_audio_affinity_backup.json", sudo=True, timeout=30)


def unpin(c):
    rc, o, e = run(c, "%s %s unpin" % (BOARD_PY, bpath("pin_non_audio.py")),
                   sudo=True, timeout=30)
    return (o or e).strip()


def restore_affinity(c):
    run(c, "%s %s restore" % (BOARD_PY, bpath("pin_non_audio.py")),
        sudo=True, timeout=30)


def arm_command(tag, nbuf, l, seconds, switch=None, queue=None):
    extra = "" if switch is None else " LIVE_SWITCH=%s" % switch
    if queue is not None:
        extra += " LIVE_QUEUE=%s" % queue
    return ("env XILINX_XRT=/usr PYTHONIOENCODING=utf-8%s %s %s "
            "--tag %s --seconds %d --nbuf %d --l %d"
            % (extra, BOARD_PY, bpath("drop_probe.py"), tag, seconds, nbuf, l))


def run_arm(c, ctl, arm, seconds, outdir):
    tag = arm["tag"]
    print("== %-22s NBUF=%d L=%d pin=%d page=%s" %
          (tag, arm["nbuf"], arm["l"], arm["pin"], arm["page"]), flush=True)
    print("   unpin:", unpin(c), flush=True)
    ph = wait_phase(c)
    print("   launch phase: uptime=%s (mod30=%.2f)" %
          (ph, (ph or 0) % 30), flush=True)

    if arm["pin"]:
        def _pin():
            time.sleep(0.4)
            rc, o, e = run(ctl, "%s %s apply"
                           % (BOARD_PY, bpath("pin_non_audio.py")),
                           sudo=True, timeout=30)
            print("   pin:", (o or e).strip(), flush=True)
        th = threading.Thread(target=_pin)
        th.start()
    else:
        th = None

    t0 = time.time()
    rc, out, err = run(c, arm_command(tag, arm["nbuf"], arm["l"], seconds,
                                      arm.get("switch"), arm.get("queue")),
                       timeout=seconds + 120, sudo=True)
    if th:
        th.join()
    print("   rc=%d elapsed=%.1fs" % (rc, time.time() - t0), flush=True)

    summary = None
    for line in out.splitlines():
        if line.startswith("DROP_SUMMARY "):
            summary = json.loads(line[len("DROP_SUMMARY "):])
    if summary is None:
        print("   !! 没拿到 DROP_SUMMARY; stdout tail:", out[-300:],
              "stderr:", err[-300:], flush=True)
        summary = {"tag": tag, "error": (err or out)[-500:]}
    summary["arm"] = arm
    with open(os.path.join(outdir, tag + ".json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    # 板上的逐帧/逐块明细也取回来，后面看时间分布要用
    sftp = c.open_sftp()
    try:
        for remote in ("/dev/shm/drop_%s.jsonl" % tag,
                       "/dev/shm/drop_trace_%s.json" % tag,
                       "/dev/shm/drop_%s.log" % tag):
            try:
                sftp.get(remote, os.path.join(outdir, os.path.basename(remote)))
            except OSError as exc:
                print("   (取不回 %s: %s)" % (remote, exc), flush=True)
    finally:
        sftp.close()

    try:
        if arm["pin"]:
            restore_affinity(c)
    except Exception as exc:                               # noqa: BLE001
        print("   restore 失败（板子可能已经不对了）: %r" % (exc,), flush=True)
    time.sleep(3.0)
    return summary


def fmt(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, float):
        return ("%." + str(nd) + "f") % v
    return str(v)


def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def table(rows, outdir):
    lines = []
    lines.append("| 臂 | NBUF | L | 钉 CPU1 | 网页 | 缺口 ms/60s | 折 10ms 块/60s | 事件数 | 30s 集中度 | io 中位/p90 (ms) | dsp p90 | CPU0/1 | webui 平均 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for s in rows:
        a = s.get("arm") or {}
        io = s.get("io_ms") or {}
        dsp = s.get("dsp_ms") or {}
        cpu = s.get("cpu_busy_pct") or {}
        fs = s.get("family_series") or []
        wavg = avg([x.get("webui_pct") for x in fs])
        ph = s.get("phase_hist_30s") or []
        conc = (max(ph) / float(sum(ph))) if sum(ph) else None
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s / %s | %s | %s / %s | %s%% |" % (
            s.get("tag"), a.get("nbuf"), a.get("l"),
            "开" if a.get("pin") else "关",
            "开" if a.get("page") else "关",
            fmt(s.get("deficit_ms_per_60s"), 1),
            fmt(s.get("lost_10ms_per_60s")),
            len(s.get("events") or []),
            fmt(conc, 2),
            fmt(io.get("median")), fmt(io.get("p90")), fmt(dsp.get("p90")),
            fmt(cpu.get("cpu0"), 1), fmt(cpu.get("cpu1"), 1), fmt(wavg, 1)))
    txt = "\n".join(lines)
    with open(os.path.join(outdir, "table.md"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print("\n" + txt, flush=True)
    return txt


DEFAULT_ARMS = [
    # page=0 是对照组，确认"打开网页"本身贡献多少丢块
    {"tag": "nopage_nopin_3_480", "nbuf": 3, "l": 480, "pin": 0, "page": 0},
    {"tag": "page_nopin_3_480", "nbuf": 3, "l": 480, "pin": 0, "page": 1},
    {"tag": "page_pin_3_480", "nbuf": 3, "l": 480, "pin": 1, "page": 1},
    {"tag": "page_nopin_4_480", "nbuf": 4, "l": 480, "pin": 0, "page": 1},
    {"tag": "page_pin_4_480", "nbuf": 4, "l": 480, "pin": 1, "page": 1},
    {"tag": "page_nopin_3_960", "nbuf": 3, "l": 960, "pin": 0, "page": 1},
    {"tag": "page_pin_3_960", "nbuf": 3, "l": 960, "pin": 1, "page": 1},
    {"tag": "page_nopin_4_960", "nbuf": 4, "l": 960, "pin": 0, "page": 1},
    {"tag": "page_pin_4_960", "nbuf": 4, "l": 960, "pin": 1, "page": 1},
    # 下面三臂是探机制，不是候选配置：
    # L=1920 看「缺口随块长」的走势；sw=1e-4 看两块之间的 Python 空档
    # （GIL 等多久）能不能靠 sys.setswitchinterval 压下来。
    {"tag": "page_nopin_3_1920", "nbuf": 3, "l": 1920, "pin": 0, "page": 1},
    {"tag": "page_nopin_2_1920", "nbuf": 2, "l": 1920, "pin": 0, "page": 1},
    {"tag": "page_nopin_3_480_sw1e4", "nbuf": 3, "l": 480, "pin": 0,
     "page": 1, "switch": "0.0001"},
    # 队列实现对照。fir_live 现在默认 SimpleQueue，所以 "queue" 这一臂是旧实现。
    {"tag": "page_nopin_3_480_qsimple", "nbuf": 3, "l": 480, "pin": 0,
     "page": 1, "queue": "simple"},
    {"tag": "page_nopin_3_480_qold", "nbuf": 3, "l": 480, "pin": 0,
     "page": 1, "queue": "queue"},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--arms", default="")
    ap.add_argument("--outdir", default="")
    ap.add_argument("--no-page", action="store_true",
                    help="不开 Chrome（仍然按 arm 的 page 字段记账之外，全部关）")
    a = ap.parse_args()

    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = a.outdir or os.path.join(ROOT, "tools", "_tmp", "drop_matrix_" + stamp)
    os.makedirs(outdir, exist_ok=True)

    arms = DEFAULT_ARMS
    if a.arms:
        want = set(x.strip() for x in a.arms.split(",") if x.strip())
        arms = [x for x in DEFAULT_ARMS if x["tag"] in want]
        if not arms:
            raise SystemExit("--arms 没匹配到任何臂；可选：%s"
                             % ",".join(x["tag"] for x in DEFAULT_ARMS))

    c = connect()
    ctl = connect()
    chrome = None
    rows = []
    try:
        files = [
            (os.path.join(ROOT, "board", "scripts", "fir_live.py"),
             bpath("fir_live.py")),
            (os.path.join(ROOT, "tools", "board_debug", "drop_probe.py"),
             bpath("drop_probe.py")),
            (os.path.join(ROOT, "tools", "board_debug", "pin_non_audio.py"),
             bpath("pin_non_audio.py")),
        ]
        for local, remote in files:
            print("push %s -> %s" % (local, remote), flush=True)
            push_checked(c, local, remote)

        clean_shm(c)
        print("clean /dev/shm: done", flush=True)

        rc, o, e = run(c, "pgrep -f 'python3 .*fir_live.py'")
        if o.strip():
            print("先停掉板上已有的 fir_live:", o.strip(), flush=True)
            run(c, "pkill -INT -f 'python3 .*fir_live.py'")
            time.sleep(8)

        # 上一轮实验可能留下亲和性备份；从干净状态起（两核都放开）
        run(c, "rm -f /dev/shm/non_audio_affinity_backup.json", sudo=True)
        print("unpin:", unpin(c), flush=True)

        page_started = False
        for arm in arms:
            if arm["page"] and not a.no_page and not page_started:
                print("开 headless Chrome 页面负载…", flush=True)
                chrome = start_chrome("http://%s:8080/" % HOST)
                page_started = True
            if not arm["page"] and page_started:
                chrome.terminate()
                chrome.wait(timeout=10)
                chrome = None
                page_started = False
                time.sleep(2)
            try:
                rows.append(run_arm(c, ctl, arm, a.seconds, outdir))
            except Exception as exc:                       # noqa: BLE001
                print("   !! 这一臂失败: %r" % (exc,), flush=True)
                rows.append({"tag": arm["tag"], "arm": arm,
                             "error": repr(exc)})

        table(rows, outdir)
        print("结果目录:", outdir, flush=True)
    finally:
        try:
            unpin(c)
        except Exception:
            pass
        if chrome is not None:
            chrome.terminate()
        try:
            c.close()
            ctl.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
