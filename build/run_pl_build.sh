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
  # 2026-10-28 下午的教训：在某些执行环境里 taskkill 会被静默拦截（返回码
  # 都看不到），残留的 vivado 一直活着，下一轮照常起来 → 并发 → 每轮必挂
  # "随机读不到某个 tcl"。16:30-16:39 连挂 6 轮就是这么来的。
  # 所以杀完必须**验尸**：还看到 vivado.exe 就大声失败，绝不带病进下一轮。
  if MSYS_NO_PATHCONV=1 tasklist /FI "IMAGENAME eq vivado.exe" 2>/dev/null | grep -q vivado.exe; then
    echo "FATAL: vivado.exe 没杀干净（taskkill 被拦/权限不足）——先手动清掉再跑" >> "$RC"
    exit 1
  fi
}

for i in 1 2 3 4 5 6 7 8; do
  kill_stray
  rm -rf build/vivado/audio_pl
  touch "$MARK"
  if [ -n "$BD_ONLY" ]; then
    export FIR_PL_BD_ONLY=1
  fi
  # 每轮换一个**全新**工程目录。2026-10-28 二次定位：靠 rm -rf 复用
  # build/vivado/audio_pl 是不安全的 —— taskkill 之后 Windows 的文件句柄要过几秒
  # 才释放，rm -rf 遇到忙文件会跳过（不报错），于是下一轮 create_project -force
  # 吃到一个半坏的工程（.cache/project.wpc 还在、.xpr 没了），接着
  # create_bd_design 加载 rsb 时就报随机一个文件 "no such file or directory"。
  # 证据：同一支 tcl 跑全新目录 build/vivado/bdtest2 → BD_ONLY 一次通过；
  #       跑 build/vivado/audio_pl → 连续 12 轮挂。换名字立刻就好。
  TS=$(date +%m%d_%H%M%S)
  RUN_NAME="pl_${i}_${TS}"
  export FIR_PL_BUILD_DIR="D:/pynq-multiband-audio-accel/build/vivado/$RUN_NAME"
  echo "round $i build_dir = $RUN_NAME" >> "$RC"
  MSYS_NO_PATHCONV=1 cmd /c "E:\Xilinx\Vivado\2020.2\bin\vivado.bat -mode batch -source D:\pynq-multiband-audio-accel\board\overlay\build_audio_pl.tcl" > "$LOG" 2>&1

  # 成功判据分两种模式：BD_ONLY 走 exit，根本到不了 "### DONE"。
  if [ -n "$BD_ONLY" ]; then
    grep -q '^### ADDR MAP OK' "$LOG" && ok=1 || ok=0
  else
    ok=0
    [ "$BIT" -nt "$MARK" ] && grep -q '^### DONE' "$LOG" && ok=1
  fi

  if [ "$ok" = "1" ]; then
    echo "round $i OK at $(date +%H:%M:%S)" >> "$RC"
    # 成功后清掉失败轮次的空工程，别让 build/vivado 越堆越大
    for d in build/vivado/pl_*; do
      [ -d "$d" ] || continue
      [ "$(basename "$d")" = "$RUN_NAME" ] && continue
      rm -rf "$d" 2>/dev/null
    done
    break
  fi
  echo "round $i failed at $(date +%H:%M:%S): $(grep -m1 '^ERROR' "$LOG")" >> "$RC"
  cp "$LOG" "tools/board_debug/_tmp/pl_build_round$i.log"
  sleep 60
done
kill_stray
echo "END" >> "$RC"
