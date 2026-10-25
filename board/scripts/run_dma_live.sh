#!/bin/sh
#
# 一条命令起 DMA 那条实时链路（实验版）：麦克风 → DDR → 核 → DDR → 耳机。
# **在板上跑**（不是在你电脑上）。出货那条是 run_live.sh，两个互不影响。
#
#     sh run_dma_live.sh            跑 60 秒
#     sh run_dma_live.sh 120        跑 120 秒
#
# 和 run_live.sh 有两点不一样，都不是可选项：
#
#   chrt -f 60    板子上有个 ksoftirqd-rt-refresh.service 把 ksoftirqd 和十几个
#                 kworker 提成了 SCHED_FIFO 50。普通优先级的进程会被它们一口气
#                 挤开 10～30 ms，而录音 FIFO 的积压一旦涨上去就再也不降（每块
#                 进出一块，是个中性平衡）—— 之后整段都背着那十几毫秒。
#                 不套 chrt 时 60 秒断流 60～650 次，套上之后 0 次。
#   taskset -c 0  钉在一颗核上，省得两个 FIFO 线程被调度器来回搬。
#
# 真要改别的参数就照 dma_fir_loop.py 的命令行加：
#
#     DMA_EXTRA="--bypass" sh run_dma_live.sh 30      直通（不做压缩），A/B 用
#     DMA_EXTRA="--tx-low 480" sh run_dma_live.sh 60  播放积压压小一点
#
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
SECONDS_RUN=${1:-60}

# 验配网页的旋钮文件要删掉：上一趟要是留了个大增益，耳机里就是一声巨响
echo "xilinx" | sudo -S rm -f /dev/shm/fir_tune.json

echo "xilinx" | sudo -S sh -c "cd '$HERE' && XILINX_XRT=/usr PYTHONIOENCODING=utf-8 \
    chrt -f 60 taskset -c 0 /usr/local/share/pynq-venv/bin/python3 \
    dma_fir_loop.py --secs $SECONDS_RUN ${DMA_EXTRA:-}"
