#!/bin/sh
# 把 ksoftirqd 和 kworker 提到比搬样线程更高的实时优先级（FIFO 50）。
#
# 为什么：fir_live.py 的搬样线程是 SCHED_FIFO 20，按设计永不阻塞
# （`duplex_block()` 里是纯 MMIO 轮询）。ksoftirqd 是普通 CFS 类，
# 在同一个核上永远排不到 CPU → CPU 的 RCU 静默态报不上去 →
# 60 秒后 `RCU Stall` → panic。把 ksoftirqd 放到 FIFO 50，
# 它需要处理软中断时就能抢占搬样线程。
#
# 2026-10-17 round12b 补：只提 ksoftirqd 不够。600 秒那一臂跑到约 390 秒时，
# `ksoftirqd/0` 已经是 S（提权生效），但转储里 `kworker/0:1` 仍是 R，
# 而且 RCU 直接点名它阻塞了宽限期（`Tasks blocked ... : P21`，21 就是它）。
# 所以 workqueue 的 kworker/[01]:* 也一起提到 FIFO 50。
#
# 实测（2026-10-17，实时全开、300 秒）：板子活着跑完，
# 丢块 61（0.20 块/秒，比不提权时还低），实时倍率 0.998x。
#
# 这个脚本要 root 跑（chrt 改内核线程的调度类）。
# 由 ksoftirqd-rt.service 在开机时调用；见 install_ksoftirqd_rt.sh。

ok=0
kw=0
kwerr=0
for p in 0 1; do
    pid=$(pgrep -f "^ksoftirqd/$p$" | head -1)
    if [ -n "$pid" ] && chrt -f -p 50 "$pid" 2>/dev/null; then
        ok=$((ok + 1))
    else
        logger -t ksoftirqd-rt -p warning "cpu$p 失败（pid=$pid）"
    fi
done

# workqueue 的普通 worker。H（高优先）和 u（unbound）不在这个核的池里，不用管。
for pid in $(pgrep -f "^kworker/[01]:"); do
    if chrt -f -p 50 "$pid" 2>/dev/null; then
        kw=$((kw + 1))
    else
        kwerr=$((kwerr + 1))
    fi
done
# 成功一次都不记：这个脚本 30 秒跑一趟，日志写的是 SD 卡，而刷卡的 kworker
# 是 FIFO 50、在 CPU0 上会直接打断搬样线程（实测每次 5~90 ms）。只有失败
# 才值得留一条警告。
[ "$kwerr" -gt 0 ] && logger -t ksoftirqd-rt -p warning "kworker 提权失败 $kwerr 个"

exit $([ "$ok" -ge 2 ] && echo 0 || echo 1)
