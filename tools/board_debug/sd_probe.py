# ⚠ 2026-10-16 收尾补:主机侧,**全是只读命令**。板子一回来就先读它自己的账本。
#   用途:回答"反复被迫断电有没有把 SD 卡弄坏"。判据见
#   skill/pitfalls/pynq_shutdown_and_power.md 的「板子硬死的时候人是被迫断电的」。
"""板子一回来就读一遍它自己的健康账本 —— **全是只读命令,不动任何状态**。

为什么先读这个:死机现场反复出现 `sync` / `jbd2/mmcblk0p2` 卡在 D、mmc0 中断
冻住,而且**每次开机都是 `EXT4-fs (mmcblk0p2): recovery complete`(脏关机)**。
rootfs 正好就在那张 SD 上(`root=/dev/mmcblk0p2`),而这张卡在内核里的名字是
"asdfg" —— 不是任何正规品牌的产品名。如果卡本身在坏,那么前面所有
"改个环境变量再试试"都是在别的地方找凶手。

这一遍问四件事:
  1. 内核自己有没有报过 mmc / ext4 的 I/O 错(有没有、多少次、最后一次是什么时候)
  2. ext4 的账本(tune2fs):挂载次数、上次 fsck、错误行为
  3. 硬件读数(XADC 电压 + 片温):区分"是不是供电塌了/过热"
  4. 那堆 daily 定时器(apt-daily / man-db / fstrim / logrotate / tmpfiles)下次什么时候响,
     因为板子的 RTC 是假的,时钟一跳就可能把错过的 "daily" 任务一起点上,集中写 SD

用法:
  python sd_probe.py <输出目录>
板子不在就一直等(最多 45 分钟),等到了马上读。
"""

import os
import shlex
import sys
import time

import paramiko

HOST, USER, PASSWD = "192.168.2.99", "xilinx", "xilinx"

# 一个 sh 脚本,整段交给 sudo 跑(单引号由 shlex.quote 负责转义)
BUNDLE = r'''
echo "== uptime / load =="
cat /proc/uptime; cat /proc/loadavg

echo "== 内核报过的 mmc / ext4 / i/o 错 (tail 80) =="
dmesg | grep -iE "mmc|sdhci|ext4|jbd2|i/o error|blk_update|timeout|error -" | tail -80

echo "== SD 卡身份 =="
for f in name oemid manfid serial date fwrev hwrev; do
  printf '%s=' "$f"; cat /sys/block/mmcblk0/device/$f 2>&1
done

echo "== ext4 账本 =="
tune2fs -l /dev/mmcblk0p2 2>&1 | grep -iE "state|mount count|check|error|block count|reserved|created|last|revision|features|lifetime|orphan"

echo "== ext4 运行期账本 (/sys/fs/ext4) —— errors_count / first_error_* / last_error_* 从来没读过 =="
for f in /sys/fs/ext4/mmcblk0p2/[a-z]*; do
  n=${f##*/}
  case "$n" in
    lifetime_write_kbytes|session_write_kbytes|errors_count|\
    first_error_time|first_error_func|first_error_line|first_error_blocks|\
    last_error_time|last_error_func|last_error_line|\
    first_orphan_cleanup_time|delayed_allocation_blocks|extent_cache_can_cache)
        printf '%s=' "$n"; cat "$f" 2>&1 ;;
  esac
done

echo "== mmc 总线硬件错误计数 (debugfs, 只读) —— SDHCI 自己数的命令超时/CRC 错 =="
if [ -d /sys/kernel/debug/mmc0 ]; then
  cat /sys/kernel/debug/mmc0/err_stats 2>&1
  echo "-- ios(当前总线：时钟/位宽/时序)--"
  cat /sys/kernel/debug/mmc0/ios 2>&1
  echo "-- 卡那侧的 debugfs --"
  ls /sys/kernel/debug/mmc0/ 2>&1
else
  echo "没有 /sys/kernel/debug/mmc0"
  mount 2>/dev/null | grep -i debugfs
fi

echo "== 卡自己报的磨损/寿命读数(有才打) =="
for f in life_time pre_eol_info type; do
  printf '%s=' "$f"; cat /sys/block/mmcblk0/device/$f 2>&1
done

echo "== mmcblk0 累计 IO =="
grep mmcblk0 /proc/diskstats
echo "-- 写入吞吐(扇区*512 / 写耗时ms*) --"
awk '$3=="mmcblk0"{printf "wr_KiB=%.0f  wr_ms=%s  有效速率=%.1f KB/s\n", $10/2, $11, ($11>0? ($10*512/1024)/($11/1000):0)}' /proc/diskstats

echo "== 定时器(下次响的时刻)=="
systemctl list-timers --all --no-pager 2>&1 | head -25

echo "== journald 状态 =="
systemctl is-active systemd-journald

echo "== XADC 电压(VCCINT/VCCAUX/VCCBRAM/温度)=="
for f in /sys/bus/iio/devices/iio:device*/in_voltage*_raw \
         /sys/bus/iio/devices/iio:device*/in_temp*_raw \
         /sys/bus/iio/devices/iio:device*/in_voltage*_scale; do
  [ -r "$f" ] && printf '%s=' "$f" && cat "$f"
done
echo "-- hwmon/thermal --"
for f in /sys/class/hwmon/hwmon*/temp*_input /sys/class/thermal/thermal_zone*/temp; do
  [ -r "$f" ] && printf '%s=' "$f" && cat "$f"
done

echo "== 文件系统余量 =="
df -h / /dev/shm 2>&1

echo "== END sd_probe =="
'''


def connect(timeout=20):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWD,
              timeout=timeout, banner_timeout=timeout, auth_timeout=timeout)
    return c


def uptime():
    try:
        c = connect(timeout=8)
        try:
            _i, out, _e = c.exec_command("cut -d. -f1 /proc/uptime", timeout=15)
            return int(float(out.read().decode().strip()))
        finally:
            c.close()
    except Exception:                                            # noqa: BLE001
        return -1


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "tools/_tmp/round5"
    os.makedirs(outdir, exist_ok=True)
    dest = os.path.join(outdir, "sd_probe.txt")

    t0 = time.time()
    print("[%s] 等板子(最多 45 分钟)..." % time.strftime("%H:%M:%S"))
    while time.time() - t0 < 45 * 60:
        u = uptime()
        if u >= 0:
            print("[%s] 板子回来了 up=%d,等 25 秒让它起齐"
                  % (time.strftime("%H:%M:%S"), u))
            time.sleep(25)
            break
        time.sleep(10)
    else:
        print("板子一直没回来,收工")
        return 1

    c = connect()
    try:
        cmd = "echo xilinx | sudo -S sh -c " + shlex.quote(BUNDLE)
        _i, out, err = c.exec_command(cmd, timeout=180)
        data = out.read().decode("utf-8", "replace") + \
            err.read().decode("utf-8", "replace")
    finally:
        c.close()

    with open(dest, "w", encoding="utf-8") as f:
        f.write(data)
    print(data)
    print("[%s] -> %s (%d bytes)" % (time.strftime("%H:%M:%S"), dest, len(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
