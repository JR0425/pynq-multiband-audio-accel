#!/usr/bin/env bash
# 单轮 BD-only，但先把 TMP/TEMP 换成**真正的 Windows 路径**再启动 vivado。
#
# 2026-10-28 发现的关键线索：Git Bash 里 TMP=/tmp、TEMP=/tmp（POSIX 路径）。
# Vivado 是 Windows 程序，会把这些当成 "\tmp"（当前盘根下的 tmp），
# 于是它在需要临时文件时随机失败 —— 现象正是"随机读不到自己的 tcl 文件、
# 每次点到的文件都不一样、报错里还带 : No error"。这些文件本身完全正常。
#
# 用法：bash tools/board_debug/bd_tmpfix_test.sh
set -u
ROOT=/d/pynq-multiband-audio-accel
TCL="D:\pynq-multiband-audio-accel\board\overlay\build_audio_pl.tcl"
VIV="E:\Xilinx\Vivado\2020.2\bin\vivado.bat"
VTMP="$ROOT/tools/board_debug/_tmp/vtmp"
LOG="$ROOT/tools/board_debug/_tmp/bd_tmpfix.log"

mkdir -p "$VTMP"
WTMP="$(cygpath -w "$VTMP")"

if tasklist 2>/dev/null | grep -qi vivado; then
    echo "有残留 vivado，先停"
    taskkill //F //IM vivado.exe > /dev/null 2>&1
    sleep 3
fi

echo "TMP/TEMP = $WTMP"
cd "$ROOT" || exit 1
FIR_PL_BD_ONLY=1 TMP="$WTMP" TEMP="$WTMP" MSYS_NO_PATHCONV=1 \
    cmd /c "$VIV -mode batch -nojournal -nolog -source $TCL" > "$LOG" 2>&1

echo "=== 结果 ==="
grep -aE "couldn't read|^ERROR|BD_ONLY|ADDR MAP|### regs|### M04|SEG_fir" "$LOG" | head -20
if grep -qa "BD_ONLY" "$LOG" && ! grep -qaE "^ERROR" "$LOG"; then
    echo "BD_OK_TMPFIX"
else
    echo "BD_FAIL_TMPFIX"
fi
