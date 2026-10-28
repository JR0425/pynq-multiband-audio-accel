#!/usr/bin/env bash
# 复用 22:49 的工程直跑综合→实现→位流（绕开 create_bd_design 的随机 rsb 失败）。
#   bash build/run_reuse_batch2.sh
set -u
cd /d/pynq-multiband-audio-accel || exit 1
LOG=tools/board_debug/_tmp/reuse_batch2.log
MSYS_NO_PATHCONV=1 cmd /c "E:\Xilinx\Vivado\2020.2\bin\vivado.bat -mode batch -source D:\pynq-multiband-audio-accel\build\reuse_batch2.tcl" > "$LOG" 2>&1
rc=$?
grep -E "^###" "$LOG"
exit $rc
