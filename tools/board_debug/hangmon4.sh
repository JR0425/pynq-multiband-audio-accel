#!/bin/sh
# ⚠ 2026-10-16 收尾补:黑匣子的**当前版**(v4)。和 v3 的关键差别是
#   只写 /dev/shm、一次都不碰 SD;历史由主机侧每 2.5 秒拉走一份。
#   它不在板上留历史 —— 板子一重启 /dev/shm 就空,所以"主机来拉"是必须的,不是可选。
# 黑匣子 v4 —— **只写 /dev/shm,一次都不碰 SD**。
#
# 为什么改(2026-10-16 晚):v3 每 6 秒 `cp` 整份日志到 SD、每 12 秒 `sync`,
# 而 v3 抓到的死亡现场恰恰是 `sync` 和 `jbd2/mmcblk0p2` 卡在 D 状态 ——
# 黑匣子自己就可能是那个往 SD 上压 IO 的人。v4 只写内存,主机每秒拉一份走。
# (板子一重启 /dev/shm 就清空,所以"主机来拉"是必须的,不是可选。)
#
# 每秒还抄一份 fir_live.py 的状态文件 —— 那是"管线到底还在前进、还是已经停了"
# 的唯一证据(黑匣子看不见 duplex_block 里面)。

RAM=/dev/shm/hangmon.log

: > "$RAM"
echo "# monitor4 start up=$(cut -d. -f1 /proc/uptime)" > "$RAM"

while true; do
  P=$(pgrep -f 'fir_live\.py' | head -1)
  {
    printf '=== up=%s load=%s free_kb=%s\n' \
      "$(cut -d. -f1 /proc/uptime)" \
      "$(cut -d' ' -f1-3 /proc/loadavg)" \
      "$(awk '/^MemFree/{print $2}' /proc/meminfo)"

    printf -- '--cpu-- '
    awk '/^cpu[0-9]/{printf "%s usr=%s sys=%s idle=%s iow=%s irq=%s soft=%s | ",
                         $1,$2,$4,$5,$6,$7,$8}' /proc/stat
    echo

    printf -- '--irq-- '
    awk '/mmc|eth|uart/{printf "%s:", $1; for(i=2;i<=NF-2;i++) printf " %s", $i; printf " | "}' \
      /proc/interrupts
    echo

    printf -- '--D-- '
    ps -eLo stat,psr,comm 2>/dev/null | awk 'NR>1 && $1 ~ /^D/' | head -8 | tr '\n' ';'
    echo

    printf -- '--fir-- '
    if [ -n "$P" ]; then
      for t in /proc/$P/task/*; do
        awk '{printf "tid=%s %s psr=%s ", $1, $3, $39}' "$t/stat" 2>/dev/null
        awk '{printf "pol=%s prio=%s | ", $41, $18}' "$t/sched" 2>/dev/null
      done
    fi
    echo

    printf -- '--st-- '
    head -c 320 /dev/shm/fir_status.json 2>/dev/null
    echo
  } >> "$RAM"
  # 2 秒一次(不是 1 秒):黑匣子本身的活越少,越不会把"它自己压挂的"
  # 算到管线头上。原先那条带 --sort=-pcpu 的 ps 是最贵的一条(要读遍
  # /proc/*/stat 再排序),已经删掉 —— 两次死亡现场里,黑匣子都只跑了
  # 9~35 秒就断了,它自己就是嫌疑犯。
  sleep 2
done
