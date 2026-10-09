# v20_gain —— 在 v18 的 AXI 外壳上，给核加一个「每段独立增益」寄存器
#
# 和 v18_axi_shell.tcl 的差别：**只多最后一条 set_directive_interface**，
# 对应签名里新加的 `band_gain` 数组参数。算法一行没动。
#
# ⚠️ 为什么 band_gain 必须加在签名**最后**：
#    HLS 给 AXI4-Lite 排偏移是按参数在签名里的**顺序**来的。
#    往中间插一个参数，它后面所有寄存器（reset / bypass）的地址会整体后移，
#    已经在板上跑着的 Python 驱动（board/scripts/fir_core.py）当场失效 ——
#    而那种错**不会报错**，只会写错地方。
#    加在末尾：新寄存器落在现有最后一个（bypass @0x48）后面，
#    老地址一个都不动。加完必须去读生成的 `xfir_multiband_hw.h` 核对。
#
# ---- 数据口：两个 AXI4-Master，一个读 in、一个写 out ----
# -depth 是告诉 HLS「这块最大多少点」，它按这个数决定突发长度和地址位宽。
# 取 8192：够放 48 kHz 下 170 ms 的音频，远超一块的实际长度，也不吃太多地址位。
set_directive_interface -mode m_axi -depth 8192 "fir_multiband" in
set_directive_interface -mode m_axi -depth 8192 "fir_multiband" out

# ---- 为什么给 in / out 再挂一次 s_axilite ----
# 只写 m_axi 的话，核去 DDR 读写的**基地址是写死的**（0），没法让软件指定缓冲区。
# 实测证据（v18 第一版）：生成出来的 fir_multiband_control_s_axi.v 里
# 只译码到 0x00~0x34，**没有 in_r / out_r 这两个基地址寄存器** —— 核永远从地址 0
# 取数，等于没法用。补上这两条，基地址才进控制口，Python 才能写缓冲区地址。
set_directive_interface -mode s_axilite -bundle control "fir_multiband" in
set_directive_interface -mode s_axilite -bundle control "fir_multiband" out

# ---- 控制寄存器：AXI4-Lite ----
# 标量直接进寄存器：
set_directive_interface -mode s_axilite -bundle control "fir_multiband" length
set_directive_interface -mode s_axilite -bundle control "fir_multiband" reset
set_directive_interface -mode s_axilite -bundle control "fir_multiband" bypass
# 数组参数（各 4 个 int16）进 s_axilite 会变成一块连续的寄存器区，
# 正好就是 §二 表里的 DRC_THR_1~4 / DRC_RATIO_1~4 / BAND_GAIN_1~4。
set_directive_interface -mode s_axilite -bundle control "fir_multiband" drc_thr
set_directive_interface -mode s_axilite -bundle control "fir_multiband" drc_ratio
# ↓↓↓ 这一条是 v20 相对 v18 唯一的新增。去掉它，band_gain 会被 HLS
#     当成 ap_memory（BRAM 端口），长出来的脚挂不进 block design。
set_directive_interface -mode s_axilite -bundle control "fir_multiband" band_gain
# return（函数是 void，但写了没坏处，HLS 会忽略）
set_directive_interface -mode s_axilite -bundle control "fir_multiband" return

# ---- 复现命令（在仓库根目录跑）----
#
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v20_gain \
#     HLS_DIRECTIVES=build/hls/directives/v20_gain.tcl \
#     HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18 -DFIR_ACC_SAT=0 -DFIR_N_TAPS=193 -DFIR_SHIFT_CHAIN=1" \
#     cmd /c "E:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat -f build\hls\run_synth.tcl"
#
# CFLAGS **逐字照抄出货那一版**（export_ip_v19.log 第 13 行）。
# 少一个 -DFIR_SHIFT_CHAIN=1，出来的就是 450 拍/采样那一版，跟已有的数全对不上。
#
# 四关（顺序不能换，前两关不过就别往下走）：
#   ① csynth 看两个数：每采样拍数（v19 是 28，加一级乘法应该只涨几拍）、
#      合并循环 VITIS_LOOP_*_10 的 II 是不是还等于 1（不是 1 = 乘法器没复用，要查）
#   ② 读生成的 xfir_multiband_hw.h 核对偏移：老寄存器一个都没动，
#      band_gain 落在 0x50~0x57
#   ③ csim：单位增益下输出必须和加增益之前**逐位相同**
#      MSYS_NO_PATHCONV=1 HLS_LABEL=csim_v20 \
#        HLS_CFLAGS="<同上>" \
#        cmd /c "E:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat -f build\hls\run_csim.tcl"
#   ④ 走 run_vivado_impl.tcl 看 WNS（现在 +1.283 ns，加一级乘法后仍要为正），
#      再导出 IP、重跑 bitstream、上板做逐位自检
