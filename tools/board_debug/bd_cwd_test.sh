#!/usr/bin/env bash
# 单个 BD-only 轮次，但把 CWD 放在一个很短的系统目录里再启动 vivado。
#
# 动机：报错里出现过
#     Error in evaluating command source [rdi::utils::find_approot_file scripts/xguifrmwork/init.tcl]
# 也就是 Vivado 在**相对路径**上找不到自己的安装根。如果它对 CWD 有依赖，
# 把 CWD 换成一个干净短路径可能就绕过去了。这个脚本就是用来验这一条的：
# 同样的 tcl、同样的工程目录，唯一差别是 CWD。
set -u
TCL="D:\pynq-multiband-audio-accel\board\overlay\build_audio_pl.tcl"
VIV="E:\Xilinx\Vivado\2020.2\bin\vivado.bat"
LOG="D:\pynq-multiband-audio-accel\tools\board_debug\_tmp\bd_cwd_test.log"

if tasklist 2>/dev/null | grep -qi vivado; then
    echo "有残留 vivado，先停"
    taskkill //F //IM vivado.exe > /dev/null 2>&1
    sleep 3
fi

cd /c/Windows/Temp || exit 1
echo "CWD = $(pwd)"
FIR_PL_BD_ONLY=1 MSYS_NO_PATHCONV=1 cmd /c "$VIV -mode batch -nojournal -nolog -source $TCL" > "$LOG" 2>&1

echo "=== 结果 ==="
grep -aE "couldn't read|^ERROR|BD_ONLY|ADDR MAP|### regs|### M04" "$LOG" | head -20
if grep -qa "BD_ONLY" "$LOG" && ! grep -qaE "^ERROR" "$LOG"; then
    echo "BD_OK_FROM_TEMP"
else
    echo "BD_FAIL_FROM_TEMP"
fi
