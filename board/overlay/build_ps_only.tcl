# 自建 overlay —— 第 1 步：只放 Zynq PS，生成 bit + hwh
#
# 为什么要先做这一步：
#   现在能跑音频的 bit 是 PYNQ 官方预编译的 base overlay，不是我们自己出的。
#   要证明"我们自己建的工程能出 bit、而且 PYNQ 能加载它"，
#   就得先跑通「建工程 -> 综合 -> 实现 -> 出 bit -> 出 hwh」这条链路。
#   这一步故意不放音频 IP：万一失败，只可能是流程问题，不会是音频 IP 的问题。
#
# 用法（在任意目录）：
#   E:\Xilinx\Vivado\2020.2\bin\vivado.bat -mode batch -source build_ps_only.tcl
#
# 产物：
#   board/overlay/ps_only.bit   给 PYNQ 加载的
#   board/overlay/ps_only.hwh   给 PYNQ 认 IP 的（必须和 .bit 同名同目录）
#   工程本体在 build/vivado/ps_only/（已被 .gitignore 忽略）
#
# 和官方 base overlay 的差别（只差这两处，都是这一步用不到的）：
#   PS 侧 I2C1 关掉了 —— 它是引到 PL 的 codec 配置总线，第 2 步再加回来
#   10 位 EMIO GPIO 关掉了 —— 那是 RPi/Arduino 排针，我们不用
#   PS 的 DDR / MIO / QSPI / SD / ENET / USB 配置一字未动，保证 PYNQ 镜像照常启动

set script_dir [file normalize [file dirname [info script]]]
set repo_root  [file normalize [file join $script_dir .. ..]]
set build_dir  [file join $repo_root build vivado ps_only]
set part_name  xc7z020clg400-1

file mkdir $build_dir
cd $build_dir

create_project -force ps_only $build_dir -part $part_name

# ---- 1. 空 BD，只放 PS ----
create_bd_design "ps_only"
set ps7_0 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7_0]
source [file join $script_dir ps7_config.tcl]
set_property -dict [list \
  CONFIG.PCW_EN_I2C1 {0} \
  CONFIG.PCW_I2C1_PERIPHERAL_ENABLE {0} \
  CONFIG.PCW_I2C1_I2C1_IO {<Select>} \
  CONFIG.PCW_GPIO_EMIO_GPIO_ENABLE {0} \
  CONFIG.PCW_GPIO_EMIO_GPIO_IO {<Select>} \
] $ps7_0

# ---- 2. DDR 和 FIXED_IO 引到顶层 ----
# PS 的这两组引脚是硬连在芯片上的，DDR 的时序约束由 PS IP 自己生成，
# 所以这一步不需要写任何 XDC。
set DDR      [create_bd_intf_port -mode Master -vlnv xilinx.com:interface:ddrx_rtl:1.0 DDR]
set FIXED_IO [create_bd_intf_port -mode Master -vlnv xilinx.com:display_processing_system7:fixedio_rtl:1.0 FIXED_IO]
connect_bd_intf_net -intf_net ps7_0_DDR      [get_bd_intf_ports DDR]      [get_bd_intf_pins ps7_0/DDR]
connect_bd_intf_net -intf_net ps7_0_FIXED_IO [get_bd_intf_ports FIXED_IO] [get_bd_intf_pins ps7_0/FIXED_IO]

# ---- 3. 时钟和复位，先把骨架搭出来 ----
# 官方 base overlay 里 audio 那条路只吃 FCLK_CLK0（100 MHz），
# 第 2 步加的 IP 全都挂在这个时钟和这个复位上。
# 现在没有任何从设备，这段只是把网拉起来，让第 2 步能直接往上面加 IP。
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_0_100M
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] \
               [get_bd_pins ps7_0/M_AXI_GP0_ACLK] \
               [get_bd_pins ps7_0/S_AXI_GP0_ACLK] \
               [get_bd_pins rst_ps7_0_100M/slowest_sync_clk]
connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_0_100M/ext_reset_in]

# PS 使能的 AXI 端口不止 GP0 一个（配置里 M_AXI_GP1 / S_AXI_HP0 / S_AXI_HP2 也都是开的），
# 每一个的时钟脚都必须接到某个 FCLK 上，否则 validate 直接报 [BD 41-758]
# "The following clock pins are not connected to a valid clock source"。
# 下面这两条是照 base.tcl 的接法抄的（base.tcl 第 4640 / 4642 行）。
# 第一次跑就是漏了这两条才失败的 —— 留着当记录。
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK1] [get_bd_pins ps7_0/S_AXI_HP0_ACLK]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK3] \
               [get_bd_pins ps7_0/M_AXI_GP1_ACLK] \
               [get_bd_pins ps7_0/S_AXI_HP2_ACLK]

regenerate_bd_layout
validate_bd_design
save_bd_design

# ---- 4. 顶层 wrapper ----
set wrapper [make_wrapper -files [get_files ps_only.bd] -top]
add_files -norecurse $wrapper
set_property top ps_only_wrapper [current_fileset]
update_compile_order -fileset sources_1

# ---- 5. 综合 + 实现 + 出 bit ----
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
  error "综合没跑完，看 build/vivado/ps_only/ps_only.runs/synth_1/runme.log"
}

launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
  error "实现没跑完，看 build/vivado/ps_only/ps_only.runs/impl_1/runme.log"
}

open_run impl_1

# ---- 6. 出 PYNQ 要的两个文件 ----
# 这里有个坑，第一次就是这么栽的：
#   write_hwdef 出的**不是**一个 .hwh 文件，而是一个 zip 包
#   （里面装的是 <名字>.hwh + ps7_init.c / ps7_init.tcl / hwdef.xml ... 一大堆）。
#   而 PYNQ 是把这个文件直接当 XML 解析的
#   （pynq/pl_server/hwh_parser.py 第 159 行：tree = ElementTree.parse(hwh_name)），
#   把一个 zip 丢给它，Overlay() 当场就挂。
#   包里那个 <名字>.hwh（根标签 <EDKSYSTEM>，一百多 KB）才是 PYNQ 要的东西，拆出来单独存。
set hwdef_zip [file join $script_dir ps_only.hwdef]
set hwh_path  [file join $script_dir ps_only.hwh]
write_hwdef -force -file $hwdef_zip
write_bitstream -force [file join $script_dir ps_only.bit]

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
    python -c \"import zipfile,sys; zipfile.ZipFile('$hwdef_zip').extract('ps_only.hwh','$script_dir')\""
}

puts "\n=== 第 1 步完成 ==="
puts "  [file join $script_dir ps_only.bit]    给 PYNQ 加载的"
puts "  [file join $script_dir ps_only.hwh]    给 PYNQ 认 IP 的（119 KB，根标签 EDKSYSTEM）"
puts "  [file join $script_dir ps_only.hwdef]  Vivado 的原始 zip 包，里面还有 ps7_init.c 等，留着备用"
report_timing_summary -quiet -file [file join $script_dir ps_only_timing.rpt]
report_utilization  -quiet -file [file join $script_dir ps_only_util.rpt]
