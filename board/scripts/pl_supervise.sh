#!/bin/sh
# 板上守护进程：fir_pl_live.py（持有 overlay）+ fir_pl_webui.py（8080 页面），
# 每 30 秒巡检一次，掉哪个拉哪个。
#
# 为什么必须成对守护：overlay 只能被一个进程持有，webui 只读写 /dev/shm 下的 json，
# 两个进程的启动顺序不能反。手工 nohup 拼这两条命令，掉一个整条通路就哑了，
# 而且从表现上看像"网页还在、就是没声音"。
#
# 为什么停服务必须用 systemctl：这个脚本自己会把掉的进程拉回来，
# `pkill fir_pl_live` 最多管 30 秒 —— 根 README 里那句"只 pkill 最多管半分钟"
# 说的就是这里。
#
# 用法（板上，root）：
#     sudo sh install_pl_webui_service.sh     # 装一次，开机自启
#     systemctl start|stop|status fir-pl-webui
# 前台调试（不装服务）：
#     sudo sh pl_supervise.sh
#
# 来历：板上原来那份从未入过库（`git log --all -- board/scripts/pl_supervise.sh` 为空），
# 于是照根 README 与 board/probes/board_accept.sh 里记录的行为重建了这一份：
# 先 live 后 webui、30 秒巡检、只用 systemctl 停。**行为对齐，实现不保证与板上那份逐字相同**
# —— 判据是 board_accept.sh 那四项能过，不是 diff 为空。
set -u

DIR="${PL_WEBUI_DIR:-/home/xilinx/dma_exp}"
PY="${PL_WEBUI_PY:-/usr/local/share/pynq-venv/bin/python3}"
PORT="${PL_WEBUI_PORT:-8080}"
INTERVAL="${PL_WEBUI_INTERVAL:-30}"

if [ ! -d "$DIR" ]; then
    echo "找不到板上目录 $DIR（用 PL_WEBUI_DIR 指定）"
    exit 1
fi
cd "$DIR" || exit 1
for f in "$DIR/fir_pl_live.py" "$DIR/fir_pl_webui.py"; do
    [ -f "$f" ] || { echo "缺 $f，先把 board/scripts/ 下的 .py 拷过来"; exit 1; }
done

running() { pgrep -f "$1" >/dev/null 2>&1; }
stamp()   { date '+%F %T'; }

start_live() {
    echo "$(stamp) 起 fir_pl_live.py"
    setsid env XILINX_XRT=/usr "$PY" -u fir_pl_live.py \
        > /tmp/live.log 2>&1 < /dev/null &
    # overlay 只能被一个进程持有：等它把 PL 装好再起网页，否则网页先拿到句柄会抢失败
    sleep 4
}

start_webui() {
    echo "$(stamp) 起 fir_pl_webui.py --port $PORT"
    setsid env XILINX_XRT=/usr "$PY" -u fir_pl_webui.py --port "$PORT" \
        > /tmp/webui.log 2>&1 < /dev/null &
    sleep 2
}

echo "$(stamp) pl_supervise 启动：dir=$DIR py=$PY port=$PORT 巡检=${INTERVAL}s"

# 第一次启动：顺序不能反
running "fir_pl_live.py"  || start_live
running "fir_pl_webui.py" || start_webui

while :; do
    sleep "$INTERVAL"

    if ! running "fir_pl_live.py"; then
        echo "$(stamp) fir_pl_live 不在了，重拉（webui 一并重启：它握的是旧状态）"
        pkill -f fir_pl_webui.py 2>/dev/null
        sleep 1
        start_live
        start_webui
        continue
    fi

    if ! running "fir_pl_webui.py"; then
        echo "$(stamp) fir_pl_webui 不在了，重拉"
        start_webui
    fi
done
