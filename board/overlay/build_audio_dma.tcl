# 音频通路 DMA 版 overlay
#
# 和出货的 fir.bit 是**两条独立的路**：build_fir.tcl 一个字不动。
# 这一版把出货用的 audio_codec_ctrl（只有 AXI-Lite，PS 逐点读寄存器）
# 换成自己写的 i2s_duplex + axi_dma，让 PS 每块只写一次地址和长度。
#
# 结构：
#
#   PS7 M_AXI_GP0 ─ axi_ic ─┬─ axi_dma/S_AXI_LITE  0x40400000
#                           ├─ gpio_ctrl            0x43C00000
#                           └─ gpio_stat            0x43C10000
#
#   axi_dma/M_AXI_MM2S ─ S_AXI_HP0（从 DDR 读播放数据）
#   axi_dma/M_AXI_S2MM ─ S_AXI_HP1（往 DDR 写录音数据）
#
#   axi_dma/M_AXIS_MM2S ── i2s_duplex/S_AXIS   （播放：DDR → codec）
#   i2s_duplex/M_AXIS   ── axi_dma/S_AXIS_S2MM （录音：codec → DDR）
#
# 两档产物，用环境变量 FIR_AUDIO_DMA_WITH_FIR 选：
#
#   0（默认）只验回环。先证明"麦克风 → 自写 I2S → DMA → DDR → 反向放音"
#           这一条路通（PLAN 里 10/28 的止损点），产物 fir_dma.bit。
#   1       再把 v25 核插进 PS 的循环（S2MM 写 DDR → PS 调核 → MM2S 从 DDR 放），
#           产物 fir_dma_core.bit。核是 m_axi 主端，不能串进流，只能由 PS 调。
#
# 不带核的时候，i2s_duplex 自己的 ctrl[1]=hw_loop 还能在 PL 内部直接把
# 收到的流灌回来（不碰 DDR、不碰 DMA），用来单独验 I2S 收发的时序。
#
# 用法：
#   vivado -mode batch -source board/overlay/build_audio_dma.tcl
#   FIR_AUDIO_DMA_WITH_FIR=1 vivado -mode batch -source board/overlay/build_audio_dma.tcl
#   （只改接线、只验 BD 时：先 set FIR_AUDIO_DMA_BD_ONLY 1）
#
# 产物：
#   board/overlay/<out_name>.bit / <out_name>.hwh
#   board/overlay/<out_name>_synth_util.rpt / <out_name>_timing.rpt / <out_name>_util.rpt

set script_dir [file normalize [file dirname [info script]]]
set repo_root  [file normalize [file join $script_dir .. ..]]
set build_dir  [file join $repo_root build vivado audio_dma]
set part_name  xc7z020clg400-1

# ---- 开关：要不要把出货的 v25 核也放进这块 BD ----
# 0（默认）= 只验 DMA 回环：麦克风 → 自写 I2S → DMA → DDR → 反向放音。
# 1 = 再把 v25 核插进 PS 的循环：S2MM 写 DDR → PS 调核 → MM2S 从 DDR 放。
#     核是「自己去内存搬」的 AXI4 主端（m_axi_gmem），不能直接串进 AXI-Stream，
#     只能由 PS 一段段调它 —— 这就是"插进 PS 的循环"的字面意思。
#     FIR_AUDIO_DMA_WITH_FIR=1 vivado -mode batch -source ...
set WITH_FIR [expr {[info exists ::env(FIR_AUDIO_DMA_WITH_FIR)]
                    && $::env(FIR_AUDIO_DMA_WITH_FIR) ne "0"}]
# 两种产物分开命名，免得带核的那版把已经验过的回环版覆盖掉
set WITH_FIR [expr {[info exists ::env(FIR_AUDIO_DMA_WITH_FIR)]
                    && $::env(FIR_AUDIO_DMA_WITH_FIR) ne "0"}]
# 播放 FIFO 深度（2^TX_AW 字）。默认 10 = 1024 字。**这一侧深度直接就是延迟**
# （MM2S 能塞就塞，FIFO 永远贴着满），所以做浅是压延迟的主要手段；
# 但它同时要盖住 PS 的卡顿，实测 1024 字（10.5 ms）能过 60 秒不掉块。
# 用 FIR_AUDIO_DMA_TX_AW=9 编 512 字那一版，产物叫 fir_dma_tx9.bit。
set TX_AW [expr {[info exists ::env(FIR_AUDIO_DMA_TX_AW)]
                 ? $::env(FIR_AUDIO_DMA_TX_AW) : 10}]
set out_name [expr {$WITH_FIR ? "fir_dma_core"
                              : ($TX_AW == 10 ? "fir_dma" : "fir_dma_tx$TX_AW")}]
puts "### WITH_FIR = $WITH_FIR   TX_AW = $TX_AW   out_name = $out_name"

file mkdir $build_dir
cd $build_dir

create_project -force audio_dma $build_dir -part $part_name

if {$WITH_FIR} {
  set ip_repo_fir [file join $repo_root build vivado ip_repo fir_multiband_v1_0]
  if {![file exists [file join $ip_repo_fir component.xml]]} {
    error "核的 IP 仓库不在：$ip_repo_fir\n先跑 build/hls 里 export_ip 那一轮"
  }
  set_property ip_repo_paths [list $ip_repo_fir] [current_project]
  update_ip_catalog
}

add_files -norecurse [list \
  [file join $repo_root src rtl axis_fifo.v] \
  [file join $repo_root src rtl i2s_duplex.v] ]
update_compile_order -fileset sources_1

create_bd_design "audio_dma"

# ---- PS：配置逐字抄出货版，只多开一个 HP1 ----
set ps7_0 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7_0]
source [file join $script_dir ps7_config.tcl]
set_property -dict [list \
  CONFIG.PCW_GPIO_EMIO_GPIO_ENABLE {0} \
  CONFIG.PCW_GPIO_EMIO_GPIO_IO {<Select>} \
] $ps7_0
# 出货配置里 HP1 是关的。不设 DATA_WIDTH 默认就是 64 位，和 HP0 一致。
set_property -dict [list \
  CONFIG.PCW_USE_S_AXI_HP1 {1} \
  CONFIG.PCW_S_AXI_HP1_DATA_WIDTH {64} \
] $ps7_0

set DDR      [create_bd_intf_port -mode Master -vlnv xilinx.com:interface:ddrx_rtl:1.0 DDR]
set FIXED_IO [create_bd_intf_port -mode Master -vlnv xilinx.com:display_processing_system7:fixedio_rtl:1.0 FIXED_IO]
connect_bd_intf_net -intf_net ps7_0_DDR      [get_bd_intf_ports DDR]      [get_bd_intf_pins ps7_0/DDR]
connect_bd_intf_net -intf_net ps7_0_FIXED_IO [get_bd_intf_ports FIXED_IO] [get_bd_intf_pins ps7_0/FIXED_IO]

# codec 的配置走 PS 的 I2C1（EMIO 到 PL 的 U9/T9）。出货版靠音频 IP 引出来，
# 这里直接把 PS 的 IIC_1 接口做成顶层端口，一个 IP 都不需要。
set IIC_1 [create_bd_intf_port -mode Master -vlnv xilinx.com:interface:iic_rtl:1.0 IIC_1]
connect_bd_intf_net -intf_net ps7_0_IIC_1 [get_bd_intf_ports IIC_1] [get_bd_intf_pins ps7_0/IIC_1]

# ---- 复位 ----
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_0_100M
connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_0_100M/ext_reset_in]

# ---- 10 MHz MCLK（codec 的主时钟，和出货版同一个配置）----
set clk_wiz [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz_10MHz]
set_property -dict [list \
  CONFIG.CLKOUT1_JITTER {290.478} \
  CONFIG.CLKOUT1_PHASE_ERROR {133.882} \
  CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {10.000} \
  CONFIG.MMCM_CLKFBOUT_MULT_F {15.625} \
  CONFIG.MMCM_CLKOUT0_DIVIDE_F {78.125} \
  CONFIG.MMCM_DIVCLK_DIVIDE {2} \
  CONFIG.RESET_PORT {resetn} \
  CONFIG.RESET_TYPE {ACTIVE_LOW} \
] $clk_wiz

# ---- 寄存器总线 ----
set axi_ic [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_ic]
set_property -dict [list CONFIG.NUM_SI {1} \
                         CONFIG.NUM_MI [expr {$WITH_FIR ? 4 : 3}]] $axi_ic

# ---- axi_dma ----
# 简单模式（不开 SG），参数和 fir_rtl_dma 完全一致（那边已经在板上跑通过）。
# c_sg_length_width 必须显式给 26：它其实是**长度寄存器的位宽**，默认 14
# 会让超过 16383 字节的传输被静默截断（PG021 表 2-15/2-25）。
set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma]
set_property -dict [list \
  CONFIG.c_include_sg {0} \
  CONFIG.c_sg_include_stscntrl_strm {0} \
  CONFIG.c_sg_length_width {26} \
  CONFIG.c_include_mm2s {1} \
  CONFIG.c_include_s2mm {1} \
  CONFIG.c_addr_width {32} \
  CONFIG.c_m_axi_mm2s_data_width {64} \
  CONFIG.c_m_axis_mm2s_tdata_width {32} \
  CONFIG.c_m_axi_s2mm_data_width {64} \
  CONFIG.c_s_axis_s2mm_tdata_width {32} \
  CONFIG.c_include_mm2s_dre {0} \
  CONFIG.c_include_s2mm_dre {0} \
  CONFIG.c_mm2s_burst_size {16} \
  CONFIG.c_s2mm_burst_size {16} \
] $dma

set gpio_ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_ctrl]
set_property -dict [list \
  CONFIG.C_GPIO_WIDTH {32} \
  CONFIG.C_ALL_OUTPUTS {1} \
  CONFIG.C_IS_DUAL {0} \
] $gpio_ctrl

set gpio_stat [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_stat]
set_property -dict [list \
  CONFIG.C_GPIO_WIDTH {32} \
  CONFIG.C_GPIO2_WIDTH {32} \
  CONFIG.C_IS_DUAL {1} \
  CONFIG.C_ALL_INPUTS {1} \
  CONFIG.C_ALL_INPUTS_2 {1} \
] $gpio_stat

# ---- 出货核（只有 WITH_FIR=1 才放）----
if {$WITH_FIR} {
  set fir [create_bd_cell -type ip -vlnv xilinx.com:hls:fir_multiband:1.0 fir_multiband_0]
}

# ---- 自写 I2S 主端 ----
set i2s [create_bd_cell -type module -reference i2s_duplex i2s_duplex_0]
if {$TX_AW != 10} {
  puts "==== 把播放 FIFO 改成 2^$TX_AW 字 ===="
  set_property -dict [list CONFIG.TX_AW $TX_AW] $i2s
  puts "   现在的 CONFIG.TX_AW = [get_property CONFIG.TX_AW $i2s]"
}

# 流端口靠 X_INTERFACE_INFO 合成接口；没合成就在下面这段打印里看得见。
puts "==== i2s_duplex_0 的接口 ===="
foreach p [get_bd_intf_pins -quiet i2s_duplex_0/*] { puts "  $p" }
puts "==== i2s_duplex_0 的引脚 ===="
foreach p [get_bd_pins -quiet i2s_duplex_0/*] { puts "  $p" }

if {[llength [get_bd_intf_pins -quiet i2s_duplex_0/S_AXIS]] == 0} {
  error "i2s_duplex 的 S_AXIS 没被合成成接口，检查 X_INTERFACE_INFO"
}
if {[llength [get_bd_intf_pins -quiet i2s_duplex_0/M_AXIS]] == 0} {
  error "i2s_duplex 的 M_AXIS 没被合成成接口，检查 X_INTERFACE_INFO"
}

connect_bd_intf_net -intf_net ps7_0_M_AXI_GP0 [get_bd_intf_pins ps7_0/M_AXI_GP0] [get_bd_intf_pins axi_ic/S00_AXI]
connect_bd_intf_net -intf_net axi_ic_m00 [get_bd_intf_pins axi_ic/M00_AXI] [get_bd_intf_pins axi_dma/S_AXI_LITE]
connect_bd_intf_net -intf_net axi_ic_m01 [get_bd_intf_pins axi_ic/M01_AXI] [get_bd_intf_pins gpio_ctrl/S_AXI]
connect_bd_intf_net -intf_net axi_ic_m02 [get_bd_intf_pins axi_ic/M02_AXI] [get_bd_intf_pins gpio_stat/S_AXI]
if {$WITH_FIR} {
  connect_bd_intf_net -intf_net axi_ic_m03 [get_bd_intf_pins axi_ic/M03_AXI] \
                                           [get_bd_intf_pins fir_multiband_0/s_axi_control]
}

# 数据：DMA 的两个 AXI4 主口 → PS 的 AXI3 从口（中间必须垫协议转换器，
# 直接连报 [BD 41-1285] protocols incompatible）
foreach nm {mm2s s2mm} {
  set pc [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_protocol_converter:2.1 pc_$nm]
  set_property -dict [list CONFIG.SI_PROTOCOL {AXI4} CONFIG.MI_PROTOCOL {AXI3}] $pc
}
connect_bd_intf_net -intf_net dma_mm2s   [get_bd_intf_pins axi_dma/M_AXI_MM2S] [get_bd_intf_pins pc_mm2s/S_AXI]
connect_bd_intf_net -intf_net pc_mm2s_hp0 [get_bd_intf_pins pc_mm2s/M_AXI]     [get_bd_intf_pins ps7_0/S_AXI_HP0]
connect_bd_intf_net -intf_net dma_s2mm   [get_bd_intf_pins axi_dma/M_AXI_S2MM] [get_bd_intf_pins pc_s2mm/S_AXI]
connect_bd_intf_net -intf_net pc_s2mm_hp1 [get_bd_intf_pins pc_s2mm/M_AXI]     [get_bd_intf_pins ps7_0/S_AXI_HP1]

# 核自己去 DDR 搬数据 → PS 的 S_AXI_HP2（ps7_config 里本来就开着，一直空着）。
# 核这边是 32 位 AXI4，HP2 是 64 位 AXI3，宽度和协议都由这个互联做，
# 和出货版 fir.bit 里接 HP0 的写法一样。
if {$WITH_FIR} {
  set axi_ic_hp [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_ic_hp]
  set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $axi_ic_hp
  connect_bd_intf_net -intf_net fir_gmem [get_bd_intf_pins fir_multiband_0/m_axi_gmem] \
                                          [get_bd_intf_pins axi_ic_hp/S00_AXI]
  connect_bd_intf_net -intf_net hp2_ddr  [get_bd_intf_pins axi_ic_hp/M00_AXI] \
                                          [get_bd_intf_pins ps7_0/S_AXI_HP2]
}

# 数据：DMA ↔ 自写 I2S（AXI-Stream）
connect_bd_intf_net -intf_net axis_play [get_bd_intf_pins axi_dma/M_AXIS_MM2S] [get_bd_intf_pins i2s_duplex_0/S_AXIS]
connect_bd_intf_net -intf_net axis_cap  [get_bd_intf_pins i2s_duplex_0/M_AXIS] [get_bd_intf_pins axi_dma/S_AXIS_S2MM]

connect_bd_net [get_bd_pins gpio_ctrl/gpio_io_o] [get_bd_pins i2s_duplex_0/ctrl]
connect_bd_net [get_bd_pins gpio_stat/gpio_io_i]  [get_bd_pins i2s_duplex_0/stat0]
connect_bd_net [get_bd_pins gpio_stat/gpio2_io_i] [get_bd_pins i2s_duplex_0/stat1]

# ---- 时钟 / 复位 ----
set clk_pins [list \
               [get_bd_pins ps7_0/FCLK_CLK0] \
               [get_bd_pins ps7_0/M_AXI_GP0_ACLK] \
               [get_bd_pins ps7_0/S_AXI_GP0_ACLK] \
               [get_bd_pins ps7_0/S_AXI_HP0_ACLK] \
               [get_bd_pins ps7_0/S_AXI_HP1_ACLK] \
               [get_bd_pins ps7_0/S_AXI_HP2_ACLK] \
               [get_bd_pins ps7_0/M_AXI_GP1_ACLK] \
               [get_bd_pins rst_ps7_0_100M/slowest_sync_clk] \
               [get_bd_pins clk_wiz_10MHz/clk_in1] \
               [get_bd_pins axi_ic/ACLK] \
               [get_bd_pins axi_ic/S00_ACLK] \
               [get_bd_pins axi_ic/M00_ACLK] \
               [get_bd_pins axi_ic/M01_ACLK] \
               [get_bd_pins axi_ic/M02_ACLK] \
               [get_bd_pins axi_dma/s_axi_lite_aclk] \
               [get_bd_pins axi_dma/m_axi_mm2s_aclk] \
               [get_bd_pins axi_dma/m_axi_s2mm_aclk] \
               [get_bd_pins pc_mm2s/aclk] \
               [get_bd_pins pc_s2mm/aclk] \
               [get_bd_pins gpio_ctrl/s_axi_aclk] \
               [get_bd_pins gpio_stat/s_axi_aclk] \
               [get_bd_pins i2s_duplex_0/clk] ]

set rst_pins [list \
               [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] \
               [get_bd_pins clk_wiz_10MHz/resetn] \
               [get_bd_pins axi_ic/ARESETN] \
               [get_bd_pins axi_ic/S00_ARESETN] \
               [get_bd_pins axi_ic/M00_ARESETN] \
               [get_bd_pins axi_ic/M01_ARESETN] \
               [get_bd_pins axi_ic/M02_ARESETN] \
               [get_bd_pins axi_dma/axi_resetn] \
               [get_bd_pins pc_mm2s/aresetn] \
               [get_bd_pins pc_s2mm/aresetn] \
               [get_bd_pins gpio_ctrl/s_axi_aresetn] \
               [get_bd_pins gpio_stat/s_axi_aresetn] \
               [get_bd_pins i2s_duplex_0/rst_n] ]

if {$WITH_FIR} {
  lappend clk_pins [get_bd_pins axi_ic/M03_ACLK] \
                   [get_bd_pins axi_ic_hp/ACLK] \
                   [get_bd_pins axi_ic_hp/S00_ACLK] \
                   [get_bd_pins axi_ic_hp/M00_ACLK] \
                   [get_bd_pins fir_multiband_0/ap_clk]
  lappend rst_pins [get_bd_pins axi_ic/M03_ARESETN] \
                   [get_bd_pins axi_ic_hp/ARESETN] \
                   [get_bd_pins axi_ic_hp/S00_ARESETN] \
                   [get_bd_pins axi_ic_hp/M00_ARESETN] \
                   [get_bd_pins fir_multiband_0/ap_rst_n]
}

connect_bd_net {*}$clk_pins
connect_bd_net {*}$rst_pins

# ---- 顶层音频端口 ----
set audio_clk_10MHz [create_bd_port -dir O -type clk audio_clk_10MHz]
connect_bd_net [get_bd_pins clk_wiz_10MHz/clk_out1] [get_bd_ports audio_clk_10MHz]

create_bd_port -dir O bclk
create_bd_port -dir O lrclk
create_bd_port -dir O sdata_o
create_bd_port -dir I sdata_i
connect_bd_net [get_bd_pins i2s_duplex_0/bclk]    [get_bd_ports bclk]
connect_bd_net [get_bd_pins i2s_duplex_0/lrclk]   [get_bd_ports lrclk]
connect_bd_net [get_bd_pins i2s_duplex_0/sdata_o] [get_bd_ports sdata_o]
connect_bd_net [get_bd_pins i2s_duplex_0/sdata_i] [get_bd_ports sdata_i]

# codec 从地址 = 0x3B（两脚都拉高）。出货版是音频 IP 里写死的常量，这里自己给。
set cst_addr [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 cst_codec_addr]
set_property -dict [list CONFIG.CONST_WIDTH {2} CONFIG.CONST_VAL {3}] $cst_addr
create_bd_port -dir O -from 1 -to 0 codec_addr
connect_bd_net [get_bd_pins cst_codec_addr/dout] [get_bd_ports codec_addr]

# ---- 地址 ----
proc seg_of_pin {pin} {
  set s [get_bd_addr_segs -quiet -of_objects [get_bd_intf_pins $pin]]
  if {[llength $s] == 0} {
    puts "==== existing addr segs ===="
    foreach x [get_bd_addr_segs *] { puts "  $x" }
    error "no addr seg for pin $pin, see dump above"
  }
  return [lindex $s 0]
}

assign_bd_address -offset 0x40400000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin axi_dma/S_AXI_LITE] -force
assign_bd_address -offset 0x43C00000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin gpio_ctrl/S_AXI] -force
assign_bd_address -offset 0x43C10000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin gpio_stat/S_AXI] -force

if {$WITH_FIR} {
  # 0x43C20000：紧挨着两颗 gpio，不和出货版的 0x43C10000 撞（两块位流不同时加载）
  set fir_seg [get_bd_addr_segs -quiet fir_multiband_0/s_axi_control/Reg]
  if {[llength $fir_seg] == 0} {
    set fir_seg [get_bd_addr_segs -quiet -filter {NAME =~ "*fir_multiband_0*"}]
  }
  if {[llength $fir_seg] == 0} { error "找不到核控制口的地址段" }
  assign_bd_address -offset 0x43C20000 -range 0x00010000 \
    -target_address_space [get_bd_addr_spaces ps7_0/Data] $fir_seg -force

  # 核的 AXI4-Master 也要能摸到 DDR：不映射只在 validate 里是 warning，
  # 板上表现是一条都读不到（"不报错、只是全错"那一类）
  foreach sp [get_bd_addr_spaces *] {
    if {[string match "*fir_multiband_0/Data*" $sp]} {
      assign_bd_address -offset 0x00000000 -range 0x20000000 \
        [get_bd_addr_segs ps7_0/S_AXI_HP2/HP2_DDR_LOWOCM] \
        -target_address_space $sp -force
    }
  }
}

# DMA 是主端，自己的地址空间里也要能看见 DDR（不映射只在 validate 里是
# CRITICAL WARNING，能过，但板上一条都读不到 —— "不报错、只是全错"那一类）
assign_bd_address -offset 0x00000000 -range 0x20000000 \
  [get_bd_addr_segs ps7_0/S_AXI_HP0/HP0_DDR_LOWOCM] \
  -target_address_space [get_bd_addr_spaces axi_dma/Data_MM2S] -force
assign_bd_address -offset 0x00000000 -range 0x20000000 \
  [get_bd_addr_segs ps7_0/S_AXI_HP1/HP1_DDR_LOWOCM] \
  -target_address_space [get_bd_addr_spaces axi_dma/Data_S2MM] -force

puts "==== ADDR MAP ===="
foreach s [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps7_0/Data]] {
  puts "  [get_property NAME $s]  offset=[get_property offset $s]  range=[get_property range $s]"
}

regenerate_bd_layout
validate_bd_design
save_bd_design

if {[info exists ::env(FIR_AUDIO_DMA_BD_ONLY)]} {
  puts "### BD_ONLY：validate 过了就停"
  exit
}

set_property synth_checkpoint_mode None [get_files audio_dma.bd]

set wrapper [make_wrapper -files [get_files audio_dma.bd] -top]
add_files -norecurse $wrapper
add_files -fileset constrs_1 -norecurse [file join $script_dir audio_pins.xdc]

update_compile_order -fileset sources_1
set_property top audio_dma_wrapper [current_fileset]

launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
  error "综合没跑完，看 $build_dir/audio_dma.runs/synth_1/runme.log"
}
open_run synth_1
report_utilization -file [file join $script_dir ${out_name}_synth_util.rpt]

launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
  error "实现没跑完，看 $build_dir/audio_dma.runs/impl_1/runme.log"
}
open_run impl_1

set hwdef_zip [file join $script_dir $out_name.hwdef]
set hwh_path  [file join $script_dir $out_name.hwh]
write_hwdef -force -file $hwdef_zip
write_bitstream -force [file join $script_dir $out_name.bit]

# .hwdef 是一份 zip，里面直接有 .hwh，用 python 拆出来。
#
# -c 的那段脚本**必须用花括号**：Tcl 在双引号里会做命令替换，`[x for x in ...]`
# 会被当成一个叫 x 的 Tcl 命令先执行掉，于是每个候选解释器都抛错、又被 catch
# 吞掉，最后只剩一句"拆 hwh 失败"。（2026-10-25 16:25 就是这么挂的；当时以为
# 是 zip 里的文件名不对，其实跟文件名无关。）
#
# zip 里那条的名字是 BD 名（audio_dma.hwh），不是输出文件名，所以按后缀找。
set unzip_ok 0
foreach py {python python3 py C:/Users/XIA/miniconda3/python.exe} {
  if {$unzip_ok} break
  if {![catch {
    exec $py -c {
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
n = [x for x in z.namelist() if x.endswith('.hwh')][0]
open(sys.argv[2], 'wb').write(z.read(n))
} $hwdef_zip $hwh_path
  }]} { set unzip_ok 1 }
}
if {!$unzip_ok || [file size $hwh_path] == 0} {
  error "拆 hwh 失败（$hwh_path 是空的）。手工执行：python -c \"import zipfile;zipfile.ZipFile('$out_name.hwdef').extract('$out_name.hwh','.')\""
}

report_timing_summary -quiet -file [file join $script_dir ${out_name}_timing.rpt]
report_utilization   -quiet -file [file join $script_dir ${out_name}_util.rpt]

set wns  [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -delay_type max]]
puts "\n### RESULT"
puts "### WITH_FIR = $WITH_FIR"
puts "### WNS_ns = $wns"
puts "### bit    = [file join $script_dir $out_name.bit]"
puts "### hwh    = $hwh_path"
puts "### DONE"
exit
