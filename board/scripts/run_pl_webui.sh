#!/usr/bin/env bash
# 网页调音台第三档（PL 直连）。它只读写 /dev/shm 下的两个 json，
# 真正把参数变成寄存器动作的是 fir_pl_live.py，两个一起跑：
#
#   sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u /home/xilinx/dma_exp/fir_pl_live.py &
#   sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u /home/xilinx/dma_exp/fir_pl_webui.py --port 8080
#
# 浏览器开 http://<板子IP>:8080/ —— 页面和第一档是同一个 page.html。
set -u
here="$(cd "$(dirname "$0")" && pwd)"
port="${1:-8080}"
exec env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u \
    "$here/fir_pl_webui.py" --port "$port"
