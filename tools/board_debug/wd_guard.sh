#!/bin/sh
# ⚠ 2026-10-16 收尾补:**这条自救路在这块板上已被实测判死,留作记录。**
#   武装 + 一直喂 + 关 fd 都正常,但停喂 90 秒不复位;换 `setsid nohup` 起也不复位;
#   一锤定音那次从武装到自然退出近 380 秒一次没喂,uptime 553→605 一秒也没重启。
#   猜测:`cadence_wdt` 本来是给 ZynqMP 的 WWDT 写的,套到 Zynq-7000 上 probe 过了,
#   但 start 里没真打开复位输出;板上 /sys/class/watchdog/watchdog0/ 底下
#   也没有标准的 state/timeout/nowayout/identity,这本身就是"不走标准那套"的信号。
# wd_guard.sh —— 只在 fir_live.py 还在跑的时候武装并喂硬件看门狗。
#
# 为什么需要它
# ------------
# 2026-10-16 晚第三次死机是"双核全死":串口一个字节都不吐、不 panic、不重启,
# 只能手按电源。那种状态下内核连调度 tick 都进不去,`panic_on_rcu_stall`
# 也打不出来 —— 软件层完全没法自救。
#
# Zynq 片上的 SWDT(`f8005000.watchdog`,内核把它报成
# `cdns-wdt ... Xilinx Watchdog Timer with timeout 10s`)是**独立于 CPU 的**:
# 只要不给它喂狗,10 秒后它就复位整个 SoC。这是唯一能在"双核全死"时
# 自动把板子拉回来的办法。
#
# 设计要点
# --------
# - **只在 fir_live.py 存在时武装**:它一退出就关掉 fd 撤防,
#   免得平时把板子每 10 秒重启一次(那比死机还烦)。
# - 喂狗间隔 2 秒 << 10 秒超时,留足余量。
# - 这个循环本身跑不起来(读目录、fork 都做不了)就说明系统已经不行了 ——
#   于是没人喂狗,看门狗复位。**"喂不了狗"就是判据,不需要额外的检测。**
#
# 危险:如果内核编了 CONFIG_WATCHDOG_NOWAYOUT,关掉 fd 是撤不掉防的,
# 那样这个脚本一死板子就会 10 秒一重启地循环。所以先检查 nowayout,
# 是 1 就干脆不武装(宁可没有这个网,也不要一个拆不掉的雷)。

WD=/dev/watchdog
NOWAYOUT=/sys/class/watchdog/watchdog0/nowayout

if [ ! -c "$WD" ]; then
  echo "wd_guard: 没有 $WD,不武装"
  exit 1
fi
if [ -r "$NOWAYOUT" ] && [ "$(cat "$NOWAYOUT")" != "0" ]; then
  echo "wd_guard: nowayout=$(cat "$NOWAYOUT") —— 关不掉 fd,不武装"
  exit 1
fi

armed=0
while :; do
  if pgrep -f 'fir_live\.py' >/dev/null 2>&1; then
    if [ $armed -eq 0 ]; then
      exec 9>"$WD" || exit 1        # 打开 = 开始计时
      printf 'wd_guard: 武装 up=%s\n' "$(cut -d. -f1 /proc/uptime)"
      armed=1
    fi
    printf '.' >&9 || exit 1        # 喂狗
  elif [ $armed -eq 1 ]; then
    exec 9>&-                       # fir_live 干净退出了 → 撤防
    echo "wd_guard: 撤防"
    armed=0
  fi
  sleep 2
done
