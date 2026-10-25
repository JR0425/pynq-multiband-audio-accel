# 自研 RTL —— DMA 版（PLAN §11.14 §4 的首选路）
#
# 和 build_rtl.tcl（块模式、AXI-Lite + 双口 BRAM）是**两条独立的路**：
# 同一份 fir_mb_fe + fir_drc4，同一套数值，只换数据怎么进出。
#
# 结构：
#
#   PS7 M_AXI_GP0 ─ axi_ic ─┬─ axi_dma/S_AXI_LITE  0x40400000
#                           ├─ gpio_ctrl            0x43C10000
#                           └─ gpio_stat            0x43C20000
#   axi_dma/M_AXI_MM2S ── S_AXI_HP0（读 DDR）
#   axi_dma/M_AXI_S2MM ── S_AXI_HP1（写 DDR）
#   axi_dma/M_AXIS_MM2S ── fir_stream_shell/S_AXIS
#   fir_stream_shell/M_AXIS ── axi_dma/S_AXIS_S2MM
#
# 为什么两个 HP 口分开：MM2S 读和 S2MM 写走同一口会互相抢带宽，
# 而且 PL 侧那条 AXIS 是单工的、天然一进一出，分开后两边各自流水。
# 这份 PS 配置是在出货 base 上**只多开一个 HP1**，其余和出货逐字相同。
#
# 用法：
#   vivado -mode batch -source board/overlay/build_rtl_dma.tcl
#
# 产物：
#   board/overlay/fir_rtl_dma.bit / fir_rtl_dma.hwh
#   board/overlay/rtl_dma_synth_util.rpt / fir_rtl_dma_timing.rpt / fir_rtl_dma_util.rpt

set script_dir [file normalize [file dirname [info script]]]
set repo_root  [file normalize [file join $script_dir .. ..]]
set build_dir  [file join $repo_root build vivado fir_rtl_dma]
set part_name  xc7z020clg400-1

file mkdir $build_dir

set ::env(FIR_RTL_REPO) $repo_root
set pre_tcl [file join $script_dir coeff_pre.tcl]
if {![file exists $pre_tcl]} { error "缺 $pre_tcl" }

cd $build_dir
create_project -force fir_rtl_dma $build_dir -part $part_name

add_files -norecurse [list \
  [file join $repo_root src rtl fir_mb_fe.v] \
  [file join $repo_root src rtl fir_drc4.v] \
  [file join $repo_root src rtl fir_stream_shell.v] ]
update_compile_order -fileset sources_1

create_bd_design "fir_rtl_dma"

# ---- PS ----
set ps7_0 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7_0]
source [file join $script_dir ps7_config.tcl]
set_property -dict [list \
  CONFIG.PCW_GPIO_EMIO_GPIO_ENABLE {0} \
  CONFIG.PCW_GPIO_EMIO_GPIO_IO {<Select>} \
] $ps7_0
# 出货配置里 HP1 是关的，这里单独开（只改这一项，别的一律照抄）。
# 不设 DATA_WIDTH 的话 IP 默认 64 位，和 HP0 一致。
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

# ---- 寄存器总线：DMA 的 lite 口 + 两个 gpio ----
set axi_ic [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_ic]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {3}] $axi_ic

# ---- axi_dma ----
# 简单模式（不开 SG）：描述符链在板上从没跑过，先用最保守的寄存器模式，
# 一次传输 = 写一次长度 + 一次地址。PYNQ 的 DMA 驱动两种模式都支持。
#
# c_sg_length_width 必须显式给 26。它的名字像只管 SG 的描述符字段，其实是
# **长度寄存器的位宽，简单模式也一样**（PG021 表 2-15/2-25；PYNQ 的 dma.py
# 就是照这个算 buffer_max_size 的）。默认 14 → 单次最多 16383 字节，
# 4800 个 32 位采样是 19200 字节，超出的部分 IP 不报错，直接把长度截断到
# 低 14 位（19200 & 0x3FFF = 2816），板上表现为"只传了 704 个采样"。
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

# ---- 自研外壳 ----
set shell [create_bd_cell -type module -reference fir_stream_shell fir_stream_shell_0]

# 流端口带 X_INTERFACE_INFO，应该被合成接口；万一没合成，这里直接报出来，
# 别等到 validate 才在一堆 DRC 里找。
puts "==== fir_stream_shell_0 的接口 ===="
foreach p [get_bd_intf_pins -quiet fir_stream_shell_0/*] { puts "  $p" }
puts "==== fir_stream_shell_0 的引脚 ===="
foreach p [get_bd_pins -quiet fir_stream_shell_0/*] { puts "  $p" }

connect_bd_intf_net -intf_net ps7_0_M_AXI_GP0 [get_bd_intf_pins ps7_0/M_AXI_GP0] [get_bd_intf_pins axi_ic/S00_AXI]
connect_bd_intf_net -intf_net axi_ic_m00 [get_bd_intf_pins axi_ic/M00_AXI] [get_bd_intf_pins axi_dma/S_AXI_LITE]
connect_bd_intf_net -intf_net axi_ic_m01 [get_bd_intf_pins axi_ic/M01_AXI] [get_bd_intf_pins gpio_ctrl/S_AXI]
connect_bd_intf_net -intf_net axi_ic_m02 [get_bd_intf_pins axi_ic/M02_AXI] [get_bd_intf_pins gpio_stat/S_AXI]

# 数据：DMA 两个 AXI4 主口 → PS 两个 HP 口
#
# PS7 的 S_AXI_HP 是 **AXI3**，axi_dma 的主口是 AXI4 —— 直接连会报
# [BD 41-1285] protocols incompatible。中间垫一个 axi_protocol_converter
# （SI=AXI4、MI=AXI3），宽度跟随两端，都是 64 位。
foreach nm {mm2s s2mm} {
  set pc [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_protocol_converter:2.1 pc_$nm]
  set_property -dict [list \
    CONFIG.SI_PROTOCOL {AXI4} \
    CONFIG.MI_PROTOCOL {AXI3} \
  ] $pc
}
if {[llength [get_bd_intf_pins -quiet axi_dma/M_AXI_MM2S]] == 0} { error "axi_dma 没有 M_AXI_MM2S" }
connect_bd_intf_net -intf_net dma_mm2s [get_bd_intf_pins axi_dma/M_AXI_MM2S] [get_bd_intf_pins pc_mm2s/S_AXI]
connect_bd_intf_net -intf_net pc_mm2s_hp0 [get_bd_intf_pins pc_mm2s/M_AXI] [get_bd_intf_pins ps7_0/S_AXI_HP0]
connect_bd_intf_net -intf_net dma_s2mm [get_bd_intf_pins axi_dma/M_AXI_S2MM] [get_bd_intf_pins pc_s2mm/S_AXI]
connect_bd_intf_net -intf_net pc_s2mm_hp1 [get_bd_intf_pins pc_s2mm/M_AXI] [get_bd_intf_pins ps7_0/S_AXI_HP1]

# 数据：DMA ↔ 外壳（AXI-Stream，接口对接口）
connect_bd_intf_net -intf_net axis_in  [get_bd_intf_pins axi_dma/M_AXIS_MM2S]  [get_bd_intf_pins fir_stream_shell_0/S_AXIS]
connect_bd_intf_net -intf_net axis_out [get_bd_intf_pins fir_stream_shell_0/M_AXIS] [get_bd_intf_pins axi_dma/S_AXIS_S2MM]

# 寄存器
connect_bd_net [get_bd_pins gpio_ctrl/gpio_io_o] [get_bd_pins fir_stream_shell_0/ctrl]
connect_bd_net [get_bd_pins gpio_stat/gpio_io_i]  [get_bd_pins fir_stream_shell_0/stat_lo]
connect_bd_net [get_bd_pins gpio_stat/gpio2_io_i] [get_bd_pins fir_stream_shell_0/stat_hi]

# ---- 时钟 ----
# 同一个源脚只能出现一次（BD 41-249），所以一起连完。
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] \
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
               [get_bd_pins axi_dma/s_axi_lite_aclk] \
               [get_bd_pins axi_dma/m_axi_mm2s_aclk] \
               [get_bd_pins axi_dma/m_axi_s2mm_aclk] \
               [get_bd_pins pc_mm2s/aclk] \
               [get_bd_pins pc_s2mm/aclk] \
               [get_bd_pins gpio_ctrl/s_axi_aclk] \
               [get_bd_pins gpio_stat/s_axi_aclk] \
               [get_bd_pins fir_stream_shell_0/clk]

connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] \
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
               [get_bd_pins fir_stream_shell_0/rst_n]

# ---- 地址 ----
# 按**接口引脚**取地址段，不要按名字 glob —— axi_dma 的从口叫 S_AXI_LITE，
# 用 axi_dma/S_AXI/* 匹配不到（块模式那边自己定义的名字碰巧叫 S_AXI）。
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
assign_bd_address -offset 0x43C10000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin gpio_ctrl/S_AXI] -force
assign_bd_address -offset 0x43C20000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin gpio_stat/S_AXI] -force

# DMA 是**主端**，它自己也有地址空间（Data_MM2S / Data_S2MM），要把 DDR 映射进去。
# 不映射只在 validate 里报 CRITICAL WARNING，能过，但板上 DMA 一条都读不到 ——
# 和块模式那次 SINGLE_PORT_BRAM 一样，属于"不报错、只是全错"的那类。
# PYNQ-Z2 是 512 MB DDR，所以 0x00000000 + 0x20000000。
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

# 只验 BD 的时候（改接线阶段）用 FIR_RTL_BD_ONLY=1 跑，省掉十几分钟综合。
if {[info exists ::env(FIR_RTL_BD_ONLY)]} {
  puts "### BD_ONLY：validate 过了就停"
  exit
}

# 系数表：BD 默认把 module reference 拆 OOC，那个 run 的 cwd 不是 synth_1，
# $readmemh 找不到表，ROM 变全 X 且照样 BUILD SUCCEEDED（块模式那边踩过）。
set_property synth_checkpoint_mode None [get_files fir_rtl_dma.bd]

set wrapper [make_wrapper -files [get_files fir_rtl_dma.bd] -top]
add_files -norecurse $wrapper

update_compile_order -fileset sources_1
set_property top fir_rtl_dma_wrapper [current_fileset]
set_property STEPS.SYNTH_DESIGN.TCL.PRE $pre_tcl [get_runs synth_1]

launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
  error "综合没跑完，看 $build_dir/fir_rtl_dma.runs/synth_1/runme.log"
}
open_run synth_1
report_utilization -file [file join $script_dir rtl_dma_synth_util.rpt]

set s_dsp [llength [get_cells -hier -filter {REF_NAME =~ DSP48*}]]
set s_bram [llength [get_cells -hier -filter {REF_NAME =~ RAMB*}]]
puts "### post-synth DSP48 = $s_dsp   RAMB = $s_bram"
if {$s_dsp < 100} {
  error "post-synth DSP48 = $s_dsp only: FIR got constant-folded (coefficient .mem missing)"
}

launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
  error "实现没跑完，看 $build_dir/fir_rtl_dma.runs/impl_1/runme.log"
}
open_run impl_1

set hwdef_zip [file join $script_dir fir_rtl_dma.hwdef]
set hwh_path  [file join $script_dir fir_rtl_dma.hwh]
write_hwdef -force -file $hwdef_zip
write_bitstream -force [file join $script_dir fir_rtl_dma.bit]

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
if {!$unzip_ok} { puts "!!! 拆 hwh 失败，手工执行：python -c \"import zipfile;zipfile.ZipFile('fir_rtl_dma.hwdef').extract('fir_rtl_dma.hwh','.')\"" }

report_timing_summary -quiet -file [file join $script_dir fir_rtl_dma_timing.rpt]
report_utilization   -quiet -file [file join $script_dir fir_rtl_dma_util.rpt]

set wns  [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -delay_type max]]
set dsps [llength [get_cells -hier -filter {REF_NAME =~ DSP48*}]]
set lut  [llength [get_cells -hier -filter {PRIMITIVE_GROUP == LUT}]]
set ff   [llength [get_cells -hier -filter {PRIMITIVE_GROUP == FLOP_LATCH}]]
puts "\n### RESULT"
puts "### WNS_ns      = $wns"
puts "### DSP48       = $dsps"
puts "### LUT         = $lut"
puts "### FF          = $ff"
puts "### bit         = [file join $script_dir fir_rtl_dma.bit]"
puts "### hwh         = $hwh_path"
puts "### DONE"
exit
