#!/bin/sh
#
# 一条命令起实时：麦克风 → 核 → 耳机。**在板上跑**（不是在你电脑上）。
#
#     sh run_live.sh            跑 30 秒
#     sh run_live.sh 60         跑 60 秒
#     sh run_live.sh 0          一直跑，Ctrl-C 停
#
# 板子上的完整命令本来是这一长串：
#
#     echo xilinx | sudo -S sh -c "cd /home/xilinx/jupyter_notebooks && \
#         XILINX_XRT=/usr PYTHONIOENCODING=utf-8 LIVE_SECONDS=30 \
#         /usr/local/share/pynq-venv/bin/python3 fir_live.py"
#
# 这个脚本就是它，省得每次打。要改别的参数就照原样加环境变量，比如：
#
#     LIVE_BYPASS=1 sh run_live.sh 30     直通（不做压缩），用来 A/B 对照
#     LIVE_SRC=wav sh run_live.sh 10      不用麦克风，放 wav（验流水线用）
#
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
SECONDS_RUN=${1:-30}

echo "xilinx" | sudo -S sh -c "cd '$HERE' && XILINX_XRT=/usr PYTHONIOENCODING=utf-8 \
    LIVE_SECONDS=$SECONDS_RUN ${LIVE_EXTRA:-} \
    /usr/local/share/pynq-venv/bin/python3 fir_live.py"
