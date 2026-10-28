#!/usr/bin/env bash
# 构建全 PL 版 overlay（fir_pl.bit）。
#
# Vivado 2020.2 在 Windows 上 create_bd_design 会随机报
# "couldn't read file ... : no such file or directory"（每次点到的文件还不一样），
# 过一阵子再跑就好 —— build/run_cap_build2.sh 里记过同样的坑。
#
# 2026-10-28 修了两个真问题（原来这两条让"重试"反而越重试越坏）：
#   ① 每轮开始前**结束残留的 vivado.exe**。上一轮失败后它的子进程往往还活着，
#      两个实例同时跑会抢 IP 缓存，第二轮必挂在
#      "Failed to create Customization object ...clk_wiz...";
#   ② 成功判定不再 grep "### DONE"，那条会命中被回显的 tcl 本身（假绿灯）。
#      改成"这一轮之后 fir_pl.bit 真的被新写过" + 日志里有独立的 ### DONE 行。
#
# 用法：
#   sh build/run_pl_build.sh            # 完整构建
#   BD_ONLY=1 sh build/run_pl_build.sh  # 只验 BD 接线，过了就停
cd /d/pynq-multiband-audio-accel || exit 1

# 只允许一个构建循环。
#
# 2026-10-28 的真教训：两个循环同时在跑，各自起一个 vivado.exe 往**同一个**
# build/vivado/audio_pl 做 create_project -force（那步会删目录重建），
# 于是一个在写、一个在读 ⇒ 每次报的"文件不存在"都不一样，而文件其实都在。
# 加上每轮开头的 taskkill，两个循环还会互相杀掉对方正在写的 vivado。
#
# 锁必须"谁上锁谁解锁"：原来无条件 rmdir，任何旧循环退出都会把新循环的锁删掉，
# 于是第三个又能进来。带 PID 之后，只有主人能删，僵死锁可以自动接管。
LOCK=tools/board_debug/_tmp/pl_build.lock
if [ -f "$LOCK" ]; then
  old=$(cat "$LOCK" 2>/dev/null)
  if [ -n "$old" ] && kill -0 "$old" 2>/dev/null; then
    echo "另一个构建循环还在跑（PID $old），退出"
    exit 1
  fi
  echo "接管僵死锁（PID ${old:-?} 已不在）"
fi
echo $$ > "$LOCK"
trap '[ "$(cat "$LOCK" 2>/dev/null)" = "$$" ] && rm -f "$LOCK"' EXIT

LOG=tools/board_debug/_tmp/pl_build.log
RC=tools/board_debug/_tmp/pl_build.rc
MARK=tools/board_debug/_tmp/pl_build_mark
BIT=board/overlay/fir_pl.bit
: > "$RC"

kill_stray() {
  MSYS_NO_PATHCONV=1 taskkill /F /IM vivado.exe >/dev/null 2>&1
  sleep 3
}

for i in 1 2 3 4 5 6 7 8; do
  kill_stray
  rm -rf build/vivado/audio_pl
  touch "$MARK"
  if [ -n "$BD_ONLY" ]; then
    export FIR_PL_BD_ONLY=1
  fi
  MSYS_NO_PATHCONV=1 cmd /c "E:\Xilinx\Vivado\2020.2\bin\vivado.bat -mode batch -source D:\pynq-multiband-audio-accel\board\overlay\build_audio_pl.tcl" > "$LOG" 2>&1

  marked=0
  [ -n "$BD_ONLY" ] && marked=1
  [ "$marked" = "0" ] && [ "$BIT" -nt "$MARK" ] && marked=1

  if [ "$marked" = "1" ] && grep -q '^### DONE' "$LOG"; then
    echo "round $i OK at $(date +%H:%M:%S)" >> "$RC"
    break
  fi
  echo "round $i failed at $(date +%H:%M:%S): $(grep -m1 '^ERROR' "$LOG")" >> "$RC"
  cp "$LOG" "tools/board_debug/_tmp/pl_build_round$i.log"
  sleep 60
done
kill_stray
echo "END" >> "$RC"
