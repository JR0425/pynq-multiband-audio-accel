#!/bin/sh
# 在板子上起浏览器调参页。
#
#   sh run_webui.sh              # 默认 8080
#   PORT=8090 sh run_webui.sh
#
# 起来之后在 PC 浏览器打开 http://192.168.2.99:8080/
# 关掉浏览器不影响音频；要停音频用页面上的「停止」。
#
# 为什么这个脚本自己要 sudo：页面上的「启动实时」会拉起 fir_live.py，
# 那条要 /dev/mem、SCHED_FIFO 和音频设备，普通用户起不来。
# PYNQ 镜像的 sudo 口令是 xilinx（和 run_live.sh 一致）。

HERE=$(cd "$(dirname "$0")" && pwd)
PORT=${PORT:-8080}

PY=/usr/local/share/pynq-venv/bin/python3
[ -x "$PY" ] || PY=$(command -v python3)
[ -n "$PY" ] || { echo "找不到 python3"; exit 1; }

export PYTHONIOENCODING=utf-8

if [ "$(id -u)" = "0" ]; then
    exec "$PY" "$HERE/fir_webui.py" --port "$PORT"
fi

echo xilinx | sudo -S "$PY" "$HERE/fir_webui.py" --port "$PORT"
