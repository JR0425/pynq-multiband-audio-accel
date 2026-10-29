#!/usr/bin/env bash
# 只跑 BD 段（create_bd_design → 连线 → assign_bd_address → validate），
# 不综合、不实现。用来快速迭代 BD 改动，每轮约 1–2 分钟。
#
# create_bd_design 会随机读不到自己的 tcl（mig_utils.tcl / init.tcl /
# sc_cosim_util.tcl，文件都在、能读）。单实例下也有 30–50% 的命中率，
# 所以要重试 —— 这不是我们代码的问题，见 skill/pitfalls/vivado_windows_build.md。
set -u
cd /d/pynq-multiband-audio-accel || exit 1

OUT=build/tmp/probes/bd_only.log
VIV="E:\Xilinx\Vivado\2020.2\bin\vivado.bat"
TCL="D:\pynq-multiband-audio-accel\board\overlay\build_audio_pl.tcl"

ok=0
for round in 1 2 3 4 5; do
    echo "---- 第 $round 轮 ----"
    if tasklist 2>/dev/null | grep -qi vivado; then
        echo "   残留 vivado，先停掉再跑（同一时刻只能一个）"
        taskkill //F //IM vivado.exe > /dev/null 2>&1
        sleep 3
    fi
    BD_DIR="$(cygpath -m "$PWD/build/vivado/bd_scratch")"
    FIR_PL_BD_ONLY=1 FIR_PL_BUILD_DIR="$BD_DIR" MSYS_NO_PATHCONV=1 cmd /c "$VIV -mode batch -nojournal -nolog -source $TCL" > "$OUT" 2>&1
    if grep -qa "BD_ONLY" "$OUT" && ! grep -qaE "^ERROR" "$OUT"; then ok=1; break; fi
    grep -a "couldn't read file" "$OUT" | head -2
done

echo
echo "=== 关键行 ==="
grep -aE "^###|^ERROR|CRITICAL WARNING|BD_ONLY" "$OUT" | head -30
echo
echo "=== 地址映射 ==="
grep -aA8 "==== ADDR MAP ====" "$OUT" | head -12
echo
echo "=== 新单元引脚（确认 AXI 接口被识别成 S_AXI） ==="
grep -aE "fir_pl_regs_0/(S_AXI|s_axi_aclk|gain_b0|ramp_div_b0|ramp_busy_b0)" "$OUT" | head -10
echo
if [ $ok -eq 1 ]; then
    echo "BD_OK"
else
    echo "BD_FAIL  完整日志：$OUT"
    tail -30 "$OUT"
fi
