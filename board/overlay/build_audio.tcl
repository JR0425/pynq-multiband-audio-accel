# 自建 overlay —— 第 2 步：加音频通路，让我们自己造的 bit 也能放出声
#
# 第 1 步（build_ps_only.tcl）证明的是"我们建的工程能出 bit、PYNQ 能加载它"，
# 但那个 bit 的 PL 里是空的。这一步往里放音频 IP，目标是
# **上板之后 base.audio 那套代码照用，能出声**。
#
# 走的是 PLAN §2.2 说的"甲"路线：复用 PYNQ 官方那个自研音频 IP
# （xilinx.com:user:audio_codec_ctrl:1.0），于是 pynq.lib.audio.AudioADAU1761
# 和已经跑通的 verify_audio_playback.py 一行都不用改。
# 代价是代码和引脚全部照抄官方 —— 但这正是"省事"的含义，不是缺陷。
#
# 这个 IP 不在 Vivado 自带库里，要先拉源码：
#   python board/overlay/fetch_audio_ip.py
#
# 用法（在任意目录）：
#   E:\Xilinx\Vivado\2020.2\bin\vivado.bat -mode batch -source build_audio.tcl
#
# 产物：
#   board/overlay/audio.bit   给 PYNQ 加载的
#   board/overlay/audio.hwh   给 PYNQ 认 IP 的（必须和 .bit 同名同目录）
#   工程本体在 build/vivado/audio/（已被 .gitignore 忽略）

set script_dir [file normalize [file dirname [info script]]]
set repo_root  [file normalize [file join $script_dir .. ..]]
set build_dir  [file join $repo_root build vivado audio]
set ip_repo    [file join $repo_root build vivado ip_repo audio_codec_ctrl_v1.0]
set part_name  xc7z020clg400-1

if {![file exists [file join $ip_repo component.xml]]} {
  error "IP 仓库不在：$ip_repo\n先跑：python board/overlay/fetch_audio_ip.py"
}

file mkdir $build_dir
cd $build_dir

create_project -force audio $build_dir -part $part_name

# 把音频 IP 挂进 IP 仓库并重建目录，否则下面按 vlnv 建 cell 会找不到
set_property ip_repo_paths $ip_repo [current_project]
update_ip_catalog -rebuild

create_bd_design "audio"

# ---- 1. PS，配置从官方 base.tcl 抄（ps7_config.tcl）----
set ps7_0 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7_0]
source [file join $script_dir ps7_config.tcl]

# 官方配置里开了两样我们用不到的东西，第 1 步两样都关了。
# 这一步只关 GPIO（那是 RPi/Arduino 排针，要在顶层多接 20 个脚）——
# **I2C1 必须留着**：codec 的配置就走它，经 EMIO 引到 PL 的 U9/T9。
set_property -dict [list \
  CONFIG.PCW_GPIO_EMIO_GPIO_ENABLE {0} \
  CONFIG.PCW_GPIO_EMIO_GPIO_IO {<Select>} \
] $ps7_0

# ---- 2. DDR 和 FIXED_IO 引到顶层 ----
set DDR      [create_bd_intf_port -mode Master -vlnv xilinx.com:interface:ddrx_rtl:1.0 DDR]
set FIXED_IO [create_bd_intf_port -mode Master -vlnv xilinx.com:display_processing_system7:fixedio_rtl:1.0 FIXED_IO]
connect_bd_intf_net -intf_net ps7_0_DDR      [get_bd_intf_ports DDR]      [get_bd_intf_pins ps7_0/DDR]
connect_bd_intf_net -intf_net ps7_0_FIXED_IO [get_bd_intf_ports FIXED_IO] [get_bd_intf_pins ps7_0/FIXED_IO]

# ---- 3. 时钟和复位骨架（和第 1 步一样）----
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_0_100M
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] \
               [get_bd_pins ps7_0/M_AXI_GP0_ACLK] \
               [get_bd_pins ps7_0/S_AXI_GP0_ACLK] \
               [get_bd_pins rst_ps7_0_100M/slowest_sync_clk]
connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_0_100M/ext_reset_in]

# PS 使能的 AXI 端口不止 GP0 一个（配置里 M_AXI_GP1 / S_AXI_HP0 / S_AXI_HP2 也都是开的），
# 每一个的时钟脚都必须接到某个 FCLK 上，否则 validate 直接报 [BD 41-758]。
# 接法照 base.tcl 第 4640 / 4642 行 —— 第 1 步漏了这两条，栽过一次。
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK1] [get_bd_pins ps7_0/S_AXI_HP0_ACLK]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK3] \
               [get_bd_pins ps7_0/M_AXI_GP1_ACLK] \
               [get_bd_pins ps7_0/S_AXI_HP2_ACLK]

# ---- 4. 音频 IP 本体 ----
# 端口：S_AXI（寄存器从接口）/ s_axi_aclk / s_axi_aresetn /
#       bclk, lrclk, sdata_o（出）/ sdata_i（入）/ codec_address（出，固定 2'b11）
set audio [create_bd_cell -type ip -vlnv xilinx.com:user:audio_codec_ctrl:1.0 audio_codec_ctrl_0]

# ---- 5. 10 MHz MCLK，给 codec 的主时钟 ----
# 参数逐项抄自 base.tcl 第 3431~3440 行：100 MHz 进，MMCM 分频出 10 MHz。
# （BCLK = 100 MHz/32、LRCLK = 100 MHz/2048 ≈ 48.83 kHz —— 这两个分频在 IP 内部，
#   改采样率要改 IP 的 RTL，不是改这里。）
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

# ---- 6. AXI 互联：PS 的 GP0 主口 -> 音频 IP 的 S_AXI 从口 ----
set axi_ic [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_ic]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $axi_ic

connect_bd_intf_net -intf_net ps7_0_M_AXI_GP0 [get_bd_intf_pins ps7_0/M_AXI_GP0] [get_bd_intf_pins axi_ic/S00_AXI]
connect_bd_intf_net -intf_net axi_ic_M00_AXI   [get_bd_intf_pins axi_ic/M00_AXI]  [get_bd_intf_pins audio_codec_ctrl_0/S_AXI]

# ---- 7. 时钟与复位接到新加的块上 ----
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] \
               [get_bd_pins rst_ps7_0_100M/slowest_sync_clk] \
               [get_bd_pins clk_wiz_10MHz/clk_in1] \
               [get_bd_pins axi_ic/ACLK] \
               [get_bd_pins axi_ic/S00_ACLK] \
               [get_bd_pins axi_ic/M00_ACLK] \
               [get_bd_pins audio_codec_ctrl_0/s_axi_aclk]

# clk_wiz 的复位是低有效，直接吃 peripheral_aresetn（base.tcl 也是这么接的）
connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] \
               [get_bd_pins clk_wiz_10MHz/resetn] \
               [get_bd_pins axi_ic/ARESETN] \
               [get_bd_pins axi_ic/S00_ARESETN] \
               [get_bd_pins axi_ic/M00_ARESETN] \
               [get_bd_pins audio_codec_ctrl_0/s_axi_aresetn]

# ---- 8. 顶层端口 ----
# 名字必须和 audio_pins.xdc 里的 [get_ports ...] 一模一样，对不上就约束不到管脚。
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

# codec 配置总线：PS 侧 I2C1 的 EMIO 接口 -> 顶层 IIC_1。
# 建了这个 interface port，Vivado 会自动在顶层长出 IIC_1_scl_io / IIC_1_sda_io
# 这两个外部脚 —— 正是 audio_pins.xdc 约束的那两个名字。
set IIC_1 [create_bd_intf_port -mode Master -vlnv xilinx.com:interface:iic_rtl:1.0 IIC_1]
connect_bd_intf_net -intf_net ps7_0_IIC_1 [get_bd_intf_ports IIC_1] [get_bd_intf_pins ps7_0/IIC_1]

# ---- 9. 基地址 ----
# 0x43C00000 和官方 base overlay 给这个 IP 的地址**一样**，
# 所以 pynq.lib.audio 里写死的偏移不用改，驱动照用。
assign_bd_address -offset 0x43C00000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] \
  [get_bd_addr_segs audio_codec_ctrl_0/S_AXI/reg0] -force

regenerate_bd_layout
validate_bd_design
save_bd_design

# ---- 10. 顶层 wrapper + 引脚约束 ----
set wrapper [make_wrapper -files [get_files audio.bd] -top]
add_files -norecurse $wrapper
add_files -fileset constrs_1 -norecurse [file join $script_dir audio_pins.xdc]
set_property top audio_wrapper [current_fileset]
update_compile_order -fileset sources_1

# ---- 11. 综合 + 实现 + 出 bit ----
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
  error "综合没跑完，看 build/vivado/audio/audio.runs/synth_1/runme.log"
}

launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
  error "实现没跑完，看 build/vivado/audio/audio.runs/impl_1/runme.log"
}

open_run impl_1

# ---- 12. 出 PYNQ 要的两个文件 ----
# 坑（第 1 步记下的）：write_hwdef 出的**不是** .hwh，是一个 zip 包，
# 里面装着 <名字>.hwh + ps7_init.c / hwdef.xml 等一大堆。
# 而 PYNQ 是直接把它当 XML 解析的（hwh_parser.py 第 159 行），塞个 zip 进去当场挂。
# 包里那个根标签 <EDKSYSTEM>、一百多 KB 的 <名字>.hwh 才是要的东西，拆出来单独存。
set hwdef_zip [file join $script_dir audio.hwdef]
set hwh_path  [file join $script_dir audio.hwh]
write_hwdef -force -file $hwdef_zip
write_bitstream -force [file join $script_dir audio.bit]

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
    python -c \"import zipfile,sys; zipfile.ZipFile('$hwdef_zip').extract('audio.hwh','$script_dir')\""
}

puts "\n=== 第 2 步完成 ==="
puts "  [file join $script_dir audio.bit]    给 PYNQ 加载的"
puts "  [file join $script_dir audio.hwh]    给 PYNQ 认 IP 的"
puts "  [file join $script_dir audio.hwdef]  Vivado 的原始 zip 包，留着备用"
report_timing_summary -quiet -file [file join $script_dir audio_timing.rpt]
report_utilization  -quiet -file [file join $script_dir audio_util.rpt]
