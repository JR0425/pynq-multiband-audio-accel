#!/bin/sh
# 一次性安装：开机把 eth0 中断绑到 CPU1，让它别再和搬样线程抢 CPU0。
#
# 为什么装成服务而不是手工 echo：板子一重启，中断亲和性就回到默认的 CPU0。
#
# 用法（在板上，用 root）：
#     sudo sh install_eth_irq_affinity.sh
#
# 卸载：
#     sudo systemctl disable --now eth-irq-affinity.service
#     sudo rm /etc/systemd/system/eth-irq-affinity.service \
#             /usr/local/sbin/eth_irq_affinity.sh
set -e

HERE=$(cd "$(dirname "$0")" && pwd)

if [ "$(id -u)" -ne 0 ]; then
    echo "要用 root 跑：sudo sh $0"
    exit 1
fi

install -m 755 "$HERE/eth_irq_affinity.sh" /usr/local/sbin/eth_irq_affinity.sh

cat > /etc/systemd/system/eth-irq-affinity.service <<'EOF'
[Unit]
Description=Move eth0 IRQ off CPU0 (the audio mover runs there at SCHED_FIFO)
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/eth_irq_affinity.sh 1
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable eth-irq-affinity.service
systemctl restart eth-irq-affinity.service
systemctl --no-pager --full status eth-irq-affinity.service | head -12
echo
echo "当前 eth0 中断分布："
grep eth0 /proc/interrupts
