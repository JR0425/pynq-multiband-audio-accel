# ⚠ 2026-10-16 收尾补:**下面这段"背景"是按当时的假设写的,而那份假设当天就被推翻了。**
#   实测 sched_rt_runtime_us 就是默认 950000(护栏在),而把实时优先级整个关掉
#   (LIVE_RT_IO=0 LIVE_RT_DSP=0)照样挂 —— 所以"RT 限流被关掉 ⇒ 完整解释"不成立。
#   脚本本身仍然有用(它读到的那些数是真的),只是别照它开头那段话下结论。
#   完整经过:report/llm_collab_log/2026-10-16_board_hang_rcu_stall.md
"""只读探针:板子上"实时线程会不会把内核饿死"这件事,到底有没有护栏。

2026-10-16 深夜六的栈证据(`tools/_tmp/round6/console.log`,`panic_print=31` 打出来的
全任务转储)把问题指到了这个方向:

  - 挂的那一下,CPU0 上跑的正是 `fir_live.py` 的**搬样线程**(pid 922),
    它的栈尾是 `__irq_usr`,CPSR 低 5 位 = 0x10 = **用户态** —— 也就是说
    它不是"卡在某次 AXI 读上",就是在**用户态空转**
  - 同一份转储里 `ksoftirqd/0` 和一堆 `kworker/0:*`(**含两个标注 mmc_complete 的**)
    全是 `R running task` —— **可运行却一直没轮上**
  - RCU 报的是 `softirq=x/x`(整段停滞里 RCU_SOFTIRQ 一次都没跑过)

`fir_live_board.py:228` 把两条线程设成 SCHED_FIFO(搬样 20 / 过核 10),注释里写的
理由是"比硬中断低(**板上内核线程多在 50**)"。**这个前提是错的**:`ksoftirqd/N` 和
`kworker/*` 是**普通 CFS 任务**(nice 0;HIGHPRI 那个是 nice -20),任何一个 RT
线程都压得过它们。板上只有 `irq/N-*` 这类线程化中断才是 FF 50。

唯一能救这个设计的护栏是 **RT 限流**(`sched_rt_runtime_us`,默认 950000 =
每秒最多给实时线程 95%,剩下 50 ms 留给内核)。**如果这个值是 -1(限流关掉),
那"RT 线程永不阻塞 ⇒ 同一颗核的内核杂务全饿死 ⇒ RCU 判卡死"就是完整的解释。**

所以这一遍只读四件事:
  1. `sched_rt_runtime_us` / `sched_rt_period_us` 现在是多少
  2. 板上**所有** FF/RR 线程是哪些、优先级多少(看 irq 线程是不是 50)
  3. `/proc/sched_debug` 有没有(CONFIG_SCHED_DEBUG),有的话直接读每核
     rt_rq 的 `rt_time`/`rt_runtime`/`rt_nr_running` —— 限流行没行一眼可见
  4. 相关的编译选项

**全是只读命令,一个写都没有。**

用法:
  python rt_probe.py                # 输出到 tools/_tmp/round6/rt_probe.txt
  python rt_probe.py <输出目录>
"""

import os
import shlex
import sys
import time

import paramiko

HOST, USER, PASSWD = "192.168.2.99", "xilinx", "xilinx"

BUNDLE = r'''
echo "== uptime / load =="
cat /proc/uptime; cat /proc/loadavg
echo "== RT 限流(默认 950000;写 -1 就是关掉护栏)=="
printf 'sched_rt_runtime_us='; cat /proc/sys/kernel/sched_rt_runtime_us
printf 'sched_rt_period_us='; cat /proc/sys/kernel/sched_rt_period_us
ls -l /proc/sys/kernel/sched_rt_runtime_us /proc/sys/kernel/sched_rt_period_us 2>&1
echo "== 板上所有 FF/RR(实时)线程:谁在跑实时、优先级多少 =="
ps -eLo pid,tid,cls,rtprio,psr,pcpu,comm 2>&1 | awk 'NR==1 || $3=="FF" || $3=="RR"' | head -40
echo "== 内核线程的调度类(证明 ksoftirqd/kworker 是普通类)=="
ps -eLo pid,cls,rtprio,psr,comm 2>&1 | grep -E "ksoftirqd|kworker|irq/|rcu_" | head -30
echo "== /proc/sched_debug 在不在(SCHED_DEBUG)="
ls -l /proc/sched_debug 2>&1
echo "-- 有的话:每核 rt_rq 的账本 --"
grep -E "^(cpu#|.*\.rt_nr_running|.*\.rt_time|.*\.rt_runtime|.*\.rt_throttled|.*rt_pushable)" /proc/sched_debug 2>&1 | head -40
echo "== 相关编译选项 =="
zcat /proc/config.gz | grep -iE "^CONFIG_(RT_GROUP_SCHED|SCHED_DEBUG|SCHEDSTATS|SCHED_AUTOGROUP|PREEMPT|PREEMPT_VOLUNTARY|NO_HZ|HZ|RCU_NOCB|RCU_BOOST|TREE_RCU|PREEMPT_RCU|IRQ_FORCED_THREADING)="
echo "== 中断线程化了吗 =="
cat /sys/module/*/parameters/threadirqs 2>/dev/null
ps -eLo pid,cls,rtprio,comm 2>&1 | grep "irq/" | head -12
echo "== END rt_probe =="
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
    dest = os.path.join(outdir, "rt_probe.txt")
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
