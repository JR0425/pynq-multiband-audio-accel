#!/bin/sh
# 一次性安装 fir-pl-webui.service —— 演示通路的"持 overlay + 网页验配"服务。
#
# 装什么：
#   /usr/local/sbin/pl_supervise.sh                守护进程（巡检 30 秒）
#   /etc/systemd/system/fir-pl-webui.service       unit
#   <板上目录>/fir_pl_live.py、fir_pl_webui.py      同目录下有的才拷（覆盖同名旧文件）
#
# 为什么要有这一步：根 README 让评委用 `systemctl start fir-pl-webui` 启动演示，
# 服务不存在时这条命令会直接报 unit not found。原来那份 unit 装在板上、没入库，
# 换一块板/重刷 SD 卡就没了。
#
# 用法（板上，root）：
#     sudo sh install_pl_webui_service.sh                 # 默认 /home/xilinx/dma_exp
#     sudo sh install_pl_webui_service.sh /home/xilinx/x   # 换目录
#
# 卸载：
#     sudo systemctl disable --now fir-pl-webui
#     sudo rm /etc/systemd/system/fir-pl-webui.service /usr/local/sbin/pl_supervise.sh
#     sudo systemctl daemon-reload
set -e

HERE=$(cd "$(dirname "$0")" && pwd)
TARGET="${1:-/home/xilinx/dma_exp}"

if [ "$(id -u)" -ne 0 ]; then
    echo "要用 root 跑：sudo sh $0"
    exit 1
fi

mkdir -p "$TARGET"
echo "板上目录：$TARGET"

# 驱动和网页：同目录下有就一起拷，没有就假定板子上已经有了
for f in fir_pl_live.py fir_pl_webui.py page.html; do
    if [ -f "$HERE/$f" ]; then
        install -m 644 "$HERE/$f" "$TARGET/$f"
        echo "  拷入 $f"
    else
        echo "  跳过 $f（仓库里没有，按板上已有处理）"
    fi
done

install -m 755 "$HERE/pl_supervise.sh" /usr/local/sbin/pl_supervise.sh

# unit 里的路径跟着 TARGET 走，不写死
sed "s#/home/xilinx/dma_exp#$TARGET#g" "$HERE/fir-pl-webui.service" \
    > /etc/systemd/system/fir-pl-webui.service

systemctl daemon-reload
systemctl enable fir-pl-webui.service
systemctl restart fir-pl-webui.service
sleep 8

echo
systemctl --no-pager --full status fir-pl-webui.service | head -12
echo
echo "进程："
pgrep -af 'fir_pl_(live|webui)\.py' || echo "  一个都没起来，看 /tmp/live.log 与 /tmp/webui.log"
echo
echo "下一步：curl -s http://127.0.0.1:8080/api/presets | head -20"
echo "停服务：systemctl stop fir-pl-webui（别只 pkill，最多管 30 秒）"
