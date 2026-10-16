#!/bin/sh
# ⚠ 2026-10-16 收尾补:这一份是**板上**跑的驱动。$1 是一串 *_*_* 环境变量开关
#   (IDLE / DUMP / FASTSTALL / NOJRNL / WD / IRQ1 / NOSIG / NOTUNE / STOP9 / NOBOX),
#   跑完打一行 RESULT=done。**"挂没挂"的判断放在主机侧做** —— 板子一挂这个脚本就跟着没了。
#   配置名 -> 环境变量串 的对照表在 next_round.py 的 CONFIGS。
#   每轮的结果、以及哪些配置被证明无效,见 report/llm_collab_log/2026-10-16_board_hang_rcu_stall.md
#   和 data/results/board_hang_evidence_20261016.md。
# 走一遍「notebook 那条路」:起实时 → 等它稳 → 拖一次滑杆 → 点停止。
#
#   $1 = 传给 fir_live.py 的额外环境变量(会盖掉前面的默认值)
#   $2 = 发完 SIGINT 等几秒(默认 8,和 notebook 里 p.wait(timeout=8) 一样)
#
# 唯一的产出是最后那行 RESULT=done。
# **挂了的判断在主机那边做**:板子一挂就重启,这个脚本跟着没了,
# 主机收不到 RESULT=done 就是挂了 —— 比在板子上自己判可靠。
#
# 环境变量必须写在 sudo 里面:sudo 默认 env_reset,外面 export 的全丢,
# XILINX_XRT 一丢 PYNQ 就报 "No Devices Found"。

EXTRA="${1:-LIVE_SRC=wav LIVE_SCOPE=1}"
WAITS="${2:-8}"
# NOSIG=1 时最多等它自己跑完几秒 —— 要盖过 LIVE_SECONDS。可用 SELFW=NNN 覆盖。
SELFW=$(echo " $EXTRA " | sed -n 's/.* SELFW=\([0-9][0-9]*\) .*/\1/p')
[ -n "$SELFW" ] || SELFW=400
DRV=/home/xilinx/one_run.log

cd /home/xilinx/jupyter_notebooks || exit 1

rm -f /dev/shm/fir_tune.json /dev/shm/fir_status.json /dev/shm/fir_scope.bin 2>/dev/null
: > "$DRV"
: > /dev/shm/one_run_out.log

echo "BEGIN up=$(cut -d. -f1 /proc/uptime) extra=[$EXTRA]" >> "$DRV"
# SD 卡从头到尾被写了多少:/proc/diskstats 里 mmcblk0 的第 8 列=写次数、第 10 列=写的扇区、
# 第 11 列=花在写上的毫秒。跑前跑后各记一次,一减就知道这几百秒里 SD 到底挨了多少笔。
echo "io_begin $(awk '$3=="mmcblk0"{print "wr_ios="$8" wr_sect="$10" wr_ms="$11}' /proc/diskstats)" >> "$DRV"

# IDLE=1:**这一轮什么都不跑** —— 不起 fir_live.py、不加载 overlay、不碰 PL,
# 只让板子闲着 IDLE_S 秒(默认 300),然后报 RESULT=done。
#
# 为什么非要有这一条:目前**所有**"挂"的记录都在跑管线,所以"是管线把板子弄挂的"
# 和"板子自己在坏"这两件事**从来没分开过**。这一臂就是那个对照组:
#   · IDLE 活 300 秒 + 管线挂  ⇒ 锁定在管线/PL 这条路上
#   · IDLE 也挂                ⇒ 和我们一行代码都无关,是机器自己(供电/PL/SD 卡)
# 放在最前面,是为了让它连一个寄存器都不碰。
case " $EXTRA " in
  *" IDLE=1 "*)
    IDLE_S=$(echo " $EXTRA " | sed -n 's/.* IDLE_S=\([0-9][0-9]*\) .*/\1/p')
    [ -n "$IDLE_S" ] || IDLE_S=300
    echo "idle_begin up=$(cut -d. -f1 /proc/uptime) seconds=$IDLE_S" >> "$DRV"
    sleep "$IDLE_S"
    echo "idle_end up=$(cut -d. -f1 /proc/uptime)" >> "$DRV"
    echo "ALIVE_AFTER_STOP=n"
    echo "io_end $(awk '$3=="mmcblk0"{print "wr_ios="$8" wr_sect="$10" wr_ms="$11}' /proc/diskstats) $(grep -E '^(nr_dirty|nr_writeback)' /proc/vmstat | tr '\n' ' ')" >> "$DRV"
    grep -E 'idle_|io_begin|BEGIN' "$DRV"
    echo "RESULT=done"
    exit 0
    ;;
esac

# WD=1:起硬件看门狗守卫(见 wd_guard.sh)。它只在 fir_live.py 活着时武装并喂狗,
# 所以"双核全死"那种连 panic 都打不出来的挂法,10 秒后会被 SWDT 复位拉回来 ——
# 这是唯一能自救的路。守卫的输出单独进 wd_guard.log,免得混进 one_run.log。
case " $EXTRA " in
  *" WD=1 "*)
    echo xilinx | sudo -S sh -c \
      'nohup sh /home/xilinx/wd_guard.sh > /dev/shm/wd_guard.log 2>&1 & echo $!'
    # 等它把能力探测跑完(nowayout / identity 那几行)
    sleep 2
    echo "wd_probe: $(ls /dev/watchdog 2>&1) state=$(cat /sys/class/watchdog/watchdog0/state 2>&1) timeout=$(cat /sys/class/watchdog/watchdog0/timeout 2>&1) nowayout=$(cat /sys/class/watchdog/watchdog0/nowayout 2>&1) id=$(cat /sys/class/watchdog/watchdog0/identity 2>&1)" >> "$DRV"
    ;;
esac

# IRQ1=1:把 mmc/eth/uart 的中断从 CPU0 挪到 CPU1(掩码 2 = 只有 CPU1)。
# 搬样线程零余量地占着 CPU0,而这些中断默认全砸在 CPU0 上。
case " $EXTRA " in
  *" IRQ1=1 "*)
    echo xilinx | sudo -S sh -c \
      'for n in 26 28 29; do [ -w /proc/irq/$n/smp_affinity ] && echo 2 > /proc/irq/$n/smp_affinity; done'
    echo "IRQ1 已挪: $(grep -E 'xuartps|eth0|mmc0' /proc/interrupts | tr -s ' ')" >> "$DRV"
    ;;
esac

# NOJRNL=1:把 SD 卡从这一段里彻底摘掉 —— 停 journald(连同它的 socket,**再 runtime-mask
# 掉,不然它会被重新拉起**)、停掉所有会写盘的 daily 定时器、把积压的脏页一次 sync 干净、
# 并把内核自己的周期回刷(默认每 5 秒一次)关掉。目标是这 300 秒里**内核没有任何理由碰 SD**。
#
# ⚠ 2026-10-16 深夜(round6) 修正:以前这一臂只 `stop`,结果跑起来时 journald 是 `activating`
# (socket 又把它拉回来了),那一轮**根本没静音**,数据作废。现在:stop + mask --runtime,
# 并且**打两次点** —— 起跑前一次、settle 10 秒后再一次(`nojrnl_check`,下面),
# 那次会带上 `wr_ios`/`wr_ms`,直接回答"这 10 秒里到底往 SD 写了没有"。
# 判读方法:拿 `injrnl_check` 和 `io_begin` 相减,差值≈0 才算这一臂干净。
#
# 为什么值得单独做这一臂:2026-10-16 深夜(round6) 的全任务转储证明挂的机制是
# **CPU0 上一条不肯让出的 SCHED_FIFO 线程把内核的延迟工作饿死**(ksoftirqd/0 被
# "可运行却调度不上"),而这个 5% 的窗口是被 nice -20 的 mmc 工作抢走的 —— SD 越忙,
# 窗口越轮不到 RCU。所以"把 SD 静音"应该是能让**实时优先级保持不变**的那条路。
case " $EXTRA " in
  *" NOJRNL=1 "*)
    echo xilinx | sudo -S sh -c 'systemctl stop systemd-journald.socket systemd-journald.service 2>/dev/null;
      systemctl mask --runtime systemd-journald.service systemd-journald.socket >/dev/null 2>&1;
      for t in apt-daily.timer apt-daily-upgrade.timer man-db.timer fstrim.timer \
               e2scrub_all.timer logrotate.timer systemd-tmpfiles-clean.timer; do
        systemctl stop $t 2>/dev/null;
        systemctl mask --runtime $t >/dev/null 2>&1;
      done;
      sync;
      sysctl -w vm.dirty_writeback_centisecs=60000 vm.dirty_expire_centisecs=600000 > /dev/null'
    echo "nojrnl: journald=$(systemctl is-active systemd-journald 2>&1) masked=$(systemctl is-enabled systemd-journald 2>&1) wbcs=$(sysctl -n vm.dirty_writeback_centisecs) timers_left=$(systemctl list-timers --all --no-pager 2>/dev/null | grep -cE 'apt-daily|man-db|fstrim|e2scrub|logrotate|tmpfiles' 2>&1) $(awk '$3=="mmcblk0"{print "wr_ios="$8" wr_sect="$10" wr_ms="$11}' /proc/diskstats)" >> "$DRV"
    ;;
esac

# (以前这里有个 HUNG=1,靠 `kernel.hung_task_timeout_secs` 抓 D 状态任务的栈。
#  2026-10-16 查板子的 /proc/config.gz:**CONFIG_DETECT_HUNG_TASK 没编**,
#  `/proc/sys/kernel/hung_task_timeout_secs` 根本不存在,整条路是死的,已删。)

# DUMP=1:panic 那一下把现场一次打干净 —— **这是目前唯一还能拿到栈的路**。
#
# 为什么非要绕:板上已经录到 5 次 panic、**0 次栈**。查过 /proc/config.gz ——
# 没有 hung_task、没有 softlockup、**没有 sysrq**(所以串口 BREAK 也不行)、
# 没有 ftrace、连 /proc/<pid>/stack 都没有(CONFIG_STACKTRACE 没编),
# 而 `panic_on_rcu_stall=1` 是在 `rcu_dump_cpu_stacks()` **之前**就 panic 的,
# 所以卡住那颗核的栈永远打不出来。`panic_print` 走的是 panic 处理**早期**的
# `panic_print_sys_info()`,不受那条顺序影响 —— 位 0 = 把所有任务的状态 + 内核栈
# 打出来(等价按了 sysrq-t)。板上实测 31 写得进去(位 0/1/2/8 都支持)。
#
# **`panic_print=31` 只影响 panic 时"打什么",不改任何行为**,所以它可以一直开着
# (2026-10-16 深夜(round6) 起:做 A/B 对照的每一臂都开,顺便给下一次死亡留现场)。
# **跑它的时候串口录制必须开着**,否则打出来了也没人接。
case " $EXTRA " in
  *" DUMP=1 "*)
    echo xilinx | sudo -S sh -c 'sysctl -w kernel.panic_print=31 > /dev/null'
    echo "dump: panic_print=$(sysctl -n kernel.panic_print) stall_timeout=$(cat /sys/module/rcupdate/parameters/rcu_cpu_stall_timeout 2>&1) rcu_panic=$(sysctl -n kernel.panic_on_rcu_stall) panic=$(sysctl -n kernel.panic)" >> "$DRV"
    ;;
esac

# FASTSTALL=1:把 `rcu_cpu_stall_timeout` 从 **60 秒**压到 10 秒(60 是板上实测的默认值,
# 不是想当然的 21 秒)。这一步让"挂 → 报 → panic → 重启"一轮从 ~100 秒缩到 ~40 秒,
# 而且报出来的时刻离死亡更近。kernel 里会自己夹到 ≥3 秒,只能在 /sys/module 那个文件上写,
# /proc/sys/kernel 下没有这个名字。
#
# ⚠ **做 A/B 对照时必须留 60 秒默认值**:10 秒会让"差一点但没挂"的轮次也变成挂,
# 那样比较就不公平了。所以它和 DUMP 分开,只在抓现场时开。
case " $EXTRA " in
  *" FASTSTALL=1 "*)
    echo xilinx | sudo -S sh -c 'echo 10 > /sys/module/rcupdate/parameters/rcu_cpu_stall_timeout'
    echo "faststall: stall_timeout=$(cat /sys/module/rcupdate/parameters/rcu_cpu_stall_timeout 2>&1)" >> "$DRV"
    ;;
esac

echo xilinx | sudo -S sh -c \
  "cd /home/xilinx/jupyter_notebooks && exec env XILINX_XRT=/usr PYTHONUNBUFFERED=1 \
   LIVE_SECONDS=0 LIVE_GAIN_DB=0,0,0,0 $EXTRA \
   /usr/local/share/pynq-venv/bin/python3 fir_live.py" \
  > /dev/shm/one_run_out.log 2>&1 &

i=0
while [ $i -lt 40 ]; do
  P=$(pgrep -f 'python3 fir_live\.py' | head -1)
  [ -n "$P" ] && break
  i=$((i + 1)); sleep 1
done
echo "pid=$P after ${i}s up=$(cut -d. -f1 /proc/uptime)" >> "$DRV"

# 再等它把 overlay 和缓冲都准备好
sleep 10

# NOJRNL=1 复检:**这一行是这一臂可信与否的全部依据。**
# 上一版就是栽在这:只 stop 不 mask,socket 10 秒内又把 journald 拉成 activating,
# 整轮"静音"是假的。现在把起跑前(io_begin/nojrnl)、settle 后(这里)、跑完(io_end)
# 三个点的 mmcblk0 写计数连起来看 —— 中间差值≈0 才说明 SD 真的没被碰。
case " $EXTRA " in
  *" NOJRNL=1 "*)
    echo "nojrnl_check up=$(cut -d. -f1 /proc/uptime) journald=$(systemctl is-active systemd-journald 2>&1) wbcs=$(sysctl -n vm.dirty_writeback_centisecs) $(grep -E '^(nr_dirty|nr_writeback|pgpgout)' /proc/vmstat | tr '\n' ' ') $(awk '$3=="mmcblk0"{print "wr_ios="$8" wr_sect="$10" wr_ms="$11}' /proc/diskstats)" >> "$DRV"
    ;;
esac

# ---- 模拟拖滑杆 ----
# NOTUNE=1:跳过这一下。用来分辨"挂"到底出在拖滑杆那一下,还是本来就会挂
# (上一轮:同一个配置 LIVE_SECONDS=25 稳跑 4/4,而带拖滑杆+SIGINT 的连挂 2/2,
#  差别只在这三样里)。
case " $EXTRA " in
  *" NOTUNE=1 "*)
    echo "after_tune up=$(cut -d. -f1 /proc/uptime) SKIPPED(NOTUNE)" >> "$DRV"
    ;;
  *)
    echo '{"seq":1,"gain_db":[-20,-10,10,20],"bypass":0}' > /dev/shm/fir_tune.json
    sleep 6
    echo "after_tune up=$(cut -d. -f1 /proc/uptime) $(head -c 400 /dev/shm/fir_status.json 2>/dev/null | tr -d '\n')" \
      >> "$DRV"
    ;;
esac

# ---- 模拟点停止 ----
# STOP9=1:改用 kill -9(不走走 stream_end 那道收尾),用来分辨"挂"是发生在
# 收尾那条路上,还是本来就已经挂了。
#
# NOSIG=1:**根本不发信号**,让它自己按 LIVE_SECONDS 跑完然后干净退出。
# 为什么要有这一条:打点跑只能回答"挂没挂",回答不了"能撑多久"。
# 而按现有证据,挂的概率大约是**每连续跑 30~60 秒一次** —— 那就得直接量时长。
case " $EXTRA " in
  *" NOSIG=1 "*)
    n=0
    while [ $n -lt "$SELFW" ]; do
      n=$((n + 1)); sleep 1
      echo xilinx | sudo -S kill -0 "$P" 2>/dev/null || break
    done
    ALIVE=$(echo xilinx | sudo -S kill -0 "$P" 2>/dev/null && echo y || echo n)
    echo "self_exit after ${n}s alive=$ALIVE up=$(cut -d. -f1 /proc/uptime)" >> "$DRV"
    echo "threads_at_stop=[$(ps -eLo tid,stat,psr,comm 2>/dev/null | grep python3 | tr -s ' ' | tr '\n' ';')]" >> "$DRV"
    echo "tail=[$(tail -c 200 /dev/shm/one_run_out.log 2>/dev/null | tr -d '\n')]" >> "$DRV"
    ;;
  *)
    case " $EXTRA " in
      *" STOP9=1 "*)
        SIG=KILL ;;
      *)
        SIG=INT ;;
    esac
    echo xilinx | sudo -S kill -"$SIG" "$P" 2>/dev/null
    n=0
    while [ $n -lt "$WAITS" ]; do
      n=$((n + 1)); sleep 1
      # fir_live.py 是 root 跑的,不收 sudo 的话 kill -0 会"没权限"而返回非零 ——
      # 那样永远报 alive=n,这一行就没意义了(2026-10-16 晚发现的)。
      echo xilinx | sudo -S kill -0 "$P" 2>/dev/null || break
    done
    ALIVE=$(echo xilinx | sudo -S kill -0 "$P" 2>/dev/null && echo y || echo n)
    echo "after_stop ${n}s alive=$ALIVE up=$(cut -d. -f1 /proc/uptime)" >> "$DRV"
    # 停在哪儿了:每个 python 线程的状态和所在核。alive=y 时这一行说明是哪个线程没退。
    echo "threads_at_stop=[$(ps -eLo tid,stat,psr,comm 2>/dev/null | grep python3 | tr -s ' ' | tr '\n' ';')]" >> "$DRV"
    echo "tail=[$(tail -c 200 /dev/shm/one_run_out.log 2>/dev/null | tr -d '\n')]" >> "$DRV"
    ;;
esac

echo "ALIVE_AFTER_STOP=$ALIVE"
echo "io_end $(awk '$3=="mmcblk0"{print "wr_ios="$8" wr_sect="$10" wr_ms="$11}' /proc/diskstats) $(grep -E '^(nr_dirty|nr_writeback)' /proc/vmstat | tr '\n' ' ')" >> "$DRV"

# 撤掉看门狗守卫(fir_live.py 已经退出,守卫本身也该撤防了)。
# 这里是用户的正常路径,守卫必须跟着走,不然它会一直占着 /dev/watchdog。
case " $EXTRA " in
  *" WD=1 "*)
    echo xilinx | sudo -S pkill -f 'wd_guar[d]\.sh' 2>/dev/null
    echo "wd_log=[$(tr '\n' ';' < /dev/shm/wd_guard.log 2>/dev/null | tail -c 200)]" >> "$DRV"
    ;;
esac

# NOJRNL=1 收尾:把 journald 和回刷节奏都还原回默认,别把板子留在一个改过的状态里。
# mask --runtime 是"只到重启为止",重启自己就没了,但正常收尾还是显式解掉更干净。
case " $EXTRA " in
  *" NOJRNL=1 "*)
    echo xilinx | sudo -S sh -c 'sysctl -w vm.dirty_writeback_centisecs=500 vm.dirty_expire_centisecs=3000 > /dev/null;
      systemctl unmask --runtime systemd-journald.service systemd-journald.socket 2>/dev/null;
      for t in apt-daily.timer apt-daily-upgrade.timer man-db.timer fstrim.timer \
               e2scrub_all.timer logrotate.timer systemd-tmpfiles-clean.timer; do
        systemctl unmask --runtime $t 2>/dev/null;
      done;
      systemctl start systemd-journald 2>/dev/null'
    echo "nojrnl_end: journald=$(systemctl is-active systemd-journald 2>&1) wbcs=$(sysctl -n vm.dirty_writeback_centisecs)" >> "$DRV"
    ;;
esac

# DUMP/FASTSTALL 收尾:把开关还原(它们只在运行期有效,重启也会自己回默认)。
# 注意:**挂了的话这一段跑不到** —— 那种情况下板子 panic 重启,设置跟着没了,也没事。
case " $EXTRA " in
  *" DUMP=1 "*|*" FASTSTALL=1 "*)
    echo xilinx | sudo -S sh -c \
      'sysctl -w kernel.panic_print=0 > /dev/null; echo 60 > /sys/module/rcupdate/parameters/rcu_cpu_stall_timeout'
    echo "dump_end: panic_print=$(sysctl -n kernel.panic_print) stall_timeout=$(cat /sys/module/rcupdate/parameters/rcu_cpu_stall_timeout 2>&1)" >> "$DRV"
    ;;
esac

grep -E 'after_tune|dump|faststall|nojrnl|BEGIN' "$DRV"
echo "RESULT=done"
