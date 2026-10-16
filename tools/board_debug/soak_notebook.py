# ⚠ 2026-10-16 收尾补:**已被 next_round.py 取代,留作参考。**
#   它起的是 hangmon3.sh(会写 SD 的那一版)、抓的是 /home/xilinx/hangmon_sd.log。
#   接着做 soak 请用 next_round.py + hangmon4.sh。
"""把 notebook 那条路反复走,数出「多少次里挂一次」。

为什么要放在主机上跑,而不是板子上:
    板子一挂就重启,板子上的脚本跟着一起没了,数不下去。
    主机这边不怕 —— 连不上就记一次 FAIL,等它起来接着数。

每一轮做的事和 notebook 一模一样:
    起 fir_live.py(wav + scope + 永不退) → 等它稳 → 写一次旋钮文件 → 等它应用
    → 发 SIGINT(等于点「停止」)→ 看它有没有活着退出来。

挂了的那一轮,顺手把两块黑匣子抢下来:
    /home/xilinx/hangmon_sd.log  每 6 秒整份落 SD 卡,断电还在
    /dev/shm/hangmon.log         内存盘,能抢到就抢

用法: python soak_notebook.py <轮数> <输出目录>
"""

import os
import re
import sys
import time

import paramiko

HOST, USER, PASSWD = "192.168.2.99", "xilinx", "xilinx"
PER_RUN_TIMEOUT = 260          # 一轮本身 ~100 秒;给它留够,挂了也要等得到异常
MON_START = (
    "pkill -f 'hangmo[n]3' 2>/dev/null; sleep 1; "
    "nohup setsid sh /home/xilinx/hangmon3.sh >/dev/null 2>&1 </dev/null & "
    "sleep 2; pgrep -f 'hangmon3' | head -1"
)


def log(outdir, msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line)
    sys.stdout.flush()
    with open(os.path.join(outdir, "soak_host.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def connect(timeout=15):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWD,
              timeout=timeout, banner_timeout=timeout, auth_timeout=timeout)
    return c


def run(c, cmd, timeout):
    _in, out, err = c.exec_command(cmd, timeout=timeout)
    data = out.read().decode("utf-8", "replace")
    return data + err.read().decode("utf-8", "replace")


def ssh_once(cmd, timeout=60):
    c = connect()
    try:
        return run(c, cmd, timeout)
    finally:
        c.close()


def up_now():
    try:
        ssh_once("cat /proc/uptime", timeout=20)
        return True
    except Exception:                                            # noqa: BLE001
        return False


def wait_up(outdir, mins=6.0):
    """挂了以后等它自己重启回来(panic=15,大约 75 秒)。"""
    t0 = time.time()
    n = 0
    while time.time() - t0 < mins * 60:
        n += 1
        if up_now():
            log(outdir, "  board back after %ds (%d probes)" % (time.time() - t0, n))
            time.sleep(20)                       # 等 sshd/服务起齐
            return True
        time.sleep(10)
    log(outdir, "  board did NOT come back in %d min" % mins)
    return False


def grab(outdir, tag):
    """把黑匣子抢下来。板子刚起来也在所不惜 —— 这些文件在 SD 上。"""
    try:
        data = ssh_once(
            "echo '##### sd'; tail -600 /home/xilinx/hangmon_sd.log 2>&1; "
            "echo '##### sdhead'; head -1 /home/xilinx/hangmon_sd.log 2>&1; "
            "echo '##### shm'; tail -80 /dev/shm/hangmon.log 2>&1; "
            "echo '##### uptime'; cat /proc/uptime; "
            "echo '##### END'", timeout=90)
        p = os.path.join(outdir, "grab_%s.txt" % tag)
        with open(p, "w", encoding="utf-8") as f:
            f.write(data)
        log(outdir, "  grabbed -> %s (%d bytes)" % (p, len(data)))
    except Exception as e:                                       # noqa: BLE001
        log(outdir, "  grab failed: %s: %s" % (type(e).__name__, e))


def main():
    n_runs = int(sys.argv[1])
    outdir = sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    log(outdir, "SOAK BEGIN runs=%d" % n_runs)

    n_pass = n_fail = 0

    for i in range(1, n_runs + 1):
        log(outdir, "--- run %d/%d ---" % (i, n_runs))
        try:
            mon = ssh_once(MON_START, timeout=60).strip()
            log(outdir, "  monitor pid=%s" % (mon or "?"))
        except Exception as e:                                   # noqa: BLE001
            log(outdir, "  monitor start failed: %s" % e)

        verdict = None
        try:
            c = connect()
            try:
                data = run(c, "sh /home/xilinx/repro_stop.sh INT 8", PER_RUN_TIMEOUT)
            finally:
                c.close()
            if "=== DONE ===" in data and "END alive=n" in data:
                verdict = "PASS"
            elif "=== DONE ===" in data:
                verdict = "PASS_LIMP"        # 走完了但进程没收干净
            else:
                verdict = "SUSPECT"
            with open(os.path.join(outdir, "run_%02d.txt" % i), "w",
                      encoding="utf-8") as f:
                f.write(data)
            m = re.search(r"status_after_tune=.*?out_rms\": ([0-9.]+).*?in_rms\": ([0-9.]+)",
                          data)
            if m:
                log(outdir, "  tuned out_rms=%s in_rms=%s" % (m.group(1), m.group(2)))
        except Exception as e:                                   # noqa: BLE001
            verdict = "HANG"
            log(outdir, "  ssh broke: %s: %s" % (type(e).__name__, e))

        if verdict == "HANG":
            n_fail += 1
            log(outdir, "  >>> HANG (board went down)")
            if wait_up(outdir):
                grab(outdir, "after_hang_%02d" % i)
        else:
            n_pass += 1
            log(outdir, "  >>> %s" % verdict)

        log(outdir, "  tally: pass=%d fail=%d" % (n_pass, n_fail))

    log(outdir, "SOAK DONE  pass=%d fail=%d  (%d runs)"
        % (n_pass, n_fail, n_runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
