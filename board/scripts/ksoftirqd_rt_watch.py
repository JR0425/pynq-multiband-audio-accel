#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每 30 秒把 ksoftirqd / kworker 提到 SCHED_FIFO 50。**在板子上以 root 常驻。**

为什么不继续用 systemd timer + shell 脚本：

timer 每跑一次，systemd 自己要往 journald 写四行（Starting/Succeeded/
Finished，`LogLevelMax=` 管不到这些），journald 把日志落在 SD 卡上。刷卡的
`kworker/0:1H-mmc_complete` 是 FIFO 50，在 CPU0 上会直接抢搬样线程（FIFO 20）。
600 秒那一趟里，丢块时间戳正好对在 timer 的启动和结束时刻上，每次 5~90 ms。

这个进程只在内存里扫 /proc、调 sched_setscheduler，不 fork、不写日志、
不碰磁盘。扫描间隔和原来一样是 30 秒，所以新 kworker 的提权时延不变。

用法（安装脚本会做）：
    /usr/bin/python3 /usr/local/sbin/ksoftirqd_rt_watch.py
"""

import os
import sys
import time

INTERVAL = float(os.environ.get("RT_REFRESH_SEC", "30"))
PRIO = int(os.environ.get("RT_PRIO", "50"))
WARN_EVERY = 600.0

last_warn = [0.0]


def scan():
    """返回 (提权成功数, 失败数)。"""
    ok = 0
    bad = 0
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        pid = int(d)
        try:
            with open("/proc/%d/comm" % pid) as f:
                comm = f.read().strip()
        except OSError:
            continue
        if not (comm.startswith("ksoftirqd/")
                or comm.startswith("kworker/0:")
                or comm.startswith("kworker/1:")):
            continue
        try:
            if os.sched_getscheduler(pid) == os.SCHED_FIFO:
                continue
            os.sched_setscheduler(pid, os.SCHED_FIFO, os.sched_param(PRIO))
            ok += 1
        except OSError:
            bad += 1
    return ok, bad


def main():
    while True:
        ok, bad = scan()
        now = time.time()
        if bad and now - last_warn[0] > WARN_EVERY:
            last_warn[0] = now
            # 只有失败才留一行；正常时完全静默（不产生 SD 写）
            sys.stderr.write("ksoftirqd_rt_watch: %d 个线程提权失败\n" % bad)
            sys.stderr.flush()
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
