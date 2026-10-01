# 自建 overlay —— 第 3 步：把核插进音频通路
#
# 前两步证的东西：
#   ①（build_ps_only.tcl）我们建的工程能出 bit、PYNQ 能加载 —— PL 里是空的
#   ②（build_audio.tcl）我们自己造的 bit 能出声 —— 走的是官方那个音频 IP
# 这一步往里加**我们自己的 FIR 核**，两条线在这里汇合。
#
# 数据怎么走：
#   Python 把一块 PCM 写进 DDR 缓冲区
#     → 把缓冲区的地址写进核的寄存器（0x43C10000）
#     → 启动核
#     → 核自己经 PS 的 S_AXI_HP0 去 DDR 读、算完自己写回 DDR
#     → Python 把结果读出来
#
# 核的两个 AXI4-Master（m_axi_in_r 读、m_axi_out_r 写）都要接进 HP0，
# 所以 HP0 那边需要一个 2 进 1 出的互联（axi_ic_hp）。
#
# 前置条件：
#   ① 音频 IP 源码：python board/overlay/fetch_audio_ip.py
#   ② 核打包成 IP：build/hls/run_export_ip.tcl 产出
#      build/vivado/ip_repo/fir_multiband_v1_0/component.xml
#
# 用法（在任意目录）：
#   E:\Xilinx\Vivado\2020.2\bin\vivado.bat -mode batch -source build_fir.tcl
#
# 产物：
#   board/overlay/fir.bit / fir.hwh   给 PYNQ 加载的
#   工程本体在 build/vivado/fir/（已被 .gitignore 忽略）

set script_dir [file normalize [file dirname [info script]]]
set repo_root  [file normalize [file join $script_dir .. ..]]
set build_dir  [file join $repo_root build vivado fir]
set ip_repo_audio [file join $repo_root build vivado ip_repo audio_codec_ctrl_v1.0]
set ip_repo_fir   [file join $repo_root build vivado ip_repo fir_multiband_v1_0]
set part_name  xc7z020clg400-1

if {![file exists [file join $ip_repo_audio component.xml]]} {
  error "音频 IP 仓库不在：$ip_repo_audio\n先跑：python board/overlay/fetch_audio_ip.py"
}
if {![file exists [file join $ip_repo_fir component.xml]]} {
  error "核的 IP 仓库不在：$ip_repo_fir\n先跑 build/hls/run_export_ip.tcl 那一轮"
}

file mkdir $build_dir
cd $build_dir

create_project -force fir $build_dir -part $part_name

# 两个 IP 仓库一起挂进来，否则下面按 vlnv 建 cell 会找不到
set_property ip_repo_paths [list $ip_repo_audio $ip_repo_fir] [current_project]
update_ip_catalog -rebuild

create_bd_design "fir"

# ---- 1. PS，配置从官方 base.tcl 抄（ps7_config.tcl）----
set ps7_0 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7_0]
source [file join $script_dir ps7_config.tcl]

# 关掉 GPIO（RPi/Arduino 排针，要在顶层多接 20 个脚）。
# I2C1 留着 —— codec 的配置走它。
set_property -dict [list \
  CONFIG.PCW_GPIO_EMIO_GPIO_ENABLE {0} \
  CONFIG.PCW_GPIO_EMIO_GPIO_IO {<Select>} \
] $ps7_0

# ---- 2. DDR 和 FIXED_IO 引到顶层 ----
set DDR      [create_bd_intf_port -mode Master -vlnv xilinx.com:interface:ddrx_rtl:1.0 DDR]
set FIXED_IO [create_bd_intf_port -mode Master -vlnv xilinx.com:display_processing_system7:fixedio_rtl:1.0 FIXED_IO]
connect_bd_intf_net -intf_net ps7_0_DDR      [get_bd_intf_ports DDR]      [get_bd_intf_pins ps7_0/DDR]
connect_bd_intf_net -intf_net ps7_0_FIXED_IO [get_bd_intf_ports FIXED_IO] [get_bd_intf_pins ps7_0/FIXED_IO]

# ---- 3. 时钟和复位骨架 ----
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_0_100M
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] \
               [get_bd_pins ps7_0/M_AXI_GP0_ACLK] \
               [get_bd_pins ps7_0/S_AXI_GP0_ACLK] \
               [get_bd_pins rst_ps7_0_100M/slowest_sync_clk]
connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_0_100M/ext_reset_in]

# PS 使能的 AXI 端口不止 GP0（配置里 M_AXI_GP1 / S_AXI_HP0 / S_AXI_HP2 也都开着），
# 每个的时钟脚都必须接到某个时钟上，否则 validate 报 [BD 41-758]。第 1 步栽过。
#
# ⚠️ 和第 2 步不一样的地方：这里 **HP0 的时钟改用 FCLK_CLK0**。
#    官方 base.tcl 是把 HP0 接到 FCLK_CLK1，而这份配置里 CLK1 = 1000/7 ≈ 142.9 MHz ——
#    核是在 100 MHz 下量的时序（余量 +0.932 ns），跑到 142.9 MHz 必崩。
#    全设计统一在 CLK0（100 MHz）上，互联里就不用放时钟转换器了。
#    （HP0 对 PS 是异步口，时钟给多少都行。）
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] [get_bd_pins ps7_0/S_AXI_HP0_ACLK]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK3] \
               [get_bd_pins ps7_0/M_AXI_GP1_ACLK] \
               [get_bd_pins ps7_0/S_AXI_HP2_ACLK]

# ---- 4. 音频 IP 本体（和第 2 步一样）----
set audio [create_bd_cell -type ip -vlnv xilinx.com:user:audio_codec_ctrl:1.0 audio_codec_ctrl_0]

# ---- 5. 10 MHz MCLK ----
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

# ---- 6. 控制通路：PS GP0 -> 音频 IP 的 S_AXI / 核的 s_axi_control ----
# 两个从口，所以互联是 1 进 2 出（第 2 步是 1 进 1 出）。
set axi_ic [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_ic]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] $axi_ic

set fir [create_bd_cell -type ip -vlnv xilinx.com:hls:fir_multiband:1.0 fir_multiband_0]

connect_bd_intf_net -intf_net ps7_0_M_AXI_GP0 [get_bd_intf_pins ps7_0/M_AXI_GP0] [get_bd_intf_pins axi_ic/S00_AXI]
connect_bd_intf_net -intf_net axi_ic_M00_AXI   [get_bd_intf_pins axi_ic/M00_AXI]  [get_bd_intf_pins audio_codec_ctrl_0/S_AXI]
connect_bd_intf_net -intf_net axi_ic_M01_AXI   [get_bd_intf_pins axi_ic/M01_AXI]  [get_bd_intf_pins fir_multiband_0/s_axi_control]

# ---- 7. 数据通路：核的 AXI4-Master -> PS 的 S_AXI_HP0 ----
# 核是「自己去内存搬」的那种（m_axi master），不是 DMA 推给它。
#
# ⚠️ 一个容易想错的地方：源码里给 in / out 各写了一条 m_axi 指令，
#    但 HLS 会把**同一个 bundle** 里的 m_axi 口合并成**一个** AXI4-Master。
#    打包出来的 component.xml 里只有一个总线接口 `m_axi_gmem`（实测确认），
#    顶层 Verilog 里倒是还看得见 m_axi_in_r_* / m_axi_out_r_* 两组线 ——
#    以 component.xml 为准，block design 里认的是 m_axi_gmem。
#
# 还是要一个互联：核这边是 32 位 AXI，PS 的 HP0 是 64 位，宽度转换由它做。
set axi_ic_hp [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_ic_hp]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $axi_ic_hp

connect_bd_intf_net -intf_net fir_gmem [get_bd_intf_pins fir_multiband_0/m_axi_gmem] [get_bd_intf_pins axi_ic_hp/S00_AXI]
connect_bd_intf_net -intf_net hp0_ddr  [get_bd_intf_pins axi_ic_hp/M00_AXI] [get_bd_intf_pins ps7_0/S_AXI_HP0]

# ---- 8. 时钟与复位接到新加的块上 ----
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] \
               [get_bd_pins rst_ps7_0_100M/slowest_sync_clk] \
               [get_bd_pins clk_wiz_10MHz/clk_in1] \
               [get_bd_pins axi_ic/ACLK] \
               [get_bd_pins axi_ic/S00_ACLK] \
               [get_bd_pins axi_ic/M00_ACLK] \
               [get_bd_pins axi_ic/M01_ACLK] \
               [get_bd_pins axi_ic_hp/ACLK] \
               [get_bd_pins axi_ic_hp/S00_ACLK] \
               [get_bd_pins axi_ic_hp/M00_ACLK] \
               [get_bd_pins audio_codec_ctrl_0/s_axi_aclk] \
               [get_bd_pins fir_multiband_0/ap_clk]

connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] \
               [get_bd_pins clk_wiz_10MHz/resetn] \
               [get_bd_pins axi_ic/ARESETN] \
               [get_bd_pins axi_ic/S00_ARESETN] \
               [get_bd_pins axi_ic/M00_ARESETN] \
               [get_bd_pins axi_ic/M01_ARESETN] \
               [get_bd_pins axi_ic_hp/ARESETN] \
               [get_bd_pins axi_ic_hp/S00_ARESETN] \
               [get_bd_pins axi_ic_hp/M00_ARESETN] \
               [get_bd_pins audio_codec_ctrl_0/s_axi_aresetn] \
               [get_bd_pins fir_multiband_0/ap_rst_n]

# ---- 9. 顶层端口（引脚约束和第 2 步完全一样）----
set audio_clk_10MHz [create_bd_port -dir O -type clk audio_clk_10MHz]
connect_bd_net [get_bd_pins clk_wiz_10MHz/clk_out1] [get_bd_ports audio_clk_10MHz]

create_bd_port -dir O bclk
create_bd_port -dir O lrclk
create_bd_port -dir O sdata_o
create_bd_port -dir I sdata_i
create_bd_port -dir O -from 1 -to 0 codec_addr

connect_bd_net [get_bd_pins audio_codec_ctrl_0/bclk]    [get_bd_ports bclk]
connect_bd_net [get_bd_pins audio_codec_ctrl_0/lrclk]   [get_bd_ports lrclk]
connect_bd_net [get_bd_pins audio_codec_ctrl_0/sdata_o] [get_bd_ports sdata_o]
connect_bd_net [get_bd_pins audio_codec_ctrl_0/sdata_i] [get_bd_ports sdata_i]
connect_bd_net [get_bd_pins audio_codec_ctrl_0/codec_address] [get_bd_ports codec_addr]

set IIC_1 [create_bd_intf_port -mode Master -vlnv xilinx.com:interface:iic_rtl:1.0 IIC_1]
connect_bd_intf_net -intf_net ps7_0_IIC_1 [get_bd_intf_ports IIC_1] [get_bd_intf_pins ps7_0/IIC_1]

# ---- 10. 基地址 ----
# 先把核身上所有的地址段打出来，名字对不上时一眼就能看见（比猜快）
puts "==== 核身上的地址段 ===="
foreach sp [get_bd_addr_spaces *] {
  if {[string match "*fir_multiband_0*" $sp]} {
    puts "  addr space: $sp"
    foreach seg [get_bd_addr_segs -of_objects $sp] {
      puts "      seg: $seg"
    }
  }
}
puts "==== 核的从口地址段 ===="
foreach seg [get_bd_addr_segs *] {
  if {[string match "*fir_multiband_0*" $seg]} {
    puts "  $seg"
  }
}

# 音频 IP：0x43C00000 —— 和官方 base overlay 一样，pynq.lib.audio 里写死的偏移照用。
# 核：      0x43C10000 —— 紧接着放，不撞。
assign_bd_address -offset 0x43C00000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] \
  [get_bd_addr_segs audio_codec_ctrl_0/S_AXI/reg0] -force

# 核的控制口段名在不同版本里叫法不同，抓不到就报出来
set fir_seg [get_bd_addr_segs -quiet fir_multiband_0/s_axi_control/Reg]
if {[llength $fir_seg] == 0} {
  set fir_seg [get_bd_addr_segs -quiet -filter {NAME =~ "*fir_multiband_0*"}]
}
if {[llength $fir_seg] == 0} {
  error "找不到核控制口的地址段，看上面那段打印"
}
assign_bd_address -offset 0x43C10000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] \
  $fir_seg -force

# 核的两个 master 得能摸到 DDR：把 HP0 的 DDR 段分给它们。
# 先自动分配一遍，再把结果打出来核对（没分到就得手工指定段名）。
assign_bd_address
puts "==== 核的两个 master 摸得到哪些内存 ===="
foreach sp [get_bd_addr_spaces *] {
  if {[string match "*fir_multiband_0/Data*" $sp]} {
    foreach seg [get_bd_addr_segs -of_objects $sp] {
      set off [get_property offset $seg]
      set rng [get_property range $seg]
      puts "  $sp -> [get_property NAME $seg]  offset=$off range=$rng"
    }
  }
}

regenerate_bd_layout
validate_bd_design
save_bd_design

# ---- 11. 顶层 wrapper + 引脚约束 ----
set wrapper [make_wrapper -files [get_files fir.bd] -top]
add_files -norecurse $wrapper
add_files -fileset constrs_1 -norecurse [file join $script_dir audio_pins.xdc]
set_property top fir_wrapper [current_fileset]
update_compile_order -fileset sources_1

# ---- 12. 综合 + 实现 + 出 bit ----
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
  error "综合没跑完，看 build/vivado/fir/fir.runs/synth_1/runme.log"
}

launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
  error "实现没跑完，看 build/vivado/fir/fir.runs/impl_1/runme.log"
}

open_run impl_1

# ---- 13. 出 PYNQ 要的两个文件 ----
# 坑（第 1 步记下的）：write_hwdef 出的不是 .hwh，是一个 zip 包，
# 里面那个根标签是 <EDKSYSTEM> 的文件才是 PYNQ 要的。
set hwdef_zip [file join $script_dir fir.hwdef]
set hwh_path  [file join $script_dir fir.hwh]
write_hwdef -force -file $hwdef_zip
write_bitstream -force [file join $script_dir fir.bit]

set unzip_ok 0
foreach py {python python3 py} {
  if {$unzip_ok} break
  if {![catch {
    exec $py -c {import sys,zipfile
z = zipfile.ZipFile(sys.argv[1])
open(sys.argv[2], 'wb').write(z.read(sys.argv[3]))} \
        $hwdef_zip $hwh_path [file tail $hwh_path]
  }]} {
    set unzip_ok 1
  }
}
if {!$unzip_ok} {
  error "拆 hwh 失败，手工执行这一句即可：\n\
    python -c \"import zipfile,sys; zipfile.ZipFile('$hwdef_zip').extract('fir.hwh','$script_dir')\""
}

# ---- 14. 整机时序：这次是**在 context 里**量的，不是光杆司令 ----
# 前面 HLS 那边量的 +0.932 ns 是 -mode out_of_context 的数（当独立零件量）。
# 这张报告才回答「整机能不能跑 100 MHz」—— 项目里一直欠着的那一条证据。
report_timing_summary -quiet -file [file join $script_dir fir_timing.rpt]
report_utilization  -quiet -file [file join $script_dir fir_util.rpt]

puts "\n=== 第 3 步完成 ==="
puts "  [file join $script_dir fir.bit]   给 PYNQ 加载的"
puts "  [file join $script_dir fir.hwh]   给 PYNQ 认 IP 的"
puts "  整机时序报告：fir_timing.rpt（看 WNS 是正是负）"
exit
