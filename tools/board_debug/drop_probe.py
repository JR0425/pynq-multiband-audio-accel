#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""drop_probe.py —— 一档 60 秒的实时丢块探针。**只在板子上跑。**

    sudo env XILINX_XRT=/usr PYTHONIOENCODING=utf-8 \\
        /usr/local/share/pynq-venv/bin/python3 drop_probe.py \\
        --tag base --seconds 60 --nbuf 3 --l 480

它自己起一条 `fir_live.py`（默认麦克风、LIVE_SECONDS=seconds），然后在板内
按 20 Hz 读 `/dev/shm/fir_status.json` —— **不能从主机走 SSH 轮询**，那样
拉取器本身就会制造丢块（实测 0.20 → 0.90 块/秒）。同时每秒记一次 CPU 和
线程负载。

输出（都在 /dev/shm，重启板子会丢，跑完由主机侧取走）：
  drop_<tag>.json     汇总
  drop_<tag>.jsonl    每一帧状态（20 Hz）
  drop_trace_<tag>.json   每块搬样时刻（来自 fir_live 的 DROP_TRACE）

最后一行打印 `DROP_SUMMARY {json}`，主机侧的 run_drop_matrix.py 解析这一行。
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time

HERE = "/home/xilinx/jupyter_notebooks"
STATUS = "/dev/shm/fir_status.json"


def read_status():
    try:
        with open(STATUS, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read_text(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except OSError:
        return ""


def cmdline(pid):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def task_comm(pid, tid):
    return read_text("/proc/%d/task/%d/comm" % (pid, tid)).strip()


def task_cpu_ticks(pid, tid=None):
    if tid is None:
        path = "/proc/%d/stat" % pid
    else:
        path = "/proc/%d/task/%d/stat" % (pid, tid)
    s = read_text(path)
    if not s:
        return None
    try:
        rest = s.rsplit(")", 1)[1].split()
        return int(rest[11]) + int(rest[12])      # utime + stime
    except (IndexError, ValueError):
        return None


def sched_bits(tid):
    out = {}
    try:
        out["policy"] = os.sched_getscheduler(tid)
    except OSError:
        out["policy"] = None
    try:
        out["prio"] = os.sched_getparam(tid).sched_priority
    except OSError:
        out["prio"] = None
    try:
        out["affinity"] = sorted(os.sched_getaffinity(tid))
    except OSError:
        out["affinity"] = None
    return out


def cpu_counters():
    out = {}
    for line in read_text("/proc/stat").splitlines():
        p = line.split()
        if len(p) >= 5 and p[0].startswith("cpu") and p[0][3:].isdigit():
            v = [int(x) for x in p[1:]]
            out[p[0]] = (sum(v), sum(v[:4]), v[3] + (v[4] if len(v) > 4 else 0))
    return out


def find_family(patterns):
    """按 cmdline 找进程，返回 {family: [pid, ...]}。"""
    fam = {k: [] for k in patterns}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        pid = int(d)
        c = cmdline(pid)
        if not c:
            continue
        head = c.split(" ", 1)[0]
        base = os.path.basename(head)
        for name, test in patterns.items():
            if test(c, head, base):
                fam[name].append(pid)
    return fam


def family_cpu(fam):
    out = {}
    for name, pids in fam.items():
        tot = 0
        alive = 0
        for pid in pids:
            t = task_cpu_ticks(pid)
            if t is not None:
                tot += t
                alive += 1
        out[name] = {"ticks": tot, "alive": alive, "pids": pids[:8]}
    return out


def audio_threads(pid):
    out = {}
    try:
        tids = os.listdir("/proc/%d/task" % pid)
    except OSError:
        return out
    for tid_s in tids:
        tid = int(tid_s)
        comm = task_comm(pid, tid)
        if comm in ("io", "dsp"):
            out[comm] = {"tid": tid}
            out[comm].update(sched_bits(tid))
    return out


def audio_thread_cpu(pid, threads):
    out = {}
    for name, info in threads.items():
        out[name] = task_cpu_ticks(pid, info["tid"])
    return out


def pct(a, b, hz):
    if a is None or b is None:
        return None
    return round((b - a) / float(hz) * 100.0, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--nbuf", type=int, default=3)
    ap.add_argument("--l", type=int, default=480)
    ap.add_argument("--src", default="mic")
    ap.add_argument("--hz", type=float, default=20.0)
    a = ap.parse_args()

    # 探针自己躲到 CPU1，别在 CPU0 上跟搬样线程抢
    try:
        os.sched_setaffinity(0, {1})
    except OSError:
        pass

    hz = os.sysconf("SC_CLK_TCK")
    tag = a.tag
    jpath = "/dev/shm/drop_%s.json" % tag
    jlpath = "/dev/shm/drop_%s.jsonl" % tag
    trace_path = "/dev/shm/drop_trace_%s.json" % tag
    gap_path = "/dev/shm/drop_gap_%s.json" % tag
    log_path = "/dev/shm/drop_%s.log" % tag

    # 上一趟留下的文件必须先删掉：/dev/shm 是 sticky 目录，且这块板子开了
    # fs.protected_regular=2 —— 文件属主不是自己时，连 root 用 "wb" 打开都会
    # 报 EACCES（只有真正的新建才允许）。所以不能指望 truncate。
    for path in (STATUS, trace_path, gap_path, jpath, jlpath, log_path):
        try:
            os.unlink(path)
        except OSError:
            pass

    env = dict(os.environ)
    env.update({
        "XILINX_XRT": "/usr",
        "PYTHONUNBUFFERED": "1",
        "LIVE_SECONDS": str(a.seconds),
        "LIVE_NBUF": str(a.nbuf),
        "LIVE_L": str(a.l),
        "LIVE_SRC": a.src,
        "LIVE_SCOPE": "1",
        "DROP_TRACE": trace_path,
        "GAP_TRACE": gap_path,
    })

    sys_info = {
        "uptime": read_text("/proc/uptime").split()[0],
        "loadavg": read_text("/proc/loadavg").strip(),
        "governor": read_text(
            "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor").strip(),
        "rt_runtime_us": read_text(
            "/proc/sys/kernel/sched_rt_runtime_us").strip(),
        "rt_period_us": read_text(
            "/proc/sys/kernel/sched_rt_period_us").strip(),
        "nproc": os.cpu_count(),
    }

    patterns = {
        "webui": lambda c, h, b: "fir_webui.py" in c,
        "jupyter": lambda c, h, b: "jupyter" in c,
        "sshd": lambda c, h, b: b == "sshd" or h.startswith("sshd:"),
    }
    fam0 = find_family(patterns)
    fam_aff = {}
    for k, v in fam0.items():
        aff = None
        if v:
            try:
                aff = sorted(os.sched_getaffinity(v[0]))
            except OSError:
                pass
        fam_aff[k] = {"n": len(v), "affinity": aff}
    sys_info["families"] = fam_aff

    try:
        lg = open(log_path, "wb")
    except OSError:
        # 还是打不开（例如别的用户留下的同名文件，sticky 目录删不掉）：
        # 退到一个带 pid 的新名字，别让整趟探针白跑。
        log_path = "%s.%d" % (log_path, os.getpid())
        lg = open(log_path, "wb")

    def _child_aff():
        # 子进程别继承探针的 CPU1 绑定；两条音频线程会自己 _pin
        try:
            os.sched_setaffinity(0, {0, 1})
        except OSError:
            pass

    t_launch = time.time()
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "fir_live.py")],
                         cwd=HERE, env=env, stdout=lg, stderr=subprocess.STDOUT,
                         start_new_session=True, preexec_fn=_child_aff)

    sys_info["child_pid"] = p.pid
    threads = {}
    samples = []
    events = []
    buckets = {}
    last_t_sample = None
    last_lost = None
    last_n = None
    t0 = None
    t_last = None
    cpu_prev = None
    cpu_prev_t = None
    cpu_busy = {"cpu0": [0.0, 0.0], "cpu1": [0.0, 0.0]}   # busy_ticks, total_ticks
    cpu_series = []
    fam_prev = None
    fam_prev_t = None
    fam_series = []
    th_prev = None
    th_prev_t = None
    th_series = []

    deadline = t_launch + a.seconds + 90.0
    exit_seen = None
    while time.time() < deadline:
        now = time.time()
        st = read_status()

        if st and st.get("pid") == p.pid and st.get("t") != last_t_sample:
            last_t_sample = st.get("t")
            if st.get("running"):
                if t0 is None:
                    t0 = float(st["t"])
                t_last = float(st["t"])
                lost = int(st.get("lost_blocks") or 0)
                d_lost = 0 if last_lost is None else max(0, lost - last_lost)
                if d_lost:
                    dt = t_last - t0
                    events.append({
                        "dt": round(dt, 3),
                        "lost": lost,
                        "delta": d_lost,
                        "n": st.get("n"),
                        "io_ms": st.get("io_ms"),
                        "dsp_ms": st.get("dsp_ms"),
                    })
                last_lost = lost
                last_n = st.get("n")
                samples.append({
                    "t": round(t_last, 3),
                    "n": st.get("n"),
                    "lost": lost,
                    "io_ms": st.get("io_ms"),
                    "dsp_ms": st.get("dsp_ms"),
                    "rt": st.get("rt"),
                    "in_rms": st.get("in_rms"),
                    "out_rms": st.get("out_rms"),
                })
                b = str(int((t_last - t0) // 10.0))
                buckets[b] = buckets.get(b, 0) + d_lost

        if not threads:
            threads = audio_threads(p.pid)
            if threads:
                sys_info["threads"] = threads
                th_prev = audio_thread_cpu(p.pid, threads)
                th_prev_t = now

        if now - (cpu_prev_t or 0) >= 1.0:
            cur = cpu_counters()
            if cpu_prev:
                for k in ("cpu0", "cpu1"):
                    if k in cur and k in cpu_prev:
                        d_total = cur[k][0] - cpu_prev[k][0]
                        d_idle = cur[k][2] - cpu_prev[k][2]
                        cpu_busy.setdefault(k, [0, 0])
                        cpu_busy[k][0] += d_total - d_idle
                        cpu_busy[k][1] += d_total
                        cpu_series.append({
                            "dt": round(now - t0, 1) if t0 else None,
                            "cpu": k,
                            "busy_pct": round(
                                100.0 * (d_total - d_idle) / d_total, 1)
                            if d_total else None,
                        })
            cpu_prev = cur
            cpu_prev_t = now

            fam = family_cpu(fam0)
            if fam_prev and fam_prev_t:
                dt_s = now - fam_prev_t
                fam_series.append({
                    "dt": round(now - t0, 1) if t0 else None,
                    "webui_pct": pct(fam_prev["webui"]["ticks"],
                                     fam["webui"]["ticks"], hz * dt_s),
                    "jupyter_pct": pct(fam_prev["jupyter"]["ticks"],
                                       fam["jupyter"]["ticks"], hz * dt_s),
                    "sshd_pct": pct(fam_prev["sshd"]["ticks"],
                                    fam["sshd"]["ticks"], hz * dt_s),
                })
            fam_prev = fam
            fam_prev_t = now

            if threads and th_prev and th_prev_t:
                cur_th = audio_thread_cpu(p.pid, threads)
                dt_s = now - th_prev_t
                row = {"dt": round(now - t0, 1) if t0 else None}
                for k in ("io", "dsp"):
                    row[k + "_pct"] = pct(th_prev.get(k), cur_th.get(k),
                                          hz * dt_s)
                th_series.append(row)
                th_prev = cur_th
                th_prev_t = now

        if p.poll() is not None:
            if exit_seen is None:
                exit_seen = now
            st2 = read_status()
            done = (st2 and st2.get("pid") == p.pid
                    and not st2.get("running"))
            if done or now - exit_seen > 2.0:
                break
        time.sleep(1.0 / a.hz)

    # 还活着就按 Ctrl-C 收工（探针自己会发，不依赖 webui）
    if p.poll() is None:
        try:
            p.send_signal(signal.SIGINT)
        except OSError:
            pass
        try:
            p.wait(timeout=45)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()

    # 等最后一帧（running=false + ok）落盘；fir_live 会在退出前写
    final = None
    for _ in range(50):
        st = read_status()
        if st and st.get("pid") == p.pid and not st.get("running"):
            final = st
            break
        time.sleep(0.1)
    if final is None:
        st = read_status()
        if st and st.get("pid") == p.pid:
            final = st
    lg.close()

    duration = (t_last - t0) if (t0 and t_last) else 0.0
    lost_total = None
    if final is not None and final.get("lost_blocks") is not None:
        lost_total = int(final["lost_blocks"])
    elif last_lost is not None:
        lost_total = last_lost

    # fir_live 的 lost_blocks 是按「本档块长」数出来的，L=960 时一块是 20 ms，
    # 和 L=480 的数字不能直接比。这里统一换成**音频时间缺口**：
    # 处理掉的音频秒数比墙钟少多少。再折算成 10 ms 一块，才是同一个尺子。
    block_sec = float(a.l) / 48000.0
    audio_rate = None
    n_span = None
    if len(samples) >= 2:
        n_span = samples[-1]["n"] - samples[0]["n"]
        dt_span = samples[-1]["t"] - samples[0]["t"]
        if dt_span > 0 and n_span > 0:
            audio_rate = n_span * block_sec / dt_span
    deficit_ms_per_60s = (None if audio_rate is None
                          else round((1.0 - audio_rate) * 60000.0, 1))
    lost_10ms_per_60s = (None if deficit_ms_per_60s is None
                         else round(deficit_ms_per_60s / 10.0, 2))

    gaps = []
    for i in range(1, len(events)):
        gaps.append(round(events[i]["dt"] - events[i - 1]["dt"], 2))

    def stat(xs):
        if not xs:
            return None
        xs = sorted(xs)
        n = len(xs)
        return {"n": n, "min": xs[0], "median": xs[n // 2],
                "p90": xs[min(n - 1, int(n * 0.9))], "max": xs[-1]}

    io_ms = [s["io_ms"] for s in samples if s.get("io_ms") is not None]
    dsp_ms = [s["dsp_ms"] for s in samples if s.get("dsp_ms") is not None]

    # 两块之间那段时间就是 I/O 线程在 Python 里待的时长。I2S 的 FIFO 只能
    # 吸收一百来微秒，超出的部分就是真丢掉的采样 —— 这个数比 lost_blocks
    # 更直接，换块长时也能横着比。
    gap_ms = None
    cycle_ms = None
    try:
        with open(trace_path, "r") as f:
            tr = json.load(f)["trace"]
        gaps = [tr[i + 1][1] - (tr[i][1] + tr[i][2])
                for i in range(len(tr) - 1)]
        if gaps:
            gaps_sorted = sorted(gaps)
            k = len(gaps_sorted)
            gap_ms = {"n": k,
                      "median": round(gaps_sorted[k // 2] * 1e3, 3),
                      "p90": round(gaps_sorted[min(k - 1, int(k * 0.9))] * 1e3, 3),
                      "max": round(gaps_sorted[-1] * 1e3, 3)}
        if len(tr) > 1:
            cycle_ms = round((tr[-1][1] - tr[0][1]) / (len(tr) - 1) * 1e3, 3)
    except (OSError, ValueError, KeyError):
        pass

    # 两块之间的空档拆成四段：等槽位、进 C 之前、出 C 之后到 qput 完、
    # 下一轮循环开头。看是哪一段贵。
    gap_us = None
    try:
        with open(gap_path, "r") as f:
            g = json.load(f)["gap"]
        parts = {"wait_slot": [], "pre_c": [], "post_c": [], "loop": []}
        for k in range(len(g) - 1):
            tg, tg1, t0, t1, t5, _i = g[k]
            parts["wait_slot"].append(tg1 - tg)
            parts["pre_c"].append(t0 - tg1)
            parts["post_c"].append(t5 - t1)
            parts["loop"].append(g[k + 1][0] - t5)
        gap_us = {}
        for k, v in parts.items():
            v = sorted(v) if v else [0.0]
            gap_us[k] = {"median": round(v[len(v) // 2] * 1e6, 1),
                         "p90": round(v[min(len(v) - 1, int(len(v) * 0.9))] * 1e6, 1),
                         "max": round(v[-1] * 1e6, 1)}
    except (OSError, ValueError, KeyError, IndexError):
        pass

    # 丢块是不是每 30 秒一簇：把事件按 dt%30 分到 10 个 3 秒格子里。
    # 均匀分布 = 跟那个 30 秒的 kworker 重扫无关。
    phase_hist = [0] * 10
    for e in events:
        phase_hist[int((e["dt"] % 30.0) // 3.0) % 10] += 1

    # 两块之间的空档拆成四段：等槽位、进 C 之前、出 C 之后到 qput 完、
    # 下一轮循环开头。看是哪一段贵。
    gap_us = None
    try:
        with open(gap_path, "r") as f:
            g = json.load(f)["gap"]
        parts = {"wait_slot": [], "pre_c": [], "post_c": [], "loop": []}
        for k in range(len(g) - 1):
            tg, tg1, t0, t1, t5, _i = g[k]
            parts["wait_slot"].append(tg1 - tg)
            parts["pre_c"].append(t0 - tg1)
            parts["post_c"].append(t5 - t1)
            parts["loop"].append(g[k + 1][0] - t5)
        gap_us = {}
        for k, v in parts.items():
            v = sorted(v) if v else [0.0]
            gap_us[k] = {"median": round(v[len(v) // 2] * 1e6, 1),
                         "p90": round(v[min(len(v) - 1, int(len(v) * 0.9))] * 1e6, 1),
                         "max": round(v[-1] * 1e6, 1)}
    except (OSError, ValueError, KeyError, IndexError):
        pass

    # 丢块是不是每 30 秒一簇：每个事件的 dt 到最近 30 秒整数倍的偏差
    phase30 = []
    for e in events:
        m = e["dt"] % 30.0
        phase30.append(round(min(m, 30.0 - m), 2))

    cpu_pct = {}
    for k, (busy, total) in cpu_busy.items():
        cpu_pct[k] = round(100.0 * busy / total, 1) if total else None

    summary = {
        "tag": tag,
        "seconds": a.seconds,
        "nbuf": a.nbuf,
        "l": a.l,
        "src": a.src,
        "sys": sys_info,
        "child_pid": p.pid,
        "duration": round(duration, 2),
        "status_frames": len(samples),
        "lost_total": lost_total,
        "lost_per_sec": round(lost_total / duration, 4) if duration else None,
        "lost_per_60s": round(lost_total / duration * 60.0, 2) if duration else None,
        "audio_rate": round(audio_rate, 5) if audio_rate else None,
        "deficit_ms_per_60s": deficit_ms_per_60s,
        "lost_10ms_per_60s": lost_10ms_per_60s,
        "io_gap_ms": gap_ms,
        "gap_us": gap_us,
        "cycle_ms": cycle_ms,
        "phase_hist_30s": phase_hist,
        "events": events,
        "event_gaps": gaps,
        "phase30": phase30,
        "buckets_10s": {k: buckets[k] for k in
                        sorted(buckets, key=lambda x: int(x))},
        "io_ms": stat(io_ms),
        "dsp_ms": stat(dsp_ms),
        "cpu_busy_pct": cpu_pct,
        "audio_threads": threads,
        "cpu_series": cpu_series,
        "family_series": fam_series,
        "thread_series": th_series,
        "final_status": final,
    }

    try:
        with open(jpath, "w") as f:
            json.dump(summary, f)
        with open(jlpath, "w") as f:
            for s in samples:
                f.write(json.dumps(s) + "\n")
    except OSError:
        # 同上：落到唯一文件名，汇总至少还能从 stdout 拿走
        jpath = "%s.%d" % (jpath, os.getpid())
        jlpath = "%s.%d" % (jlpath, os.getpid())
        with open(jpath, "w") as f:
            json.dump(summary, f)
        with open(jlpath, "w") as f:
            for s in samples:
                f.write(json.dumps(s) + "\n")

    print("DROP_SUMMARY " + json.dumps(summary, ensure_ascii=True))


if __name__ == "__main__":
    main()
