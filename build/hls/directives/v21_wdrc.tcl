# v21_wdrc —— 在 v20_gain 上给 DRC 加 attack / release（真 WDRC）
#
# 和 v20_gain.tcl 的差别：**只多最后两条 set_directive_interface**，
# 对应签名里新加的 `drc_attack` / `drc_release` 数组参数。FIR / 相减 / 增益都没动。
#
# ⚠️ 新参数同样加在签名**最后**：HLS 按参数顺序排 AXI4-Lite 偏移，
#    中间插一个会把后面所有寄存器挪位，board/scripts/fir_core.py 当场失效。
#    加在末尾后预期偏移：
#        band_gain   0x50 ~ 0x57   （v20 已有）
#        drc_attack  0x58 ~ 0x5f   （本轮新增）
#        drc_release 0x60 ~ 0x67   （本轮新增）
#    加完必须去读生成的 `xfir_multiband_hw.h` 核对实际偏移。
#
# 寄存器里放的是 Q1.15 的**平滑步进**，不是毫秒：
#     step = round((1 - exp(-1/(tau*fs))) * 32768),  fs = 48000
#     换算在 board/scripts/fir_core.py 的 ms_to_coef() 里做，核里只有乘加。
#     传 0 = 不启用，退回 v20 的无记忆压缩（逐位相同）。
#
# ---- 数据口：两个 AXI4-Master，一个读 in、一个写 out ----
set_directive_interface -mode m_axi -depth 8192 "fir_multiband" in
set_directive_interface -mode m_axi -depth 8192 "fir_multiband" out

# ---- 基地址要进控制口，否则核只会从地址 0 取数（v18 踩过）----
set_directive_interface -mode s_axilite -bundle control "fir_multiband" in
set_directive_interface -mode s_axilite -bundle control "fir_multiband" out

# ---- 控制寄存器：AXI4-Lite ----
set_directive_interface -mode s_axilite -bundle control "fir_multiband" length
set_directive_interface -mode s_axilite -bundle control "fir_multiband" reset
set_directive_interface -mode s_axilite -bundle control "fir_multiband" bypass
set_directive_interface -mode s_axilite -bundle control "fir_multiband" drc_thr
set_directive_interface -mode s_axilite -bundle control "fir_multiband" drc_ratio
set_directive_interface -mode s_axilite -bundle control "fir_multiband" band_gain
# ↓↓↓ 这两条是 v21 相对 v20 唯一的新增。
set_directive_interface -mode s_axilite -bundle control "fir_multiband" drc_attack
set_directive_interface -mode s_axilite -bundle control "fir_multiband" drc_release
set_directive_interface -mode s_axilite -bundle control "fir_multiband" return

# ---- 复现命令（在仓库根目录跑）----
#
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v21_wdrc \
#     HLS_DIRECTIVES=build/hls/directives/v21_wdrc.tcl \
#     HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18 -DFIR_ACC_SAT=0 -DFIR_N_TAPS=193 -DFIR_SHIFT_CHAIN=1" \
#     "$VITIS_HLS_BIN" -f build/hls/run_synth.tcl
#
# CFLAGS **逐字照抄出货那一版**（v20）。少一个 -DFIR_SHIFT_CHAIN=1，
# 出来的就是 450 拍/采样那一版，跟已有的数全对不上。
#
# 四关（顺序不能换）：
#   ① csim：attack = release = 0 时输出必须和 v20_baseline_output.txt **逐字节相同**；
#      attack/release = 136/7 时和 tools/ref_model_wdrc.py **逐点相同**
#   ② csynth：看每采样拍数（v20 是 35）和资源增量
#   ③ 读 xfir_multiband_hw.h 核对偏移：老寄存器一个都没动，
#      attack 落在 0x58、release 落在 0x60
#   ④ 走 run_vivado_impl.tcl 看 WNS，再导出 IP、重跑 bitstream、上板自检
