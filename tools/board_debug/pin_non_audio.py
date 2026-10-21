#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pin_non_audio.py —— 把非音频进程的每个线程钉到 CPU1。**只在板子上跑。**

    sudo /usr/local/share/pynq-venv/bin/python3 pin_non_audio.py apply
    sudo /usr/local/share/pynq-venv/bin/python3 pin_non_audio.py restore
    sudo /usr/local/share/pynq-venv/bin/python3 pin_non_audio.py status

CPU0 只留给搬样线程（它是 I2S 的时钟，余量为零）。sshd / Jupyter /
fir_webui 的线程留在 CPU0 上就会和它抢，表现为丢块。

第一次 `apply` 会把每个线程原来的亲和性存进
`/dev/shm/non_audio_affinity_backup.json`，`restore` 按它改回去。
重复 `apply` 不会覆盖第一份备份。
"""

import json
import os
import sys

BACKUP = "/dev/shm/non_audio_affinity_backup.json"
CPU1 = {1}

PATTERNS = ("fir_webui.py", "jupyter", "ipykernel", "sshd")


def cmdline(pid):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def match(pid):
    c = cmdline(pid)
    if not c:
        return False
    head = c.split(" ", 1)[0]
    base = os.path.basename(head)
    if base == "sshd" or head.startswith("sshd:"):
        return True
    return any(p in c for p in ("fir_webui.py", "jupyter", "ipykernel"))


def pids():
    out = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            pid = int(d)
            if pid != os.getpid() and match(pid):
                out.append(pid)
    return out


def tasks(pid):
    try:
        return [int(x) for x in os.listdir("/proc/%d/task" % pid)]
    except OSError:
        return []


def load_backup():
    try:
        with open(BACKUP, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_backup(d):
    tmp = BACKUP + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, BACKUP)


def apply():
    backup = load_backup()
    changed = 0
    failed = 0
    for pid in pids():
        for tid in tasks(pid):
            key = "%d/%d" % (pid, tid)
            try:
                old = sorted(os.sched_getaffinity(tid))
                if key not in backup:
                    backup[key] = old
                if old != sorted(CPU1):
                    os.sched_setaffinity(tid, CPU1)
                    changed += 1
            except OSError:
                failed += 1
    save_backup(backup)
    print("pinned_threads=%d failed=%d backup=%d" %
          (changed, failed, len(backup)))


def unpin():
    """把所有匹配线程放回两颗核 —— 做「不钉」对照前用。"""
    changed = 0
    failed = 0
    for pid in pids():
        for tid in tasks(pid):
            try:
                os.sched_setaffinity(tid, {0, 1})
                changed += 1
            except OSError:
                failed += 1
    print("unpinned_threads=%d failed=%d" % (changed, failed))


def restore():
    backup = load_backup()
    changed = 0
    missing = 0
    for key, mask in backup.items():
        pid_s, tid_s = key.split("/")
        try:
            os.sched_setaffinity(int(tid_s), set(mask))
            changed += 1
        except OSError:
            missing += 1
    print("restored_threads=%d missing=%d" % (changed, missing))


def status():
    rows = []
    for pid in pids():
        for tid in tasks(pid):
            try:
                aff = sorted(os.sched_getaffinity(tid))
            except OSError:
                continue
            rows.append("%d/%d %s" % (pid, tid, aff))
    print("threads=%d" % len(rows))
    for r in rows[:40]:
        print(r)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in (
            "apply", "restore", "unpin", "status"):
        raise SystemExit(__doc__)
    {"apply": apply, "restore": restore, "unpin": unpin,
     "status": status}[sys.argv[1]]()


if __name__ == "__main__":
    main()
