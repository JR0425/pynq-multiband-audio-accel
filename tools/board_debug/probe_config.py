# ⚠ 2026-10-16 收尾补:主机侧。只读为主,**唯一的写是 `kernel.panic_print=31`**。
#   它确认了 panic_print 在这块板上写得进去(返回 31)—— 这是后来能抓到栈的前提。
"""板子活着时先问清楚"抓栈到底有什么工具" —— 只读为主,唯一一次写是 panic_print。

背景:串口里已经录到 **5 次 panic,一次栈都没有**。原因找到了:
`panic_on_rcu_stall` 在 `rcu_dump_cpu_stacks()` **之前**就 panic,所以卡住那颗核
的栈永远打不出来。手册上还留着两条可能的路,这一遍就是把它们的开关摸清楚:

  1. `panic_print` —— panic 时把**所有任务**的状态+栈打出来(等价 sysrq-t)。
     它走的是 panic 处理早期的 `panic_print_sys_info()`,不受 rcu 那条顺序影响。
     能不能用、哪些位能写进去,直接试。
  2. `/proc/<pid>/stack` —— 能不能读到某个任务的**内核栈**(要 CONFIG_STACKTRACE)。

顺带把两件一直没确认的事定下来:
  - `nohz_full` 有没有开(如果 CPU0 在 nohz_full 的岛上,用户态空转不报 QS,
    那这个 stall 就是**误报**,不是真死)
  - cpufreq governor(启动日志里有"LSB: set CPUFreq kernel parameters")

用法:
  python probe_config.py                # 直接用默认输出名
  python probe_config.py <输出目录>
"""

import os
import shlex
import sys
import time

import paramiko

HOST, USER, PASSWD = "192.168.2.99", "xilinx", "xilinx"

BUNDLE = r'''
echo "== uptime =="
cat /proc/uptime
echo "== cmdline =="
cat /proc/cmdline
echo "== nohz_full =="
cat /sys/devices/system/cpu/nohz_full 2>&1
ls -d /sys/devices/system/cpu/nohz* 2>&1
echo "== isolcpus / 核 =="
cat /sys/devices/system/cpu/isolated 2>&1
cat /sys/devices/system/cpu/online 2>&1
echo "== cpufreq =="
for d in /sys/devices/system/cpu/cpu*/cpufreq; do
  [ -d "$d" ] || continue
  echo "$d governor=$(cat $d/scaling_governor 2>&1) cur=$(cat $d/scaling_cur_freq 2>&1) min=$(cat $d/scaling_min_freq 2>&1) max=$(cat $d/scaling_max_freq 2>&1) drv=$(cat $d/scaling_driver 2>&1)"
done
echo "== rcu 可调参数(能不能写)=="
ls -l /sys/module/rcupdate/parameters/ 2>&1
for f in /sys/module/rcupdate/parameters/*; do printf '%s=' "$f"; cat "$f" 2>&1; done
echo "== rcu stall 相关 sysctl =="
ls /proc/sys/kernel/ | grep -iE "rcu|stall|panic|hung|lockup|sysrq|task|ftrace"
echo "== panic_print 当前值,再试着写 31 =="
printf 'now='; cat /proc/sys/kernel/panic_print
sysctl -w kernel.panic_print=31 2>&1
printf 'after='; cat /proc/sys/kernel/panic_print
sysctl -w kernel.panic_print=0 > /dev/null 2>&1
printf 'restored='; cat /proc/sys/kernel/panic_print
echo "== 关键编译选项 =="
zcat /proc/config.gz | grep -iE "^CONFIG_(STACKTRACE|ARM_UNWIND|UNWINDER|FRAME_POINTER|RCU_CPU_STALL_TIMEOUT|NO_HZ|NO_HZ_FULL|DEBUG_LOCK_ALLOC|PROVE_LOCKING|LOCKDEP|SOFTLOCKUP_DETECTOR|DETECT_HUNG_TASK|BOOTPARAM_SOFTLOCKUP|MAGIC_SYSRQ|SCHED_DEBUG|SCHEDSTATS|DEBUG_STACK_USAGE|LOCKUP_DETECTOR|RCU_BOOST|PREEMPT|HZ|DEBUG_INFO)="
echo "== /proc/<pid>/stack 能不能读 =="
ls -l /proc/1/stack 2>&1
head -6 /proc/1/stack 2>&1
echo "== self/stack =="
head -6 /proc/self/stack 2>&1
echo "== printk 阈值 =="
cat /proc/sys/kernel/printk
echo "== END probe_config =="
'''


def connect(timeout=20):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWD,
              timeout=timeout, banner_timeout=timeout, auth_timeout=timeout)
    return c


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "tools/_tmp/round6"
    os.makedirs(outdir, exist_ok=True)
    dest = os.path.join(outdir, "probe_config.txt")
    c = connect()
    try:
        cmd = "echo xilinx | sudo -S sh -c " + shlex.quote(BUNDLE)
        _i, out, err = c.exec_command(cmd, timeout=180)
        data = out.read().decode("utf-8", "replace") + \
            err.read().decode("utf-8", "replace")
    finally:
        c.close()
    with open(dest, "w", encoding="utf-8") as f:
        f.write(data)
    print(data)
    print("[%s] -> %s (%d bytes)" % (time.strftime("%H:%M:%S"), dest, len(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
