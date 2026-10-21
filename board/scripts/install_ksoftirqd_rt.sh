#!/bin/sh
# 一次性安装：让内核线程提权的开机提权在每次启动时自动生效。
#
# 为什么要在板上装成服务，而不是每次跑之前手工 chrt：
#   · 演示是点 notebook 启动，没有机会插一条 sudo chrt；
#   · 板子 panic 重启后设置会丢，服务会自己重新提权；
#   · one_run.sh 的每一臂也就自动带上这个前提，A/B 不会混。
#
# 为什么还要周期重扫：workqueue 会按需新建 kworker，开机那一次扫描抓不到它们。
# 新 kworker 仍是普通 CFS 类，一样会被搬样线程饿死（round12b 的 `kworker/0:1`
# 就是开机时就有、但只提 ksoftirqd 那版漏掉的）。
#
# 重扫是一个常驻的小 Python 进程（ksoftirqd_rt_watch.py），不是 systemd timer：
# timer 每 30 秒让 systemd 写四行 journal，journald 落盘在 SD 卡上，刷卡的
# kworker 是 FIFO 50、在 CPU0 上会打断搬样（实测 5~90 ms）。常驻进程只读
# /proc、只调 sched_setscheduler，不 fork、不写日志、不碰磁盘。
#
# 用法（在板上，用 root）：
#     sudo sh install_ksoftirqd_rt.sh
#
# 卸载：
#     sudo systemctl disable --now ksoftirqd-rt.service ksoftirqd-rt-refresh.service
#     sudo rm /etc/systemd/system/ksoftirqd-rt.service \
#             /etc/systemd/system/ksoftirqd-rt-refresh.service \
#             /usr/local/sbin/ksoftirqd_rt.sh \
#             /usr/local/sbin/ksoftirqd_rt_watch.py
set -e

HERE=$(cd "$(dirname "$0")" && pwd)

if [ "$(id -u)" -ne 0 ]; then
    echo "要用 root 跑：sudo sh $0"
    exit 1
fi

install -m 755 "$HERE/ksoftirqd_rt.sh" /usr/local/sbin/ksoftirqd_rt.sh
install -m 755 "$HERE/ksoftirqd_rt_watch.py" /usr/local/sbin/ksoftirqd_rt_watch.py

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
Description=Keep ksoftirqd/kworker at SCHED_FIFO 50 (in-process scan, no journal writes)

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/sbin/ksoftirqd_rt_watch.py
Restart=always
RestartSec=2
CPUAffinity=1
StandardOutput=null
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 旧版是 30 秒一次的 systemd timer；保留文件会让它继续每 30 秒写 journal
# （journald 在 SD 卡上，刷卡的 kworker 会打断搬样）。这里主动停掉并删掉。
systemctl disable --now ksoftirqd-rt-refresh.timer 2>/dev/null || true
rm -f /etc/systemd/system/ksoftirqd-rt-refresh.timer

systemctl daemon-reload
systemctl enable ksoftirqd-rt.service
systemctl restart ksoftirqd-rt.service
systemctl enable --now ksoftirqd-rt-refresh.service
systemctl --no-pager --full status ksoftirqd-rt.service | head -12
echo
echo "watch: $(systemctl is-active ksoftirqd-rt-refresh.service)"
echo "当前策略："
for p in 0 1; do
    pid=$(pgrep -f "^ksoftirqd/$p$" | head -1)
    [ -n "$pid" ] && chrt -p "$pid"
done
for pid in $(pgrep -f "^kworker/[01]:"); do
    printf 'kworker pid=%s ' "$pid"; chrt -p "$pid" | tail -1
done
