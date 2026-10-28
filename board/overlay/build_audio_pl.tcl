# 音频通路「全 PL」版 overlay
#
# 和另外两块位流的关系：
#   fir.bit          出货版：PS 逐点读寄存器、PS 调核
#   fir_dma_core.bit DMA 版：PS 每块搬一次，核还是 v25
#   fir_pl.bit       这一块：麦克风 → I2S → 前端 → 压缩 → I2S → 耳机，
#                    **整条链在 PL 里，PS 只在一开始配一次 codec**
#
# 为什么要这一块：块模式的地板是 11 ms（一块的缓冲 + PS 每块 2.6 ms 两项
# 压不下去），要到助听器那种 10 ms 以下只能把 PS 拿出时序环。
#
# 结构：
#
#   ps7 ─ axi_ic ─┬─ gpio_ctrl    0x43C00000   （run / drc_en / flush）
#                 ├─ gpio_stat    0x43C10000   （i2s_duplex: ovf/und + FIFO 水位）
#                 └─ gpio_stat2   0x43C20000   （直连模块: 输出 FIFO 溢出 / 峰值水位）
#
#   i2s_duplex/M_AXIS ─┐
#                      ├─ fir_pl_direct ─┬─ i2s_duplex/S_AXIS
#                      ┘                 └─ 前端 fir_mb_fe → fir_drc4
#
# 用法：
#   vivado -mode batch -source board/overlay/build_audio_pl.tcl
#   FIR_PL_BD_ONLY=1 vivado ...    只验接线，十几秒
#   FIR_PL_TX_TARGET=64 vivado ... 播放 FIFO 水位目标（字），直接进延迟
#
# 产物：board/overlay/<out_name>.bit / .hwh

set script_dir [file normalize [file dirname [info script]]]
set repo_root  [file normalize [file join $script_dir .. ..]]
set build_dir  [file join $repo_root build vivado audio_pl]
set part_name  xc7z020clg400-1

# 播放 FIFO 水位目标（字）。一个字 = 1/(2·48.83k) = 10.2 µs，
# 所以 128 字 ≈ 1.31 ms，64 字 ≈ 0.65 ms。它是延迟里最大的一项，也是最该调的。
set tx_target [expr {[info exists ::env(FIR_PL_TX_TARGET)]
                     ? $::env(FIR_PL_TX_TARGET) : 128}]
set out_name [expr {$tx_target == 128 ? "fir_pl" : "fir_pl_tx$tx_target"}]
puts "### TX_TARGET = $tx_target   out_name = $out_name"

set ::env(FIR_RTL_REPO) $repo_root
set pre_tcl [file join $script_dir coeff_pre.tcl]
if {![file exists $pre_tcl]} { error "缺 $pre_tcl" }

file mkdir $build_dir
# 注意：**不能** cd 进 $build_dir 再 create_project -force 到同一个目录 —— 那等于
# 把当前目录删掉重建，Windows 下进程的 CWD 会变成已失效的目录，之后 Vivado 内部
# 任何相对路径 source 都会报
#     couldn't read file "init.tcl": No error
# 报错里那句 "No error" 就是这个症状（系统调用其实返回了 ERROR_SUCCESS）。
# 停在父目录最安全，所有路径本来也都是绝对路径。
cd [file dirname $build_dir]

create_project -force audio_pl $build_dir -part $part_name

add_files -norecurse [list \
  [file join $repo_root src rtl axis_fifo.v] \
  [file join $repo_root src rtl i2s_duplex.v] \
  [file join $repo_root src rtl fir_mb_fe.v] \
  [file join $repo_root src rtl fir_drc4.v] \
  [file join $repo_root src rtl fir_pl_direct.v] ]
update_compile_order -fileset sources_1

create_bd_design "audio_pl"

# ---- PS：配置逐字抄出货版 ----
set ps7_0 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7_0]
source [file join $script_dir ps7_config.tcl]
# ps7_config.tcl 是给 DMA 版用的，里面开了 S_AXI_HP0/HP2 给 DMA 走。
# 这一版没有 DMA、没有 DDR 访问，HP 口留着会 validate 报"时钟脚没接"。
set_property -dict [list \
  CONFIG.PCW_USE_S_AXI_HP0 {0} \
  CONFIG.PCW_USE_S_AXI_HP2 {0} \
  CONFIG.PCW_GPIO_EMIO_GPIO_ENABLE {0} \
  CONFIG.PCW_GPIO_EMIO_GPIO_IO {<Select>} \
] $ps7_0

set DDR      [create_bd_intf_port -mode Master -vlnv xilinx.com:interface:ddrx_rtl:1.0 DDR]
set FIXED_IO [create_bd_intf_port -mode Master -vlnv xilinx.com:display_processing_system7:fixedio_rtl:1.0 FIXED_IO]
connect_bd_intf_net -intf_net ps7_0_DDR      [get_bd_intf_ports DDR]      [get_bd_intf_pins ps7_0/DDR]
connect_bd_intf_net -intf_net ps7_0_FIXED_IO [get_bd_intf_ports FIXED_IO] [get_bd_intf_pins ps7_0/FIXED_IO]

# codec 的 I2C 走 PS 的 IIC_1（EMIO），和 DMA 版一样直接做成顶层端口
set IIC_1 [create_bd_intf_port -mode Master -vlnv xilinx.com:interface:iic_rtl:1.0 IIC_1]
connect_bd_intf_net -intf_net ps7_0_IIC_1 [get_bd_intf_ports IIC_1] [get_bd_intf_pins ps7_0/IIC_1]

# ---- 复位 ----
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_0_100M
connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_0_100M/ext_reset_in]

# ---- 10 MHz MCLK（codec 主时钟）----
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
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {4}] $axi_ic

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

set gpio_stat2 [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_stat2]
set_property -dict [list \
  CONFIG.C_GPIO_WIDTH {32} \
  CONFIG.C_ALL_INPUTS {1} \
  CONFIG.C_IS_DUAL {0} \
] $gpio_stat2

# 调试口：dbg0 = 喂进/吐出前端的字数，dbg1 = 状态快照
set gpio_dbg [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_dbg]
set_property -dict [list \
  CONFIG.C_GPIO_WIDTH {32} \
  CONFIG.C_GPIO2_WIDTH {32} \
  CONFIG.C_IS_DUAL {1} \
  CONFIG.C_ALL_INPUTS {1} \
  CONFIG.C_ALL_INPUTS_2 {1} \
] $gpio_dbg

# ---- 直连那一条链 ----
# i2s_duplex 是在 fir_pl_direct 内部例化的，BD 里只摆这一个 cell。
# 中间那对 AXIS（i2s 的 M_AXIS 进前端、前端出来进 i2s 的 S_AXIS）
# 都在模块内部，BD 上看不到，也不该看到 —— 那正是"PS 不在环里"的意思。
set pl [create_bd_cell -type module -reference fir_pl_direct fir_pl_direct_0]
set_property -dict [list CONFIG.TX_TARGET $tx_target] $pl
puts "==== fir_pl_direct_0 的引脚 ===="
foreach p [get_bd_pins -quiet fir_pl_direct_0/*] { puts "  $p" }

connect_bd_intf_net -intf_net ps7_0_M_AXI_GP0 [get_bd_intf_pins ps7_0/M_AXI_GP0] [get_bd_intf_pins axi_ic/S00_AXI]
connect_bd_intf_net -intf_net axi_ic_m00 [get_bd_intf_pins axi_ic/M00_AXI] [get_bd_intf_pins gpio_ctrl/S_AXI]
connect_bd_intf_net -intf_net axi_ic_m01 [get_bd_intf_pins axi_ic/M01_AXI] [get_bd_intf_pins gpio_stat/S_AXI]
connect_bd_intf_net -intf_net axi_ic_m02 [get_bd_intf_pins axi_ic/M02_AXI] [get_bd_intf_pins gpio_stat2/S_AXI]
connect_bd_intf_net -intf_net axi_ic_m03 [get_bd_intf_pins axi_ic/M03_AXI] [get_bd_intf_pins gpio_dbg/S_AXI]

connect_bd_net [get_bd_pins gpio_ctrl/gpio_io_o]  [get_bd_pins fir_pl_direct_0/ctrl]
connect_bd_net [get_bd_pins gpio_stat/gpio_io_i]  [get_bd_pins fir_pl_direct_0/stat0]
connect_bd_net [get_bd_pins gpio_stat/gpio2_io_i] [get_bd_pins fir_pl_direct_0/stat1]
connect_bd_net [get_bd_pins gpio_stat2/gpio_io_i] [get_bd_pins fir_pl_direct_0/stat2]
connect_bd_net [get_bd_pins gpio_dbg/gpio_io_i]   [get_bd_pins fir_pl_direct_0/dbg0]
connect_bd_net [get_bd_pins gpio_dbg/gpio2_io_i]  [get_bd_pins fir_pl_direct_0/dbg1]

# ---- 时钟 / 复位 ----
set clk_pins [list \
               [get_bd_pins ps7_0/FCLK_CLK0] \
               [get_bd_pins ps7_0/M_AXI_GP0_ACLK] \
               [get_bd_pins ps7_0/S_AXI_GP0_ACLK] \
               [get_bd_pins ps7_0/M_AXI_GP1_ACLK] \
               [get_bd_pins rst_ps7_0_100M/slowest_sync_clk] \
               [get_bd_pins clk_wiz_10MHz/clk_in1] \
               [get_bd_pins axi_ic/ACLK] \
               [get_bd_pins axi_ic/S00_ACLK] \
               [get_bd_pins axi_ic/M00_ACLK] \
               [get_bd_pins axi_ic/M01_ACLK] \
               [get_bd_pins axi_ic/M02_ACLK] \
               [get_bd_pins axi_ic/M03_ACLK] \
               [get_bd_pins gpio_ctrl/s_axi_aclk] \
               [get_bd_pins gpio_stat/s_axi_aclk] \
               [get_bd_pins gpio_stat2/s_axi_aclk] \
               [get_bd_pins gpio_dbg/s_axi_aclk] \
               [get_bd_pins fir_pl_direct_0/clk] ]

set rst_pins [list \
               [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] \
               [get_bd_pins clk_wiz_10MHz/resetn] \
               [get_bd_pins axi_ic/ARESETN] \
               [get_bd_pins axi_ic/S00_ARESETN] \
               [get_bd_pins axi_ic/M00_ARESETN] \
               [get_bd_pins axi_ic/M01_ARESETN] \
               [get_bd_pins axi_ic/M02_ARESETN] \
               [get_bd_pins axi_ic/M03_ARESETN] \
               [get_bd_pins gpio_ctrl/s_axi_aresetn] \
               [get_bd_pins gpio_stat/s_axi_aresetn] \
               [get_bd_pins gpio_stat2/s_axi_aresetn] \
               [get_bd_pins gpio_dbg/s_axi_aresetn] \
               [get_bd_pins fir_pl_direct_0/rst_n] ]

connect_bd_net {*}$clk_pins
connect_bd_net {*}$rst_pins

# ---- 顶层音频端口 ----
set audio_clk_10MHz [create_bd_port -dir O -type clk audio_clk_10MHz]
connect_bd_net [get_bd_pins clk_wiz_10MHz/clk_out1] [get_bd_ports audio_clk_10MHz]

create_bd_port -dir O bclk
create_bd_port -dir O lrclk
create_bd_port -dir O sdata_o
create_bd_port -dir I sdata_i
connect_bd_net [get_bd_pins fir_pl_direct_0/bclk]    [get_bd_ports bclk]
connect_bd_net [get_bd_pins fir_pl_direct_0/lrclk]   [get_bd_ports lrclk]
connect_bd_net [get_bd_pins fir_pl_direct_0/sdata_o] [get_bd_ports sdata_o]
connect_bd_net [get_bd_pins fir_pl_direct_0/sdata_i] [get_bd_ports sdata_i]

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

assign_bd_address -offset 0x43C00000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin gpio_ctrl/S_AXI] -force
assign_bd_address -offset 0x43C10000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin gpio_stat/S_AXI] -force
assign_bd_address -offset 0x43C20000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin gpio_stat2/S_AXI] -force
assign_bd_address -offset 0x43C30000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin gpio_dbg/S_AXI] -force

puts "==== ADDR MAP ===="
foreach s [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps7_0/Data]] {
  puts "  [get_property NAME $s]  offset=[get_property offset $s]  range=[get_property range $s]"
}

regenerate_bd_layout
validate_bd_design
save_bd_design

if {[info exists ::env(FIR_PL_BD_ONLY)]} {
  puts "### BD_ONLY：validate 过了就停"
  exit
}

# 系数表：BD 默认把 module reference 拆 OOC，那个 run 的 cwd 不是 synth_1，
# $readmemh 找不到表 → ROM 全 X 且照样 BUILD SUCCEEDED。
set_property synth_checkpoint_mode None [get_files audio_pl.bd]

set wrapper [make_wrapper -files [get_files audio_pl.bd] -top]
add_files -norecurse $wrapper
add_files -fileset constrs_1 -norecurse [file join $script_dir audio_pins.xdc]

update_compile_order -fileset sources_1
set_property top audio_pl_wrapper [current_fileset]
set_property STEPS.SYNTH_DESIGN.TCL.PRE $pre_tcl [get_runs synth_1]

launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
  error "综合没跑完，看 $build_dir/audio_pl.runs/synth_1/runme.log"
}
open_run synth_1
report_utilization -file [file join $script_dir ${out_name}_synth_util.rpt]

# 系数表没读到的时候 FIR 会被综合成常量，DSP 数会掉到个位数 —— 硬断言
set s_dsp [llength [get_cells -hier -filter {REF_NAME =~ DSP48*}]]
set s_bram [llength [get_cells -hier -filter {REF_NAME =~ RAMB*}]]
puts "### post-synth DSP48 = $s_dsp   RAMB = $s_bram"
if {$s_dsp < 100} {
  error "post-synth DSP48 = $s_dsp only: FIR got constant-folded (coefficient .mem missing)"
}

launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
  error "实现没跑完，看 $build_dir/audio_pl.runs/impl_1/runme.log"
}
open_run impl_1

set hwdef_zip [file join $script_dir $out_name.hwdef]
set hwh_path  [file join $script_dir $out_name.hwh]
write_hwdef -force -file $hwdef_zip
write_bitstream -force [file join $script_dir $out_name.bit]

# 从 .hwdef（是个 zip）里抠出 .hwh。
# 注意：判据只能看"文件写出来了没有"，不能看 exit code —— Vivado 的 exec 环境里
# python 常常返回非 0 却已经把文件写好（2026-10-28 因此误报过一轮失败）。
set unzip_ok 0
foreach py {C:/Users/XIA/miniconda3/python.exe python3 python py} {
  if {$unzip_ok} break
  catch {
    exec $py -c {
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
n = [x for x in z.namelist() if x.endswith('.hwh')][0]
open(sys.argv[2], 'wb').write(z.read(n))
} $hwdef_zip $hwh_path
  }
  if {[file exists $hwh_path] && [file size $hwh_path] > 0} { set unzip_ok 1 }
}
if {!$unzip_ok} {
  error "拆 hwh 失败（$hwh_path 不存在或为空），hwdef 在 $hwdef_zip"
}

report_timing_summary -quiet -file [file join $script_dir ${out_name}_timing.rpt]
report_utilization   -quiet -file [file join $script_dir ${out_name}_util.rpt]

set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -delay_type max]]
puts "\n### RESULT"
puts "### TX_TARGET = $tx_target"
puts "### WNS_ns = $wns"
puts "### bit    = [file join $script_dir $out_name.bit]"
puts "### hwh    = $hwh_path"
puts "### DONE"
exit
