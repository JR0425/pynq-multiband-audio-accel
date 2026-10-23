#!/usr/bin/env python3
"""性能线测量：音频链路延迟（数字法）+ CPU 占用。主机侧跑，SSH 上板。

    python tools/board_debug/perf_probe.py latency [秒/配置]
    python tools/board_debug/perf_probe.py cpu [秒]

latency：对每个配置跑一遍实时通路（LIVE_SRC=wav，不依赖话筒），
  由 fir_live.py 的 LAT_TRACE 记下每块"被采到的时刻"和"交给 codec 的时刻"，
  两者之差就是这一块每个采样真正经过的数字通路延迟。四个配置见 CONFIGS。
  产物：data/results/latency_<配置>_<日期>.json + 一张汇总表。

cpu：跑 60 秒实时（同演示配置），板上每秒采一次 /proc/stat 和进程/线程
  jiffies，跑完拉回主机算占用。

⚠️ 板子上的 `fir_live.py` 会被这个脚本覆盖（从仓库推当前版本）。
"""

import base64
import hashlib
import json
import os
import statistics
import sys
import time

import paramiko

HOST, USER, PASSWD = "192.168.2.99", "xilinx", "xilinx"
BOARD_DIR = "/home/xilinx/jupyter_notebooks"
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(REPO, "data", "results")
STAMP = time.strftime("%Y%m%d")

# (名字, L, NBUF, 公式延迟 ms) —— 公式 = NBUF*L/48000*1000 + 2 ms(codec)
CONFIGS = [
    ("n3_l480", 480, 3, 32),
    ("n2_l480", 480, 2, 22),
    ("n2_l240", 240, 2, 12),
    ("n2_l120", 120, 2, 7),
]

CLK = 100        # USER_HZ


def ssh():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWD, timeout=25,
              banner_timeout=25, auth_timeout=25)
    return c


def run(c, cmd, timeout=300, get_pty=False):
    _in, out, err = c.exec_command(cmd, timeout=timeout, get_pty=get_pty)
    try:
        _in.close()
    except OSError:
        pass
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    return out.channel.recv_exit_status(), o, e


def push(c, local, remote, make_exec=False):
    """推文件并**校验 md5**。只信 rc 会踩坑：曾经 rc=0 但板上还是旧文件。"""
    with open(local, "rb") as f:
        raw = f.read().replace(b"\r\n", b"\n")
    want = hashlib.md5(raw).hexdigest()
    b64 = base64.b64encode(raw)
    # 先写临时文件再 mv，避免半截文件被正在跑的进程读到
    tmp = remote + ".new"
    _in, out, err = c.exec_command("base64 -d > %s" % tmp, timeout=120)
    _in.write(b64)
    _in.channel.shutdown_write()
    rc = out.channel.recv_exit_status()
    if rc != 0:
        return rc, err.read().decode("utf-8", "replace")
    rc2, o, e = run(c, "chmod %s %s && mv %s %s && md5sum %s"
                    % ("+x" if make_exec else "644", tmp, tmp, remote, remote))
    got = o.split()[0] if o.strip() else ""
    if rc2 != 0 or got != want:
        return 1, "md5 不符：期望 %s 实际 %s%s" % (want, got, e.strip())
    return 0, ""


def pull(c, remote, local):
    rc, o, e = run(c, "base64 -w0 %s" % remote, timeout=120)
    if rc != 0:
        raise RuntimeError("pull %s failed: %s" % (remote, e))
    with open(local, "wb") as f:
        f.write(base64.b64decode(o.encode()))


def live_cmd(extra, secs):
    return ("cd %s && LIVE_EXTRA='%s' sh run_live.sh %d"
            % (BOARD_DIR, extra, secs))


def do_latency(c, secs):
    rc, e = push(c, os.path.join(REPO, "board", "scripts", "fir_live.py"),
                 os.path.join(BOARD_DIR, "fir_live.py"))
    print("push fir_live.py rc=%d %s" % (rc, e.strip()))
    if rc != 0:
        raise SystemExit("push 失败，停下来别拿旧文件量")
    rows = []
    for name, L, NBUF, formula in CONFIGS:
        rj = "/tmp/lat_%s.json" % name
        extra = "LIVE_SRC=wav LIVE_L=%d LIVE_NBUF=%d LAT_TRACE=%s" % (
            L, NBUF, rj)
        run(c, "rm -f %s" % rj)
        print("\n=== %s: L=%d NBUF=%d（公式 %.0f ms）===" % (name, L, NBUF, formula))
        t0 = time.time()
        rc, o, e = run(c, live_cmd(extra, secs), timeout=secs + 90)
        print("   run rc=%d  %.1f s" % (rc, time.time() - t0))
        tail = [ln for ln in o.splitlines() if "逐位" in ln or "自检" in ln
                or "实时" in ln or "丢" in ln]
        for ln in tail[-6:]:
            print("   | " + ln.strip())
        local = os.path.join(OUT, "latency_%s_%s.json" % (name, STAMP))
        pull(c, rj, local)
        with open(local) as f:
            d = json.load(f)
        cap, out = d["cap"], d["out"]
        # 每条记录是 (块序号, 交给 codec 的时刻)；块序号就是 cap 的下标
        lat = [(s, t - cap[s]) for s, t in out if 0 <= s < len(cap)]
        if not lat:
            print("   !! 没有可用的延迟样本")
            continue
        v = sorted(x[1] * 1000.0 for x in lat)
        n = len(v)
        med = statistics.median(v)
        p90 = v[int(n * 0.9)]
        print("   n=%d  中位 %.2f ms  p90 %.2f  min %.2f  max %.2f  (L=%d → %.2f 块)"
              % (n, med, p90, v[0], v[-1], L, med / (1000.0 * L / 48000)))
        rows.append((name, L, NBUF, formula, n, med, p90, v[0], v[-1], local))
    print("\n| 配置 | L | NBUF | 公式 ms | n | 中位 ms | p90 ms | min | max |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print("| %s | %d | %d | %.0f | %d | %.2f | %.2f | %.2f | %.2f |"
              % r[:9])


def do_cpu(c, secs):
    for local, remote in (
            (os.path.join(REPO, "board", "scripts", "fir_live.py"),
             os.path.join(BOARD_DIR, "fir_live.py")),
            (os.path.join(REPO, "tools", "board_debug", "cpu_watch.py"),
             "/tmp/cpu_watch.py")):
        rc, msg = push(c, local, remote)
        if rc != 0:
            raise SystemExit("push %s 失败：%s" % (os.path.basename(local), msg))
    run(c, "echo xilinx | sudo -S pkill -INT -f 'fir_live[.]py' ; sleep 3")
    run(c, "rm -f /tmp/cpu.csv /tmp/live_out.txt")
    extra = "LIVE_SRC=mic"
    # 必须串在同一条 SSH 命令里：后台子进程会一直占着通道，分两次发会卡到超时
    cmd = ("cd %s && (LIVE_EXTRA='%s' sh run_live.sh %d </dev/null "
           ">/tmp/live_out.txt 2>&1 &) ; sleep 3 ; "
           "echo xilinx | sudo -S /usr/bin/python3 /tmp/cpu_watch.py %d /tmp/cpu.csv"
           % (BOARD_DIR, extra, secs, secs + 2))
    rc, o, e = run(c, cmd, timeout=secs + 120)
    print(o[-300:].strip() or e[-300:].strip())
    rc, o, e = run(c, "cat /tmp/live_out.txt | tail -30")
    print(o)
    local = os.path.join(OUT, "cpu_%s.csv" % STAMP)
    pull(c, "/tmp/cpu.csv", local)
    rows = []
    with open(local) as f:
        next(f)
        for ln in f:
            p = ln.strip().split(",")
            if len(p) < 6 or not p[1]:
                continue
            rows.append([float(p[0])] + [(int(x) if x else None) for x in p[1:]])
    rows = rows[6:]                         # 前几秒在下载 overlay，不算
    acc = {"dt": 0.0, "proc": 0.0, "cpu": 0.0, "idle": 0.0,
           "io": 0.0, "dsp": 0.0, "io_dt": 0.0, "dsp_dt": 0.0}
    for a, b in zip(rows, rows[1:]):
        dt = b[0] - a[0]
        if dt <= 0 or b[1] is None or a[1] is None:
            continue
        acc["dt"] += dt
        acc["proc"] += b[1] - a[1]
        acc["cpu"] += b[2] - a[2]
        acc["idle"] += b[3] - a[3]
        for k, i in (("io", 4), ("dsp", 5)):
            if a[i] is not None and b[i] is not None:
                acc[k] += b[i] - a[i]
                acc[k + "_dt"] += dt
    if acc["dt"] < 10:
        print("!! 有效样本太少：%.1f 秒" % acc["dt"])
        return
    core_j = acc["cpu"] / 2.0               # 双核，单核 jiffies
    print("\n统计 %.1f 秒：" % acc["dt"])
    print("  整机 idle  %.1f%%" % (100.0 * acc["idle"] / acc["cpu"]))
    print("  fir_live 进程占单核 %.1f%%（整机 %.1f%%）"
          % (100.0 * acc["proc"] / core_j, 100.0 * acc["proc"] / acc["cpu"]))
    if acc["io_dt"] > 0:
        print("    io 线程  %.1f%% 单核" % (100.0 * acc["io"] / core_j))
    if acc["dsp_dt"] > 0:
        print("    dsp 线程 %.1f%% 单核" % (100.0 * acc["dsp"] / core_j))
    if acc["io_dt"] > 0 and acc["dsp_dt"] > 0:
        print("    其余     %.1f%% 单核"
              % (100.0 * (acc["proc"] - acc["io"] - acc["dsp"]) / core_j))


def main():
    # Windows 控制台是 GBK，板子输出里有 ⚠ 之类的字符，直接 print 会抛异常
    sys.stdout.reconfigure(errors="replace")
    mode = sys.argv[1] if len(sys.argv) > 1 else "latency"
    secs = int(sys.argv[2]) if len(sys.argv) > 2 else (20 if mode == "latency" else 60)
    c = ssh()
    try:
        if mode == "latency":
            do_latency(c, secs)
        else:
            do_cpu(c, secs)
    finally:
        c.close()


if __name__ == "__main__":
    main()
