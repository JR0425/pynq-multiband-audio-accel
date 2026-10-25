#!/bin/sh
# 把 eth0 的中断移到另一个核（默认 CPU1）。
#
# 搬样线程钉在 CPU0、SCHED_FIFO 20，每 7.5 ms 要交一块，实际花 7.41 ms ——
# 余量约 1%。eth0 的中断默认落在 CPU0，处理它的内核线程是 FIFO 50，比搬样高：
# 每来一个包就把搬样挤开一下，挤掉的整块补不回来。
#
# 实测（2026-10-25，fir.bit，L=360/NBUF=2，fir_live 由网页起）：
#   中断在 CPU0：网页按 150/250 ms 轮询，60 秒丢 33~39 块（0.6~0.7 块/秒）
#   中断在 CPU1：同样的轮询，60 秒丢 0 块，io 中位 7.415 ms、最大 7.428 ms
#
# 用法（root）：sh eth_irq_affinity.sh [cpu]
set -e

cpu=${1:-1}

# 开机时网卡可能还没 probe 完，等一会儿再找。
i=0
irq=""
while [ "$i" -lt 60 ]; do
    irq=$(awk '$NF == "eth0" { sub(":", "", $1); print $1; exit }' /proc/interrupts)
    [ -n "$irq" ] && break
    i=$((i + 1))
    sleep 1
done

if [ -z "$irq" ]; then
    echo "没在 /proc/interrupts 里找到 eth0" >&2
    exit 1
fi

if [ ! -w "/proc/irq/$irq/smp_affinity_list" ]; then
    echo "/proc/irq/$irq/smp_affinity_list 不可写（内核不支持给这个中断改亲和性）" >&2
    exit 1
fi

echo "$cpu" > "/proc/irq/$irq/smp_affinity_list"
echo "eth0 irq $irq -> cpu$(cat "/proc/irq/$irq/smp_affinity_list")"
