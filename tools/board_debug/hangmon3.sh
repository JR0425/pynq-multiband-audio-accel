#!/bin/sh
# ⚠ 2026-10-16 收尾补:**这一版已废弃,留作反面教材,别再用。**
#   它每 6 秒把整份日志 `cp` 到 SD、每 12 秒 `sync`,还每秒跑一次 `ps`;而它抓到的
#   死亡现场恰恰是 `sync` / `jbd2/mmcblk0p2` 卡在 D 状态 —— 黑匣子自己就可能是
#   那个往 SD 上压 IO 的人。请用 hangmon4.sh。
# 死机黑匣子 第三版 (2026-10-16 晚)
#
# 第二版漏掉的关键量:CPU0 到底还在不在收中断。
# "rcu stall on CPU0" 只说明 CPU0 有 21 秒没经过静止态;要分清是
#   ① CPU0 在核里关着中断转,还是
#   ② CPU0 被一个 SCHED_FIFO 的用户态线程占满(中断其实还是收的),
# 得看 /proc/stat 里那一路的 irq/softirq 计数还涨不涨 —— 关中断就是冻住。
#
# 另外把 fir_live.py 自己的线程单独点出来(状态+在哪颗核+调度策略),
# 因为 ps 的 comm 全是 python3,分不出 io/dsp 两条。
#
# 采样只写内存盘,每 6 秒才整份拷到 SD;sync 每 12 秒一次。
# 自己不能成为 SD 卡上的负担 —— 第一版就是栽在这。

RAM=/dev/shm/hangmon.log
SD=/home/xilinx/hangmon_sd.log

echo "# monitor3 start $(date '+%F %T') uptime=$(cut -d. -f1 /proc/uptime)" > "$RAM"

n=0
while true; do
  n=$((n + 1))
  P=$(pgrep -f 'fir_live\.py' | head -1)
  {
    printf '=== %s up=%s load=%s memfree_kb=%s fir=%s\n' \
      "$(date '+%T')" "$(cut -d. -f1 /proc/uptime)" \
      "$(cut -d' ' -f1-3 /proc/loadavg)" \
      "$(awk '/^MemFree/{print $2}' /proc/meminfo)" \
      "$(pgrep -c -f 'fir_live\.py')"

    # 每颗核:用户/系统/空闲/等IO/硬中断/软中断 的累积节拍数。
    # 关中断的判据:irq 和 idle 同时冻住,而 system 还在涨。
    printf -- '--cpu-- '
    awk '/^cpu[0-9]/{printf "%s usr=%s sys=%s idle=%s iow=%s irq=%s soft=%s | ",
                         $1,$2,$4,$5,$6,$7,$8}' /proc/stat
    echo

    # 网口和 SD 卡的中断数(每颗核各一列)。
    printf -- '--irq-- '
    awk '/mmc|eth|uart/{printf "%s:", $1; for(i=2;i<=NF-2;i++) printf " %s", $i; printf " | "}' \
      /proc/interrupts
    echo

    printf -- '--D-- '
    ps -eLo stat,psr,comm 2>/dev/null | awk 'NR>1 && $1 ~ /^D/' | head -8 | tr '\n' ';'
    echo

    if [ -n "$P" ]; then
      printf -- '--fir-- '
      for t in /proc/$P/task/*; do
        awk '{printf "tid=%s %s psr=%s ", $1, $3, $39}' "$t/stat" 2>/dev/null
        awk '{printf "pol=%s prio=%s | ", $41, $18}' "$t/sched" 2>/dev/null
      done
      echo
    fi

    echo "--threads--"
    ps -eLo psr,pcpu,stat,comm --sort=-pcpu 2>/dev/null | head -9 | tail -8
  } >> "$RAM"

  if [ $((n % 3)) -eq 0 ]; then cp "$RAM" "$SD"; fi
  if [ $((n % 6)) -eq 0 ]; then sync; fi
  sleep 2
done
