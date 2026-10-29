#!/bin/sh
# 重启网页第三档：先清旧进程，再按顺序起 fir_pl_live → fir_pl_webui。
# 顺序不能反：overlay 只能被一个进程持有，网页进程只读写 /dev/shm 下的 json。
#
#   sudo sh /home/xilinx/dma_exp/pl_webui_up.sh
#
# 2026-10-29 加这个脚本的原因：手工 nohup 的时候，ssh 会话会被后台进程挂住，
# 而且旧进程占着 8080 会让新进程直接 OSError 98，看着像"网页还是老版本"。
cd /home/xilinx/dma_exp || exit 1

pkill -f fir_pl_webui 2>/dev/null
pkill -f fir_pl_live 2>/dev/null
sleep 1

setsid env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u \
    fir_pl_live.py > /tmp/live.log 2>&1 < /dev/null &
sleep 4
setsid env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u \
    fir_pl_webui.py --port 8080 > /tmp/webui.log 2>&1 < /dev/null &
sleep 3

echo "--- live.log"
tail -3 /tmp/live.log
echo "--- webui.log"
tail -3 /tmp/webui.log
echo "--- 进程"
ps -ef | grep -E '[f]ir_pl_(live|webui)'
