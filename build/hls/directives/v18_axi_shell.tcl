# v18_axi_shell —— 给核套 AXI 外壳（第 ③ 步的第一步）
#
# 为什么要有这一版：
#   核现在是「块进块出、传内存指针」的形式（`const int16_t *in` / `int16_t *out`）。
#   不加接口指令的话，Vitis HLS 默认把数组参数做成 **ap_memory**（BRAM 端口），
#   长出来的是 in_address0 / in_q0 / out_ce0 这种脚 —— 那是接到 BRAM 上的，
#   挂不进 block design 的 AXI 互联，也没有寄存器可配。
#
# 这一版把接口换掉，算法不动：
#   · in / out          → AXI4-Master（m_axi），核自己去 DDR 读写，成块地搬
#   · length / reset / bypass / drc_thr / drc_ratio → AXI4-Lite（s_axilite）
#     这一组就对应 `report/hardware_interface_spec.md` §二 那张寄存器表
#
# ⚠️ 只动接口不动算法，所以 csim 的输出必须**逐位不变**。
#    跑完综合要立刻重跑 csim 对一遍（见文件末尾的复现命令）。
#
# ⚠️ 为什么是 m_axi 而不是 axis（流）：
#    核是「块进块出」的，一次要吃一整块才吐得出结果（延迟线跨块保持）。
#    改成流式等于把核重写一遍，csim、拍数、时序全部作废。
#    m_axi 是**改动最小的那个形状**，而且和 9/26 参考 overlay 证过的那条路一致
#    （DMA/DDR → 核 → DDR，那条路上的数据是「块」不是「流」）。

# ---- 数据口：两个 AXI4-Master，一个读 in、一个写 out ----
# -depth 是告诉 HLS「这块最大多少点」，它按这个数决定突发长度和地址位宽。
# 取 8192：够放 48 kHz 下 170 ms 的音频，远超一块的实际长度，也不吃太多地址位。
set_directive_interface -mode m_axi -depth 8192 "fir_multiband" in
set_directive_interface -mode m_axi -depth 8192 "fir_multiband" out

# ---- 为什么要给 in / out 再挂一次 s_axilite ----
# 只写 m_axi 的话，核去 DDR 读写的**基地址是写死的**（0），没法让软件指定缓冲区。
# 实测证据（v18 第一版）：生成出来的 fir_multiband_control_s_axi.v 里
# 只译码到 0x00~0x34，**没有 in_r / out_r 这两个基地址寄存器** ——
# 核永远从地址 0 取数，等于没法用。
#
# 补上这两条，基地址才会变成控制口里的寄存器，Python 才能把
# 「一块 DDR 缓冲区的地址」写进去。（这是 HLS 的标准写法。）
set_directive_interface -mode s_axilite -bundle control "fir_multiband" in
set_directive_interface -mode s_axilite -bundle control "fir_multiband" out

# ---- 控制寄存器：AXI4-Lite ----
# 标量直接进寄存器：
set_directive_interface -mode s_axilite -bundle control "fir_multiband" length
set_directive_interface -mode s_axilite -bundle control "fir_multiband" reset
set_directive_interface -mode s_axilite -bundle control "fir_multiband" bypass
# 数组参数（各 4 个 int16）进 s_axilite 会变成一块连续的寄存器区，
# 正好就是 §二 表里的 DRC_THR_1~4 / DRC_RATIO_1~4。
set_directive_interface -mode s_axilite -bundle control "fir_multiband" drc_thr
set_directive_interface -mode s_axilite -bundle control "fir_multiband" drc_ratio
# return（函数是 void，但写了没坏处，HLS 会忽略）
set_directive_interface -mode s_axilite -bundle control "fir_multiband" return

# ---- 复现命令（在仓库根目录跑）----
#
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v18_axi_shell \
#     HLS_DIRECTIVES=build/hls/directives/v18_axi_shell.tcl \
#     HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18 -DFIR_ACC_SAT=0 -DFIR_N_TAPS=193" \
#     cmd /c "E:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat -f build\hls\run_synth.tcl"
#
# CFLAGS 逐字照抄 v13_sub_n193 那一轮（见 build/hls/synth_v13.log 第 14 行），
# 否则出来的不是「选定的那一版」，和已有的数对不上。
#
# 综合完要做的两件事：
#   ① 重跑 csim 确认数值逐位不变：
#      MSYS_NO_PATHCONV=1 HLS_LABEL=csim_v18_axi \
#        HLS_CFLAGS="<同上>" \
#        cmd /c "E:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat -f build\hls\run_csim.tcl"
#   ② 量 WNS：把 solution 存成 build/hls/sol_v18_axi_shell，再走 run_vivado_impl.tcl
