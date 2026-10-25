# 自研 RTL —— DMA 版 + 波形抓取（PLAN §11.14 §4 的拔高档：抓 valid 时序图）
#
# 和 build_rtl_dma.tcl 是同一个系统的两份位流，区别只在数据通路上串了两个
# 直通监视器 fir_axis_monitor，把 tvalid / tready / tlast / tdata 引成普通
# 引脚，接到自研的抓取器 fir_wave_cap：
#
#   axi_dma/M_AXIS_MM2S ─ mon_in  ─ fir_stream_shell/S_AXIS
#   fir_stream_shell/M_AXIS ─ mon_out ─ axi_dma/S_AXIS_S2MM
#
# **为什么不用 Xilinx 的 ILA**（2026-10-25 查 IP-XACT 确认）：
#   ila v6.2 的 component.xml 里只有 TRIG_IN / TRIG_OUT / signal_clock /
#   SLOT_x_AXI 这几个总线接口，**没有 AXI4-Lite 从口** —— 它只能由 JTAG 经
#   dbg_hub 控制。PYNQ 板上的 JTAG 在 PL 的 USB 口上，PS 够不着。
#   system_ila 有 AXI-Lite（aximm_slave_decl），但它的寄存器映射和数据排布
#   在 IP-XACT / hwh 里一个字段都没有（只有 PG261 的人读表），要在板上靠试错
#   摸。抓取器几十行、寄存器自己定，反而确定。
#
# **这一版只出图，不出数**：串了监视器之后组合路径多一级，
# 报告里的拍数、吞吐一律引不带抓取器的 fir_rtl_dma.bit（PLAN 原话）。
#
# 地址分配（PS7 Data 空间）：
#   0x40400000  axi_dma/S_AXI_LITE
#   0x43C00000  输入侧波形缓冲  bram_ctrl_in   4 KB = 1024 个 32 位字
#   0x43C08000  输出侧波形缓冲  bram_ctrl_out  4 KB
#   0x43C10000  gpio_ctrl       bit0 = drc_en, bit1 = cap_arm, bit3:2 = trig_sel
#   0x43C20000  gpio_stat       shell 的 stat_lo / stat_hi
#   0x43C30000  gpio_cap        bit0 = 抓取 done
#
# 用法：
#   vivado -mode batch -source board/overlay/build_rtl_dma_cap.tcl
#   FIR_RTL_BD_ONLY=1 时只 validate 就退（改接线时用，省十几分钟）
#
# 产物：
#   board/overlay/fir_rtl_dma_cap.bit / fir_rtl_dma_cap.hwh
#   board/overlay/fir_rtl_dma_cap_timing.rpt / fir_rtl_dma_cap_util.rpt

set script_dir [file normalize [file dirname [info script]]]
set repo_root  [file normalize [file join $script_dir .. ..]]
set build_dir  [file join $repo_root build vivado fir_rtl_dma_cap]
set part_name  xc7z020clg400-1

file mkdir $build_dir

set ::env(FIR_RTL_REPO) $repo_root
set pre_tcl [file join $script_dir coeff_pre.tcl]
if {![file exists $pre_tcl]} { error "缺 $pre_tcl" }

cd $build_dir
create_project -force fir_rtl_dma_cap $build_dir -part $part_name

add_files -norecurse [list \
  [file join $repo_root src rtl fir_mb_fe.v] \
  [file join $repo_root src rtl fir_drc4.v] \
  [file join $repo_root src rtl fir_stream_shell.v] \
  [file join $repo_root src rtl fir_axis_monitor.v] \
  [file join $repo_root src rtl fir_wave_cap.v] ]
update_compile_order -fileset sources_1

create_bd_design "fir_rtl_dma_cap"

# ---- PS ----
set ps7_0 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7_0]
source [file join $script_dir ps7_config.tcl]
set_property -dict [list \
  CONFIG.PCW_GPIO_EMIO_GPIO_ENABLE {0} \
  CONFIG.PCW_GPIO_EMIO_GPIO_IO {<Select>} \
] $ps7_0
set_property -dict [list \
  CONFIG.PCW_USE_S_AXI_HP1 {1} \
  CONFIG.PCW_S_AXI_HP1_DATA_WIDTH {64} \
] $ps7_0

set DDR      [create_bd_intf_port -mode Master -vlnv xilinx.com:interface:ddrx_rtl:1.0 DDR]
set FIXED_IO [create_bd_intf_port -mode Master -vlnv xilinx.com:display_processing_system7:fixedio_rtl:1.0 FIXED_IO]
connect_bd_intf_net -intf_net ps7_0_DDR      [get_bd_intf_ports DDR]      [get_bd_intf_pins ps7_0/DDR]
connect_bd_intf_net -intf_net ps7_0_FIXED_IO [get_bd_intf_ports FIXED_IO] [get_bd_intf_pins ps7_0/FIXED_IO]

# ---- 复位 ----
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_0_100M
connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_0_100M/ext_reset_in]

# ---- 寄存器总线 ----
set axi_ic [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_ic]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {6}] $axi_ic

# ---- axi_dma（和出货的 DMA 版逐字相同）----
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

set gpio_cap [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_cap]
set_property -dict [list \
  CONFIG.C_GPIO_WIDTH {32} \
  CONFIG.C_ALL_INPUTS {1} \
  CONFIG.C_IS_DUAL {0} \
] $gpio_cap

# ---- 波形缓冲：两块 1024×32 ----
# 接法照抄 build_rtl.tcl（那边是同日上板验证过的）：axi_bram_ctrl 的
# SINGLE_PORT_BRAM 必须是 1，A 口一根根连普通引脚，地址用 xlslice
# 从**字节**地址取 [11:2] 变成**字**地址。
foreach nm {in out} {
  set bc [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 bram_ctrl_$nm]
  set_property -dict [list \
    CONFIG.PROTOCOL {AXI4LITE} \
    CONFIG.SINGLE_PORT_BRAM {1} \
    CONFIG.DATA_WIDTH {32} \
    CONFIG.READ_LATENCY {1} \
  ] $bc

  set bm [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 bram_$nm]
  # 顺序要紧：Byte_Size 默认 9，32 位数据宽在 9 的倍数规则下非法，
  # 所以先开字节写使能、再把 Byte_Size 改成 8，最后才设宽度。
  set_property CONFIG.Use_Byte_Write_Enable {true} $bm
  set_property CONFIG.Byte_Size {8} $bm
  set_property -dict [list \
    CONFIG.Memory_Type {True_Dual_Port_RAM} \
    CONFIG.use_bram_block {Stand_Alone} \
    CONFIG.EN_SAFETY_CKT {false} \
    CONFIG.Register_PortA_Output_of_Memory_Primitives {false} \
    CONFIG.Register_PortB_Output_of_Memory_Primitives {false} \
    CONFIG.Write_Width_A {32} \
    CONFIG.Write_Depth_A {1024} \
    CONFIG.Read_Width_A {32} \
    CONFIG.Write_Width_B {32} \
    CONFIG.Read_Width_B {32} \
    CONFIG.Enable_A {Use_ENA_Pin} \
    CONFIG.Enable_B {Use_ENB_Pin} \
    CONFIG.Operating_Mode_A {READ_FIRST} \
    CONFIG.Operating_Mode_B {READ_FIRST} \
  ] $bm

  # 4 KB 地址段 → bram_addr_a 是 12 位字节地址，取 [11:2] = 10 位字地址
  set sl [create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice:1.0 slice_$nm]
  set_property -dict [list \
    CONFIG.DIN_WIDTH {12} CONFIG.DIN_FROM {11} CONFIG.DIN_TO {2} \
    CONFIG.DOUT_WIDTH {10} \
  ] $sl

  connect_bd_net [get_bd_pins bram_ctrl_$nm/bram_addr_a]   [get_bd_pins slice_$nm/Din]
  connect_bd_net [get_bd_pins slice_$nm/Dout]              [get_bd_pins bram_$nm/addra]
  connect_bd_net [get_bd_pins bram_ctrl_$nm/bram_wrdata_a] [get_bd_pins bram_$nm/dina]
  connect_bd_net [get_bd_pins bram_ctrl_$nm/bram_en_a]     [get_bd_pins bram_$nm/ena]
  connect_bd_net [get_bd_pins bram_ctrl_$nm/bram_we_a]     [get_bd_pins bram_$nm/wea]
  connect_bd_net [get_bd_pins bram_ctrl_$nm/bram_rddata_a] [get_bd_pins bram_$nm/douta]
}

# ---- 自研外壳 + 两个直通监视器 + 抓取器 ----
set shell   [create_bd_cell -type module -reference fir_stream_shell  fir_stream_shell_0]
set mon_in  [create_bd_cell -type module -reference fir_axis_monitor mon_in]
set mon_out [create_bd_cell -type module -reference fir_axis_monitor mon_out]
set cap     [create_bd_cell -type module -reference fir_wave_cap     cap_0]

foreach m {fir_stream_shell_0 mon_in mon_out cap_0} {
  puts "==== $m 的接口 ===="
  foreach p [get_bd_intf_pins -quiet $m/*] { puts "  $p" }
}

# ---- 寄存器 / 数据总线 ----
connect_bd_intf_net -intf_net ps7_0_M_AXI_GP0 [get_bd_intf_pins ps7_0/M_AXI_GP0] [get_bd_intf_pins axi_ic/S00_AXI]
connect_bd_intf_net -intf_net axi_ic_m00 [get_bd_intf_pins axi_ic/M00_AXI] [get_bd_intf_pins axi_dma/S_AXI_LITE]
connect_bd_intf_net -intf_net axi_ic_m01 [get_bd_intf_pins axi_ic/M01_AXI] [get_bd_intf_pins gpio_ctrl/S_AXI]
connect_bd_intf_net -intf_net axi_ic_m02 [get_bd_intf_pins axi_ic/M02_AXI] [get_bd_intf_pins gpio_stat/S_AXI]
connect_bd_intf_net -intf_net axi_ic_m03 [get_bd_intf_pins axi_ic/M03_AXI] [get_bd_intf_pins bram_ctrl_in/S_AXI]
connect_bd_intf_net -intf_net axi_ic_m04 [get_bd_intf_pins axi_ic/M04_AXI] [get_bd_intf_pins bram_ctrl_out/S_AXI]
connect_bd_intf_net -intf_net axi_ic_m05 [get_bd_intf_pins axi_ic/M05_AXI] [get_bd_intf_pins gpio_cap/S_AXI]

# PS7 的 S_AXI_HP 是 AXI3，axi_dma 主口是 AXI4，中间垫协议转换器
foreach nm {mm2s s2mm} {
  set pc [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_protocol_converter:2.1 pc_$nm]
  set_property -dict [list \
    CONFIG.SI_PROTOCOL {AXI4} \
    CONFIG.MI_PROTOCOL {AXI3} \
  ] $pc
}
connect_bd_intf_net -intf_net dma_mm2s    [get_bd_intf_pins axi_dma/M_AXI_MM2S] [get_bd_intf_pins pc_mm2s/S_AXI]
connect_bd_intf_net -intf_net pc_mm2s_hp0 [get_bd_intf_pins pc_mm2s/M_AXI]     [get_bd_intf_pins ps7_0/S_AXI_HP0]
connect_bd_intf_net -intf_net dma_s2mm    [get_bd_intf_pins axi_dma/M_AXI_S2MM] [get_bd_intf_pins pc_s2mm/S_AXI]
connect_bd_intf_net -intf_net pc_s2mm_hp1 [get_bd_intf_pins pc_s2mm/M_AXI]     [get_bd_intf_pins ps7_0/S_AXI_HP1]

# ---- 流：DMA → 监视器 → 外壳 → 监视器 → DMA ----
connect_bd_intf_net -intf_net dma_to_mon_in    [get_bd_intf_pins axi_dma/M_AXIS_MM2S]        [get_bd_intf_pins mon_in/S_AXIS]
connect_bd_intf_net -intf_net mon_in_to_shell  [get_bd_intf_pins mon_in/M_AXIS]               [get_bd_intf_pins fir_stream_shell_0/S_AXIS]
connect_bd_intf_net -intf_net shell_to_mon_out [get_bd_intf_pins fir_stream_shell_0/M_AXIS]  [get_bd_intf_pins mon_out/S_AXIS]
connect_bd_intf_net -intf_net mon_out_to_s2mm  [get_bd_intf_pins mon_out/M_AXIS]              [get_bd_intf_pins axi_dma/S_AXIS_S2MM]

# ---- 寄存器和抓取器 ----
connect_bd_net [get_bd_pins gpio_stat/gpio_io_i]  [get_bd_pins fir_stream_shell_0/stat_lo]
connect_bd_net [get_bd_pins gpio_stat/gpio2_io_i] [get_bd_pins fir_stream_shell_0/stat_hi]

# gpio_ctrl 的 32 位输出拆三份：给外壳的整字，arm 取 bit1，trig_sel 取 [3:2]
connect_bd_net [get_bd_pins gpio_ctrl/gpio_io_o] [get_bd_pins fir_stream_shell_0/ctrl]

set sl_arm [create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice:1.0 slice_arm]
set_property -dict [list CONFIG.DIN_WIDTH {32} CONFIG.DIN_FROM {1} \
                         CONFIG.DIN_TO {1} CONFIG.DOUT_WIDTH {1}] $sl_arm
connect_bd_net [get_bd_pins gpio_ctrl/gpio_io_o] [get_bd_pins slice_arm/Din]
connect_bd_net [get_bd_pins slice_arm/Dout]      [get_bd_pins cap_0/arm]

set sl_trig [create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice:1.0 slice_trig]
set_property -dict [list CONFIG.DIN_WIDTH {32} CONFIG.DIN_FROM {3} \
                         CONFIG.DIN_TO {2} CONFIG.DOUT_WIDTH {2}] $sl_trig
connect_bd_net [get_bd_pins gpio_ctrl/gpio_io_o] [get_bd_pins slice_trig/Din]
connect_bd_net [get_bd_pins slice_trig/Dout]     [get_bd_pins cap_0/trig_sel]

# 监视器 → 抓取器
connect_bd_net [get_bd_pins mon_in/dbg_tvalid]  [get_bd_pins cap_0/in_valid]
connect_bd_net [get_bd_pins mon_in/dbg_tready]  [get_bd_pins cap_0/in_ready]
connect_bd_net [get_bd_pins mon_in/dbg_tlast]   [get_bd_pins cap_0/in_last]
connect_bd_net [get_bd_pins mon_in/dbg_tdata]   [get_bd_pins cap_0/in_tdata]
connect_bd_net [get_bd_pins mon_out/dbg_tvalid] [get_bd_pins cap_0/out_valid]
connect_bd_net [get_bd_pins mon_out/dbg_tready] [get_bd_pins cap_0/out_ready]
connect_bd_net [get_bd_pins mon_out/dbg_tlast]  [get_bd_pins cap_0/out_last]
connect_bd_net [get_bd_pins mon_out/dbg_tdata]  [get_bd_pins cap_0/out_tdata]

# 抓取器 → 两块 BRAM 的 B 口（B 口只写，选择器那一侧走 A 口读）
connect_bd_net [get_bd_pins cap_0/bram_en]  [get_bd_pins bram_in/enb]  [get_bd_pins bram_out/enb]
connect_bd_net [get_bd_pins cap_0/bram_addr] [get_bd_pins bram_in/addrb] [get_bd_pins bram_out/addrb]
connect_bd_net [get_bd_pins cap_0/bram_din_in]  [get_bd_pins bram_in/dinb]
connect_bd_net [get_bd_pins cap_0/bram_din_out] [get_bd_pins bram_out/dinb]

# 开了 Use_Byte_Write_Enable 之后 B 口多一根 web（按字节写使能）。
# 板上第一次跑不通的另一半原因也在这里：**只把 enb 拉高不会写进 BRAM**。
# 实测这个 blk_mem_gen 的 web 是 **1 位**（Byte_Size 在 8.4 里没落成 8），
# 所以常量给 1 位；接 4 位会在 BD 里被截成最低位，看着能过、其实只在 WSTRB=0xF 时写。
set const_ff [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 const_ff]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $const_ff
connect_bd_net [get_bd_pins const_ff/dout] [get_bd_pins bram_in/web] [get_bd_pins bram_out/web]

# 抓取器 done → gpio_cap bit0（高位补 0；xlconcat 的 In0 是最低位，
# 接反了 done 会跑到 bit31 —— 板上第一次就是读到 0x80000000 才发现的）
set const0 [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 const_zero31]
set_property -dict [list CONFIG.CONST_WIDTH {31} CONFIG.CONST_VAL {0}] $const0
set cat_cap [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 cat_cap]
set_property -dict [list CONFIG.NUM_PORTS {2}] $cat_cap
connect_bd_net [get_bd_pins cap_0/done]        [get_bd_pins cat_cap/In0]
connect_bd_net [get_bd_pins const_zero31/dout] [get_bd_pins cat_cap/In1]
connect_bd_net [get_bd_pins cat_cap/dout]      [get_bd_pins gpio_cap/gpio_io_i]

# ---- 时钟（同一个源脚只能出现一次，BD 41-249）----
# ⚠️ 两块 blk_mem_gen 的 clka/clkb 必须在这张表里。2026-10-25 第一版漏了这四根：
# BD 照样 validate 过、位流照样出、抓取器 done 照样置起，但 **PS 写不进也读不回、
# 抓取器写进去的全是 0** —— 因为 BRAM 根本没有时钟。这种错在图上表现为"全零"，
# 很容易被当成位序或地址接错，实际是少接一根时钟。
set clk_pins [list \
  [get_bd_pins ps7_0/M_AXI_GP0_ACLK] \
  [get_bd_pins ps7_0/S_AXI_GP0_ACLK] \
  [get_bd_pins ps7_0/S_AXI_HP0_ACLK] \
  [get_bd_pins ps7_0/S_AXI_HP1_ACLK] \
  [get_bd_pins ps7_0/S_AXI_HP2_ACLK] \
  [get_bd_pins ps7_0/M_AXI_GP1_ACLK] \
  [get_bd_pins rst_ps7_0_100M/slowest_sync_clk] \
  [get_bd_pins axi_ic/ACLK] \
  [get_bd_pins axi_ic/S00_ACLK] \
  [get_bd_pins axi_ic/M00_ACLK] \
  [get_bd_pins axi_ic/M01_ACLK] \
  [get_bd_pins axi_ic/M02_ACLK] \
  [get_bd_pins axi_ic/M03_ACLK] \
  [get_bd_pins axi_ic/M04_ACLK] \
  [get_bd_pins axi_ic/M05_ACLK] \
  [get_bd_pins axi_dma/s_axi_lite_aclk] \
  [get_bd_pins axi_dma/m_axi_mm2s_aclk] \
  [get_bd_pins axi_dma/m_axi_s2mm_aclk] \
  [get_bd_pins pc_mm2s/aclk] \
  [get_bd_pins pc_s2mm/aclk] \
  [get_bd_pins gpio_ctrl/s_axi_aclk] \
  [get_bd_pins gpio_stat/s_axi_aclk] \
  [get_bd_pins gpio_cap/s_axi_aclk] \
  [get_bd_pins bram_ctrl_in/s_axi_aclk] \
  [get_bd_pins bram_ctrl_out/s_axi_aclk] \
  [get_bd_pins bram_in/clka] \
  [get_bd_pins bram_in/clkb] \
  [get_bd_pins bram_out/clka] \
  [get_bd_pins bram_out/clkb] \
  [get_bd_pins fir_stream_shell_0/clk] \
  [get_bd_pins mon_in/clk] \
  [get_bd_pins mon_out/clk] \
  [get_bd_pins cap_0/clk] ]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] {*}$clk_pins

set rst_pins [list \
  [get_bd_pins axi_ic/ARESETN] \
  [get_bd_pins axi_ic/S00_ARESETN] \
  [get_bd_pins axi_ic/M00_ARESETN] \
  [get_bd_pins axi_ic/M01_ARESETN] \
  [get_bd_pins axi_ic/M02_ARESETN] \
  [get_bd_pins axi_ic/M03_ARESETN] \
  [get_bd_pins axi_ic/M04_ARESETN] \
  [get_bd_pins axi_ic/M05_ARESETN] \
  [get_bd_pins axi_dma/axi_resetn] \
  [get_bd_pins pc_mm2s/aresetn] \
  [get_bd_pins pc_s2mm/aresetn] \
  [get_bd_pins gpio_ctrl/s_axi_aresetn] \
  [get_bd_pins gpio_stat/s_axi_aresetn] \
  [get_bd_pins gpio_cap/s_axi_aresetn] \
  [get_bd_pins bram_ctrl_in/s_axi_aresetn] \
  [get_bd_pins bram_ctrl_out/s_axi_aresetn] \
  [get_bd_pins fir_stream_shell_0/rst_n] \
  [get_bd_pins mon_in/rst_n] \
  [get_bd_pins mon_out/rst_n] \
  [get_bd_pins cap_0/rst_n] ]
connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] {*}$rst_pins

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
assign_bd_address -offset 0x43C00000 -range 0x00001000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin bram_ctrl_in/S_AXI] -force
assign_bd_address -offset 0x43C08000 -range 0x00001000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin bram_ctrl_out/S_AXI] -force
assign_bd_address -offset 0x43C10000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin gpio_ctrl/S_AXI] -force
assign_bd_address -offset 0x43C20000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin gpio_stat/S_AXI] -force
assign_bd_address -offset 0x43C30000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin gpio_cap/S_AXI] -force

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

if {[info exists ::env(FIR_RTL_BD_ONLY)]} {
  puts "### BD_ONLY：validate 过了就停"
  exit
}

set_property synth_checkpoint_mode None [get_files fir_rtl_dma_cap.bd]

set wrapper [make_wrapper -files [get_files fir_rtl_dma_cap.bd] -top]
add_files -norecurse $wrapper

update_compile_order -fileset sources_1
set_property top fir_rtl_dma_cap_wrapper [current_fileset]
set_property STEPS.SYNTH_DESIGN.TCL.PRE $pre_tcl [get_runs synth_1]

launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
  error "综合没跑完，看 $build_dir/fir_rtl_dma_cap.runs/synth_1/runme.log"
}
open_run synth_1
report_utilization -file [file join $script_dir rtl_dma_cap_synth_util.rpt]

set s_dsp [llength [get_cells -hier -filter {REF_NAME =~ DSP48*}]]
set s_bram [llength [get_cells -hier -filter {REF_NAME =~ RAMB*}]]
puts "### post-synth DSP48 = $s_dsp   RAMB = $s_bram"
if {$s_dsp < 100} {
  error "post-synth DSP48 = $s_dsp only: FIR got constant-folded (coefficient .mem missing)"
}

launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
  error "实现没跑完，看 $build_dir/fir_rtl_dma_cap.runs/impl_1/runme.log"
}
open_run impl_1

set hwdef_zip [file join $script_dir fir_rtl_dma_cap.hwdef]
set hwh_path  [file join $script_dir fir_rtl_dma_cap.hwh]
write_hwdef -force -file $hwdef_zip
write_bitstream -force [file join $script_dir fir_rtl_dma_cap.bit]

set unzip_ok 0
foreach py {python python3 py} {
  if {$unzip_ok} break
  if {![catch {
    exec $py -c {import sys,zipfile
z = zipfile.ZipFile(sys.argv[1])
open(sys.argv[2], 'wb').write(z.read(sys.argv[3]))} \
        $hwdef_zip $hwh_path [file tail $hwh_path]
  }]} { set unzip_ok 1 }
}
if {!$unzip_ok} { puts "!!! 拆 hwh 失败" }

report_timing_summary -quiet -file [file join $script_dir fir_rtl_dma_cap_timing.rpt]
report_utilization   -quiet -file [file join $script_dir fir_rtl_dma_cap_util.rpt]

set wns  [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -delay_type max]]
set dsps [llength [get_cells -hier -filter {REF_NAME =~ DSP48*}]]
set lut  [llength [get_cells -hier -filter {REF_NAME =~ LUT}]]
set ff   [llength [get_cells -hier -filter {PRIMITIVE_GROUP == FLOP_LATCH}]]
puts "\n### RESULT"
puts "### WNS_ns      = $wns"
puts "### DSP48       = $dsps"
puts "### LUT         = $lut"
puts "### FF          = $ff"
puts "### bit         = [file join $script_dir fir_rtl_dma_cap.bit]"
puts "### hwh         = $hwh_path"
puts "### DONE"
exit
