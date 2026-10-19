#!/bin/sh
#
# WDRC 起停时间（attack / release）的听感 A/B —— **在板上跑**。
#
#     sh wdrc_ab_live.sh
#
# 目的：同一趟里只改起停时间，判断"真 WDRC"和"无记忆压缩"耳朵能不能分出来。
# 门限和压缩比全程不动（−36 dBFS、4:1），所以唯一的变量就是起停。
#
# 时间线：
#     0-20s    起停 0 / 0      → 无记忆压缩（老 drc()）
#    20-50s    起停 5 / 100 ms → 真 WDRC
#    50-90s    起停 0 / 0      → 切回来
#
# 判据：20 秒和 50 秒前后，"一抽一抽"的起伏有没有变化。
# 数字侧同时看：`[调参] 起振 ...` 两行（改动被接住）、
#              逐位自检 960000/960000（边改参数边跑也没算错）。
#
cd /home/xilinx/jupyter_notebooks || exit 1
T=/dev/shm/fir_tune.json

# 启动前先摆好旋钮：门限 −36 dBFS、4:1，起停先关。
# fir_live 启动后的第一次轮询（约 50 ms）就会把它吃进去。
printf '%s\n' '{"gain_db":[0,0,0,0],"thr_db":[-36,-36,-36,-36],"ratio":[0.25,0.25,0.25,0.25],"attack_ms":[0,0,0,0],"release_ms":[0,0,0,0],"bypass":0}' > "$T"

LIVE_EXTRA='LIVE_VOLUME=35 LIVE_DIAG=1' sh run_live.sh 90 > /tmp/wdrc_ab_live.log 2>&1 &
PID=$!

sleep 20
printf '%s\n' '{"attack_ms":[5,5,5,5],"release_ms":[100,100,100,100]}' > "$T"
echo ">>> 起停 -> 5 / 100 ms  @$(date +%M:%S)"

sleep 30
printf '%s\n' '{"attack_ms":[0,0,0,0],"release_ms":[0,0,0,0]}' > "$T"
echo ">>> 起停 -> 0 / 0  @$(date +%M:%S)"

wait $PID
printf '%s\n' '{"attack_ms":[0,0,0,0],"release_ms":[0,0,0,0]}' > "$T"

echo "==================== 跑完 ===================="
grep -a "调参\|电平\] \|OK \]\|倍率\|进核的电平" /tmp/wdrc_ab_live.log
