# 独立 RTL overlay —— 只为了把 fir_mb_fe 上板量一遍拍数
#
# 这是 PLAN §11.14 §4「第 9 项」的执行体，三条护栏照抄：
#   ① 只在独立的 fir_rtl.bit / 独立工程里做，**不替换、不加载到出货的 fir.bit**
#   ② 不通就整个删掉，不留半成品
#   ③ 不占打包时间
#
# 结构（刻意做到最笨，因为要一次成型）：
#
#   PS7 M_AXI_GP0 ─ axi_ic ─┬─ bram_ctrl_in  0x43C00000 32KB ──A口─┐
#                           │                                 bram_in(双口) ─B口─┐
#                           ├─ bram_ctrl_out 0x43C08000 32KB ──A口─┐              │
#                           │                                 bram_out(双口)─B口─┤
#                           ├─ gpio_ctrl      0x43C10000                           ├─ fir_rtl_shell ─ fir_mb_fe
#                           └─ gpio_stat      0x43C20000                           ┘
#
# 全程没有任何自研 AXI 逻辑：数据走 axi_bram_ctrl + 双口 BRAM，
# 控制/状态走 axi_gpio。这一条是为了彻底避开"AXI 主机写错把 PS 挂死"
# 那类事故（PROGRESS 里记过一次，Zynq 的 M_AXI_GP 没有超时）。
#
# ⚠️ 2026-10-25 探针实测纠正了四处（原来的写法是静默不生效或直接报错的）：
#   ① axi_bram_ctrl 的参数名是 CONFIG.SINGLE_PORT_BRAM，不是 SINGLE_PORT；
#      MEM_DEPTH / DATA_WIDTH 在 AXI4LITE 下是**只读**（由地址段范围推出来），
#      所以深度不能设，只能靠 assign_bd_address -range 决定。
#   ② axi_bram_ctrl 的原生口给的是 15 位**字节**地址，blk_mem_gen 的 addra 是
#      13 位**字**地址。2026-10-25 上板纠正：接接口时工具接的是 bram_addr_a[12:0]，
#      不是 [14:2] —— 字节地址被当成字地址用。现在改成一根根连 + xlslice 取 [14:2]。
#      B 口不走接口，所以外壳那一侧直接用 13 位字下标。
#   ③ stat 是 64 位，必须拆成两个 32 位口接双通道 axi_gpio，不能直接连 32 位口。
#   ④ axi_bram_ctrl 的 BRAM_PORT 和自研外壳**都是主端**，两个主端连不上
#      （BD 41-171）。中间必须垫一块真双口 BRAM：控制器占 A 口、外壳占 B 口。
#   ⑤ **SINGLE_PORT_BRAM 必须是 1**。填 0 时控制器把 B 口也算作自己的，
#      读数据从没接线的 B 口取，整片缓冲读回来恒为 0x00000008。
#
# 用法：
#   vivado -mode batch -source board/overlay/build_rtl.tcl
#
# 产物：
#   board/overlay/fir_rtl.bit / fir_rtl.hwh
#   board/overlay/rtl_synth_util.rpt
#   board/overlay/fir_rtl_timing.rpt / fir_rtl_util.rpt

set script_dir [file normalize [file dirname [info script]]]
set repo_root  [file normalize [file join $script_dir .. ..]]
set build_dir  [file join $repo_root build vivado fir_rtl]
set part_name  xc7z020clg400-1

file mkdir $build_dir

# ---------------------------------------------------------------- 系数表前置
# 具体逻辑在 coeff_pre.tcl 里（静态文件，免得在 Tcl 里拼 Tcl 拼错转义）。
# 这里只把仓库路径通过环境变量递过去。
set ::env(FIR_RTL_REPO) $repo_root
set pre_tcl [file join $script_dir coeff_pre.tcl]
if {![file exists $pre_tcl]} { error "缺 $pre_tcl" }

# ---------------------------------------------------------------- 工程
cd $build_dir
create_project -force fir_rtl $build_dir -part $part_name

# RTL 必须在建 module cell 之前挂进 sources
add_files -norecurse [list \
  [file join $repo_root src rtl fir_mb_fe.v] \
  [file join $repo_root src rtl fir_drc4.v] \
  [file join $repo_root src rtl fir_rtl_shell.v] ]
update_compile_order -fileset sources_1

create_bd_design "fir_rtl"

# ---- PS，配置从官方 base.tcl 抄（和出货 overlay 同一份）----
set ps7_0 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7_0]
source [file join $script_dir ps7_config.tcl]
# 只关掉 RPi 排针那组 EMIO GPIO（和出货 overlay 一样）。
# **不动 I2C1**：它经 EMIO 引出 IIC_1_scl_io / IIC_1_sda_io 两个顶层脚，
# 出货 fir.bit 就是靠 audio_pins.xdc 里的 U9/T9 约束过的。这里保持 PS 配置
# 和出货版逐字相同（改 PS 配置才是风险来源），另外补一个只含这两条约束的
# 极小 XDC。codec 在本 overlay 里不接，这两个脚闲着不影响任何东西。
set_property -dict [list \
  CONFIG.PCW_GPIO_EMIO_GPIO_ENABLE {0} \
  CONFIG.PCW_GPIO_EMIO_GPIO_IO {<Select>} \
] $ps7_0

# 2026-10-25：这里原本生成并挂一个只约束 IIC_1_scl_io / IIC_1_sda_io 的
# rtl_pins.xdc，但 fir_rtl_wrapper 顶层**根本没有这两个脚**（I2C1 虽在 PS7
# 配置里开着，本 BD 没把它引出成端口），XDC 里包着的 if 又被 Vivado 直接
# 拒绝（Designutils 20-1307），整份约束是空的、只剩一条 CRITICAL WARNING。
# 所以整段删掉，不生成也不挂。

set DDR      [create_bd_intf_port -mode Master -vlnv xilinx.com:interface:ddrx_rtl:1.0 DDR]
set FIXED_IO [create_bd_intf_port -mode Master -vlnv xilinx.com:display_processing_system7:fixedio_rtl:1.0 FIXED_IO]
connect_bd_intf_net -intf_net ps7_0_DDR      [get_bd_intf_ports DDR]      [get_bd_intf_pins ps7_0/DDR]
connect_bd_intf_net -intf_net ps7_0_FIXED_IO [get_bd_intf_ports FIXED_IO] [get_bd_intf_pins ps7_0/FIXED_IO]

# ---- 复位骨架 ----
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_0_100M
connect_bd_net [get_bd_pins ps7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_0_100M/ext_reset_in]
# 时钟统一放到下面「---- 时钟 ----」一次连完（同一个源脚不能出现两次，BD 41-249）。

# ---- 四个从设备 ----
set axi_ic [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_ic]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {4}] $axi_ic

# 每个缓冲 32 KB = 8192 个 32 位字。深度由后面的 assign_bd_address -range 决定，
# 这里**不要**设 MEM_DEPTH（AXI4LITE 下只读，设了会被静默忽略）。
#
# 中间必须垫一块真双口 BRAM：axi_bram_ctrl 的 BRAM_PORT 是**主端**，
# 我们的外壳也是主端（它自己出地址读数据），两个主端连不上
# （BD 41-171）。标准接法是「控制器 A 口、自研主端 B 口」各占一边。
foreach nm {in out} {
  set bc [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 bram_ctrl_$nm]
  # ⚠️ SINGLE_PORT_BRAM 必须是 **1**（2026-10-25 上板实测）。
  # 填 0（双口）时控制器认为两个 BRAM 口都归它用：写走 A 口、**读走 B 口**。
  # 而我们的 B 口是给自研外壳用的，控制器那侧的 B 口没人接，Vivado 就把
  # bram_rddata_b 绑成常量 —— 于是整片缓冲读回来恒为 0x00000008、
  # 写进去的东西永远读不到。xsim 里完全看不出来，只有上板才露。
  set_property -dict [list \
    CONFIG.PROTOCOL {AXI4LITE} \
    CONFIG.SINGLE_PORT_BRAM {1} \
    CONFIG.DATA_WIDTH {32} \
    CONFIG.READ_LATENCY {1} \
  ] $bc

  set bm [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 bram_$nm]
  # 顺序要紧：32 位数据宽在默认 Byte_Size=9 下是非法的（必须是 9 的倍数），
  # 所以先开字节写使能、再把 Byte_Size 改成 8，最后才设宽度。
  # 合成一个 -dict 会整批校验失败，一条都落不下去。
  set_property CONFIG.Use_Byte_Write_Enable {true} $bm
  set_property CONFIG.Byte_Size {8} $bm
  set_property -dict [list \
    CONFIG.Memory_Type {True_Dual_Port_RAM} \
    CONFIG.use_bram_block {Stand_Alone} \
    CONFIG.EN_SAFETY_CKT {false} \
    CONFIG.Register_PortA_Output_of_Memory_Primitives {false} \
    CONFIG.Register_PortB_Output_of_Memory_Primitives {false} \
    CONFIG.Write_Width_A {32} \
    CONFIG.Write_Depth_A {8192} \
    CONFIG.Read_Width_A {32} \
    CONFIG.Write_Width_B {32} \
    CONFIG.Read_Width_B {32} \
    CONFIG.Enable_A {Use_ENA_Pin} \
    CONFIG.Enable_B {Use_ENB_Pin} \
    CONFIG.Operating_Mode_A {READ_FIRST} \
    CONFIG.Operating_Mode_B {READ_FIRST} \
  ] $bm
}

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

# ---- 自研外壳（内部例化 fir_mb_fe）----
set shell [create_bd_cell -type module -reference fir_rtl_shell fir_rtl_shell_0]

# 外壳的两个 BRAM RST 端口在本设计里没用（模块内部完全没引用），
# 但空着会留悬空网络，接一个常量 0。
set const0 [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 const_zero]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $const0

# ---- 控制通路 ----
connect_bd_intf_net -intf_net ps7_0_M_AXI_GP0 [get_bd_intf_pins ps7_0/M_AXI_GP0] [get_bd_intf_pins axi_ic/S00_AXI]
connect_bd_intf_net -intf_net axi_ic_m00 [get_bd_intf_pins axi_ic/M00_AXI] [get_bd_intf_pins bram_ctrl_in/S_AXI]
connect_bd_intf_net -intf_net axi_ic_m01 [get_bd_intf_pins axi_ic/M01_AXI] [get_bd_intf_pins bram_ctrl_out/S_AXI]
connect_bd_intf_net -intf_net axi_ic_m02 [get_bd_intf_pins axi_ic/M02_AXI] [get_bd_intf_pins gpio_ctrl/S_AXI]
connect_bd_intf_net -intf_net axi_ic_m03 [get_bd_intf_pins axi_ic/M03_AXI] [get_bd_intf_pins gpio_stat/S_AXI]

# ---- 数据通路 ----
# A 口（AXI 侧）**不能用 connect_bd_intf_net 连接口**，一根根连普通引脚。
#
# 两个原因，都是 2026-10-25 上板才暴露的：
#
#   ① 地址语义不同。axi_bram_ctrl 的 bram_addr_a 是 15 位**字节**地址，
#      blk_mem_gen 的 addra 是 13 位**字**地址。直接 connect_bd_intf_net 会
#      报 [BD 41-2384] 并把 addra 接成 bram_addr_a[12:0]（连低 13 位），
#      等于拿字节地址当字地址用 —— 采样 k 落到第 4k 个字，2048 点之后回卷。
#      必须用 xlslice 取 [14:2]。
#
#   ② wea 只有 1 位（blk_mem_gen 没开字节写使能），bram_we_a 是 4 位，
#      接口连接只会接最低位。普通 connect_bd_net 也一样会报位宽不符，
#      但「只接最低位」对 32 位整字写正好是对的（WSTRB=0xF 时 bram_we_a=4'hF）。
#
# B 口仍然一根根连普通信号（见 fir_rtl_shell.v 顶部注释）。
foreach nm {in out} {
  set sl [create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice:1.0 slice_$nm]
  set_property -dict [list \
    CONFIG.DIN_WIDTH {15} CONFIG.DIN_FROM {14} CONFIG.DIN_TO {2} \
    CONFIG.DOUT_WIDTH {13} \
  ] $sl

  connect_bd_net [get_bd_pins bram_ctrl_$nm/bram_addr_a]   [get_bd_pins slice_$nm/Din]
  connect_bd_net [get_bd_pins slice_$nm/Dout]              [get_bd_pins bram_$nm/addra]
  connect_bd_net [get_bd_pins bram_ctrl_$nm/bram_wrdata_a] [get_bd_pins bram_$nm/dina]
  connect_bd_net [get_bd_pins bram_ctrl_$nm/bram_en_a]     [get_bd_pins bram_$nm/ena]
  connect_bd_net [get_bd_pins bram_ctrl_$nm/bram_we_a]     [get_bd_pins bram_$nm/wea]
  connect_bd_net [get_bd_pins bram_ctrl_$nm/bram_rddata_a] [get_bd_pins bram_$nm/douta]
}
connect_bd_net [get_bd_pins fir_rtl_shell_0/bram_in_en]   [get_bd_pins bram_in/enb]
connect_bd_net [get_bd_pins fir_rtl_shell_0/bram_in_we]   [get_bd_pins bram_in/web]
connect_bd_net [get_bd_pins fir_rtl_shell_0/bram_in_addr] [get_bd_pins bram_in/addrb]
connect_bd_net [get_bd_pins fir_rtl_shell_0/bram_in_din]  [get_bd_pins bram_in/dinb]
connect_bd_net [get_bd_pins fir_rtl_shell_0/bram_in_dout] [get_bd_pins bram_in/doutb]

connect_bd_net [get_bd_pins fir_rtl_shell_0/bram_out_en]   [get_bd_pins bram_out/enb]
connect_bd_net [get_bd_pins fir_rtl_shell_0/bram_out_we]   [get_bd_pins bram_out/web]
connect_bd_net [get_bd_pins fir_rtl_shell_0/bram_out_addr] [get_bd_pins bram_out/addrb]
connect_bd_net [get_bd_pins fir_rtl_shell_0/bram_out_din]  [get_bd_pins bram_out/dinb]
connect_bd_net [get_bd_pins fir_rtl_shell_0/bram_out_dout] [get_bd_pins bram_out/doutb]

# ---- 寄存器 ----
# stat 是 64 位，拆成两个 32 位口：通道 1 放 total_cycles，通道 2 放状态位
connect_bd_net [get_bd_pins gpio_ctrl/gpio_io_o] [get_bd_pins fir_rtl_shell_0/ctrl]
connect_bd_net [get_bd_pins gpio_stat/gpio_io_i]  [get_bd_pins fir_rtl_shell_0/stat_lo]
connect_bd_net [get_bd_pins gpio_stat/gpio2_io_i] [get_bd_pins fir_rtl_shell_0/stat_hi]

# ---- 时钟 ----
# 只能有一条从 FCLK_CLK0 **引脚**出发的 connect_bd_net：同一个源脚出现两次
# 会报 BD 41-249。所以全部时钟脚一次连完。
# 配置里 M_AXI_GP1 / S_AXI_HP0 / S_AXI_HP2 都开着，每个时钟脚都得接，
# 否则 validate 报 [BD 41-758]（出货 overlay 那边栽过）。
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0] \
               [get_bd_pins ps7_0/M_AXI_GP0_ACLK] \
               [get_bd_pins ps7_0/S_AXI_GP0_ACLK] \
               [get_bd_pins ps7_0/S_AXI_HP0_ACLK] \
               [get_bd_pins ps7_0/S_AXI_HP2_ACLK] \
               [get_bd_pins ps7_0/M_AXI_GP1_ACLK] \
               [get_bd_pins rst_ps7_0_100M/slowest_sync_clk] \
               [get_bd_pins axi_ic/ACLK] \
               [get_bd_pins axi_ic/S00_ACLK] \
               [get_bd_pins axi_ic/M00_ACLK] \
               [get_bd_pins axi_ic/M01_ACLK] \
               [get_bd_pins axi_ic/M02_ACLK] \
               [get_bd_pins axi_ic/M03_ACLK] \
               [get_bd_pins bram_ctrl_in/s_axi_aclk] \
               [get_bd_pins bram_ctrl_out/s_axi_aclk] \
               [get_bd_pins gpio_ctrl/s_axi_aclk] \
               [get_bd_pins gpio_stat/s_axi_aclk] \
               [get_bd_pins bram_in/clka] \
               [get_bd_pins bram_in/clkb] \
               [get_bd_pins bram_out/clka] \
               [get_bd_pins bram_out/clkb] \
               [get_bd_pins fir_rtl_shell_0/clk] \
               [get_bd_pins fir_rtl_shell_0/bram_in_clk] \
               [get_bd_pins fir_rtl_shell_0/bram_out_clk]

# axi_bram_ctrl 的 bram_clk_a/b 是它把 s_axi_aclk 转发出来的**输出**，
# 这里不接 —— BRAM 时钟直接从同一根 FCLK 网给，电气上完全等价。

# 外壳的两个 BRAM RST 端口在本设计里没用（模块内部完全没引用），
# 但空着会留悬空网络，接常量 0。axi_bram_ctrl 的 bram_rst_* 同样是 IP 输出，
# 不接（BRAM 本身没有复位脚）。
connect_bd_net [get_bd_pins const_zero/dout] \
               [get_bd_pins fir_rtl_shell_0/bram_in_rst] \
               [get_bd_pins fir_rtl_shell_0/bram_out_rst]

connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] \
               [get_bd_pins axi_ic/ARESETN] \
               [get_bd_pins axi_ic/S00_ARESETN] \
               [get_bd_pins axi_ic/M00_ARESETN] \
               [get_bd_pins axi_ic/M01_ARESETN] \
               [get_bd_pins axi_ic/M02_ARESETN] \
               [get_bd_pins axi_ic/M03_ARESETN] \
               [get_bd_pins bram_ctrl_in/s_axi_aresetn] \
               [get_bd_pins bram_ctrl_out/s_axi_aresetn] \
               [get_bd_pins gpio_ctrl/s_axi_aresetn] \
               [get_bd_pins gpio_stat/s_axi_aresetn] \
               [get_bd_pins fir_rtl_shell_0/rst_n]

# ---- 地址：四个从口，互不相撞 ----
# 两个缓冲各 32 KB（8192 字 × 4 B），这是 MEM_DEPTH 的唯一来源。
proc seg_of {cell} {
  set s [get_bd_addr_segs -quiet ${cell}/S_AXI/*]
  if {[llength $s] == 0} {
    set s [get_bd_addr_segs -quiet -filter "NAME =~ *${cell}*"]
  }
  if {[llength $s] == 0} {
    puts "==== existing addr segs ===="
    foreach x [get_bd_addr_segs *] { puts "  $x" }
    error "no addr seg found for ${cell}, see dump above"
  }
  return [lindex $s 0]
}

assign_bd_address -offset 0x43C00000 -range 0x00008000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of bram_ctrl_in] -force
assign_bd_address -offset 0x43C08000 -range 0x00008000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of bram_ctrl_out] -force
assign_bd_address -offset 0x43C10000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of gpio_ctrl] -force
assign_bd_address -offset 0x43C20000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of gpio_stat] -force

puts "==== ADDR MAP ===="
foreach s [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps7_0/Data]] {
  puts "  [get_property NAME $s]  offset=[get_property offset $s]  range=[get_property range $s]"
}


regenerate_bd_layout
validate_bd_design
save_bd_design

# ---- 强制全局综合 ----
# BD 默认把 module reference（我们的外壳）拆成一个独立的 OOC 综合 run，
# 那个 run 的 cwd 不是 synth_1，$readmemh 找不到系数表 —— Vivado 只报
# CRITICAL WARNING 不报错，ROM 变全 X，整条 FIR 被折成常数，
# 到实现阶段连 BRAM/DSP 一起被删干净（现象：util 里 DSP=0、BRAM=0）。
# 设成 None 让所有东西都在 synth_1 里综合，TCL.PRE 才管用。
set_property synth_checkpoint_mode None [get_files fir_rtl.bd]

# ---- 顶层 wrapper ----
set wrapper [make_wrapper -files [get_files fir_rtl.bd] -top]
add_files -norecurse $wrapper

# ---- 保住 BD 里那两块双口 BRAM（2026-10-25 实测，不加就整颗核消失）----
# 现象：综合日志里 189 行 "Generating DSP"、DSP 初步映射表 189 行，最后网表里
# DSP48 = 0、RAMB = 0、wr_en/wr_idx/dout_reg/p[]/系数 ROM 全被删；util 里
# 只剩 PS7 + AXI 骨架。单独综合同一份 RTL（不带 BD）是 197 个 DSP。
# 原因：bram_out 的 B 口写数据就是 FIR 输出，A 口那侧又只有 AXI 从机在读，
# 综合把"只被 AXI 读、没有别的观察者"的存储判成可删，于是写口整条反向剪掉
# （f_dout → wr_data → 整个 FIR）。两块 BRAM 都加 DONT_TOUCH 后实测
# DSP48 = 197、RAMB = 20，FIR 完整保留。
set keep_xdc [file join $build_dir rtl_keep.xdc]
set fh_keep [open $keep_xdc w]
puts $fh_keep "## 自动生成（build_rtl.tcl）—— 别让综合把 BD 里那两块 BRAM 当死存储删掉"
puts $fh_keep "set_property DONT_TOUCH true \[get_cells -hier -filter {REF_NAME =~ fir_rtl_bram_*_0}\]"
close $fh_keep
add_files -fileset constrs_1 -norecurse $keep_xdc

update_compile_order -fileset sources_1
set_property top fir_rtl_wrapper [current_fileset]

# ---- 系数表前置钩子 ----
set_property STEPS.SYNTH_DESIGN.TCL.PRE $pre_tcl [get_runs synth_1]

# ---- 综合 ----
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
  error "综合没跑完，看 $build_dir/fir_rtl.runs/synth_1/runme.log"
}
open_run synth_1
report_utilization -file [file join $script_dir rtl_synth_util.rpt]

# 兜底：系数表没读到的时候 Vivado 只报 CRITICAL WARNING 不报错，
# 整条 FIR 会被折成常数、DSP 掉到个位数，然后照样 BUILD SUCCEEDED。
# 这里直接拦死，免得又出一颗空壳 bitstream。
set s_dsp [llength [get_cells -hier -filter {REF_NAME =~ DSP48*}]]
set s_bram [llength [get_cells -hier -filter {REF_NAME =~ RAMB*}]]
puts "### post-synth DSP48 = $s_dsp   RAMB = $s_bram"
if {$s_dsp < 100} {
  error "post-synth DSP48 = $s_dsp only: FIR got constant-folded (coefficient .mem missing)"
}

# ---- 实现 ----
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
  error "实现没跑完，看 $build_dir/fir_rtl.runs/impl_1/runme.log"
}
open_run impl_1

# ---- PYNQ 要的两个文件 ----
# write_hwdef 出的是 zip，里面根标签 <EDKSYSTEM> 的那个才是 .hwh（第 1 步踩过）
set hwdef_zip [file join $script_dir fir_rtl.hwdef]
set hwh_path  [file join $script_dir fir_rtl.hwh]
write_hwdef -force -file $hwdef_zip
write_bitstream -force [file join $script_dir fir_rtl.bit]

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
if {!$unzip_ok} { puts "!!! 拆 hwh 失败，手工执行：python -c \"import zipfile;zipfile.ZipFile('fir_rtl.hwdef').extract('fir_rtl.hwh','.')\"" }

report_timing_summary -quiet -file [file join $script_dir fir_rtl_timing.rpt]
report_utilization   -quiet -file [file join $script_dir fir_rtl_util.rpt]

set wns  [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -delay_type max]]
set dsps [llength [get_cells -hier -filter {REF_NAME =~ DSP48*}]]
set lut  [llength [get_cells -hier -filter {PRIMITIVE_GROUP == LUT}]]
set ff   [llength [get_cells -hier -filter {PRIMITIVE_GROUP == FLOP_LATCH}]]
puts "\n### RESULT"
puts "### WNS_ns      = $wns"
puts "### DSP48       = $dsps"
puts "### LUT         = $lut"
puts "### FF          = $ff"
puts "### bit         = [file join $script_dir fir_rtl.bit]"
puts "### hwh         = $hwh_path"
puts "### DONE"
exit
