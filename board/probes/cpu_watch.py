#!/usr/bin/env python3
"""在板上采样 CPU 占用（主机关 SSH 只发一条命令）。

    python3 cpu_watch.py <秒> <输出 CSV>

每秒一行，列：
    t, 进程 jiffies, 系统单核总 jiffies, idle+iowait jiffies,
    io 线程 jiffies, dsp 线程 jiffies, 主线程 jiffies

进程按 cmdline 里的 `fir_live.py` 找。找不到（还没起来）就空着等。
"""

import os
import sys
import time

CLK = os.sysconf("SC_CLK_TCK")


def read_cpu_line():
    with open("/proc/stat") as f:
        for line in f:
            if line.startswith("cpu "):
                v = [int(x) for x in line.split()[1:]]
                total = sum(v[:8])
                idle = v[3] + v[4]
                return total, idle
    return 0, 0


def proc_stat(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            s = f.read()
        # 第 2 个字段是带括号的 comm，里面可能有空格 → 从最后一个 ')' 之后切
        rest = s[s.rindex(")") + 2:].split()
        return int(rest[11]) + int(rest[12])       # utime + stime
    except (OSError, ValueError):
        return None


def thread_stats(pid):
    out = {}
    try:
        tids = os.listdir("/proc/%d/task" % pid)
    except OSError:
        return out
    for tid in tids:
        try:
            with open("/proc/%d/task/%s/stat" % (pid, tid)) as f:
                s = f.read()
        except OSError:
            continue
        rest = s[s.rindex(")") + 2:].split()
        name = s[s.index("(") + 1:s.rindex(")")]
        out[name] = int(rest[11]) + int(rest[12])
    return out


def find_pid():
    """找 python3 跑的 fir_live.py，多个就取最新的（pid 最大）。

    不能只匹配 `fir_live.py`：sudo 包的 `sh -c "... fir_live.py"` 外壳命令行里
    也有这几个字，先撞上它就统计成 0% 了（2026-10-22 就是这么错的）。
    也不能只找一次：上一趟残留的进程会在这时退出，之后整张表都是空的。
    """
    best = None
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % d, "rb") as f:
                parts = f.read().split(b"\0")
        except OSError:
            continue
        if len(parts) < 2:
            continue
        exe = parts[0].decode("utf-8", "replace")
        args = b" ".join(parts[1:]).decode("utf-8", "replace")
        if exe.endswith("python3") and "fir_live.py" in args:
            best = int(d)
    return best


def main():
    secs = float(sys.argv[1])
    path = sys.argv[2]
    t_end = time.time() + secs
    pid = None
    with open(path, "w") as f:
        f.write("t,proc,total,idle,io,dsp\n")
        while time.time() < t_end:
            p = proc_stat(pid) if pid else None
            if p is None:              # 还没起来，或者这是一趟残留进程刚退出
                pid = find_pid()
                p = proc_stat(pid) if pid else None
            th = thread_stats(pid) if pid else {}
            tot, idle = read_cpu_line()
            f.write("%.3f,%s,%d,%d,%s,%s\n" % (
                time.time(), "" if p is None else p, tot, idle,
                th.get("io", ""), th.get("dsp", "")))
            f.flush()
            time.sleep(1.0)


if __name__ == "__main__":
    main()
