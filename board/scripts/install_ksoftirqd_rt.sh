#!/bin/sh
# 一次性安装：让内核线程提权的开机提权在每次启动时自动生效。
#
# 为什么要在板上装成服务，而不是每次跑之前手工 chrt：
#   · 演示是点 notebook 启动，没有机会插一条 sudo chrt；
#   · 板子 panic 重启后设置会丢，服务会自己重新提权；
#   · one_run.sh 的每一臂也就自动带上这个前提，A/B 不会混。
#
# 为什么还要一个 30 秒的 timer：workqueue 会按需新建 kworker，开机那一次扫描
# 抓不到它们。新 kworker 仍是普通 CFS 类，一样会被搬样线程饿死（round12b 的
# `kworker/0:1` 就是开机时就有、但只提 ksoftirqd 那版漏掉的）。timer 周期重扫，
# 已经提过权的再 chrt 一次是空操作。
#
# 用法（在板上，用 root）：
#     sudo sh install_ksoftirqd_rt.sh
#
# 卸载：
#     sudo systemctl disable --now ksoftirqd-rt.service ksoftirqd-rt-refresh.timer
#     sudo rm /etc/systemd/system/ksoftirqd-rt.service \
#             /etc/systemd/system/ksoftirqd-rt-refresh.service \
#             /etc/systemd/system/ksoftirqd-rt-refresh.timer \
#             /usr/local/sbin/ksoftirqd_rt.sh
set -e

HERE=$(cd "$(dirname "$0")" && pwd)

if [ "$(id -u)" -ne 0 ]; then
    echo "要用 root 跑：sudo sh $0"
    exit 1
fi

install -m 755 "$HERE/ksoftirqd_rt.sh" /usr/local/sbin/ksoftirqd_rt.sh

cat > /etc/systemd/system/ksoftirqd-rt.service <<'EOF'
[Unit]
Description=Raise ksoftirqd/kworker above the audio mover's SCHED_FIFO
After=basic.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ksoftirqd_rt.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/ksoftirqd-rt-refresh.service <<'EOF'
[Unit]
Description=Re-scan ksoftirqd/kworker scheduling classes (new workers appear at runtime)

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ksoftirqd_rt.sh
EOF

cat > /etc/systemd/system/ksoftirqd-rt-refresh.timer <<'EOF'
[Unit]
Description=Periodically re-raise newly created kworkers

[Timer]
OnBootSec=45
OnUnitActiveSec=30

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable ksoftirqd-rt.service
systemctl restart ksoftirqd-rt.service
systemctl enable --now ksoftirqd-rt-refresh.timer
systemctl --no-pager --full status ksoftirqd-rt.service | head -12
echo
echo "timer: $(systemctl is-active ksoftirqd-rt-refresh.timer)"
echo "当前策略："
for p in 0 1; do
    pid=$(pgrep -f "^ksoftirqd/$p$" | head -1)
    [ -n "$pid" ] && chrt -p "$pid"
done
for pid in $(pgrep -f "^kworker/[01]:"); do
    printf 'kworker pid=%s ' "$pid"; chrt -p "$pid" | tail -1
done
