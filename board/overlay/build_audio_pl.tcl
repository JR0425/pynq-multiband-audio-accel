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
# 允许换一个工程目录（BD 段迭代时用 build/vivado/bd_scratch，免得反复
# create_project -force 破坏正式工程里已有的综合结果）
if {[info exists ::env(FIR_PL_BUILD_DIR)]} {
    set build_dir $::env(FIR_PL_BUILD_DIR)
}
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
  [file join $repo_root src rtl fir_gain_ramp.v] \
  [file join $repo_root src rtl fir_pl_regs.v] \
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
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {6}] $axi_ic

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

# ---- 参数寄存器（2026-10-28 加）------------------------------------------
# 2 个 axi_gpio 只能搬 32 位整数，装不下「四段增益 × 4 段参数」。
# 这里挂一个自研的 AXI4-Lite 从机 fir_pl_regs（32 个寄存器 + 双耳两组银行），
# 地址 0x43C40000。它同时把 RAMP_DIV / RAMP_BUSY 也管了。
# 注意：CTRL 仍然走 gpio_ctrl（0x43C00000），探针和演示脚本都按那个地址写的，
# 换地址会让所有现成的脚本失效 —— 等网页那一档稳定了再合并。
set regs [create_bd_cell -type module -reference fir_pl_regs fir_pl_regs_0]
puts "==== fir_pl_regs_0 的引脚 ===="
foreach p [get_bd_pins -quiet fir_pl_regs_0/*] { puts "  $p" }

# 参数包直连：fir_pl_regs 的输出位宽已经按 fir_pl_direct 的需要裁好
# （增益每段 16 位 → 64 位；thr/ratio/atk/rel 每段 24 位 → 96 位），
# 所以这里不再需要 xlslice。之前的接法是 128 位打包 + 取 [63:0] 再当四个 16 位用，
# 结果 b1/b3 段吃到 0、b2 段吃到 b1 的值（批 1 位流就带着这个 bug）。

# 只读输入先全部接 0（电平表/包络/播放/抓取都还没实现）
set z128 [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 z128]
set_property -dict [list CONFIG.CONST_WIDTH {128} CONFIG.CONST_VAL {0}] $z128
set z32 [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 z32]
set_property -dict [list CONFIG.CONST_WIDTH {32} CONFIG.CONST_VAL {0}] $z32
set z1 [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 z1]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $z1

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
# ---- 波表 BRAM：板上 WAV 音源（2026-10-28 批 1）----
# PS 经 axi_bram_ctrl 持续填充 WAV（环形,16 位样本放 [31:16]）,
# B 口给 fir_pl_direct 按采样节拍读。16384 字 × 32 位 = 64 KB（表口径）。
set ply_bram [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 ply_bram]
set_property -dict [list \
  CONFIG.Memory_Type {True_Dual_Port_RAM} \
  CONFIG.Write_Width_A {32} \
  CONFIG.Write_Depth_A {16384} \
  CONFIG.Use_Byte_Write_Enable {false} \
  CONFIG.Enable_B {Use_ENB_Pin} \
  CONFIG.Register_PortA_Enable_of_Memory_Primitives {false} \
  CONFIG.Register_PortB_Enable_of_Memory_Primitives {false} \
] $ply_bram
set ply_ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 ply_ctrl]
set_property -dict [list CONFIG.SINGLE_PORT_BRAM {0} CONFIG.DATA_WIDTH {32}] $ply_ctrl
connect_bd_intf_net [get_bd_intf_pins ply_ctrl/BRAM_PORTA] [get_bd_intf_pins ply_bram/BRAM_PORTA]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0]           [get_bd_pins ply_bram/clkb]
connect_bd_net [get_bd_pins fir_pl_direct_0/ply_en]    [get_bd_pins ply_bram/enb]
connect_bd_net [get_bd_pins fir_pl_direct_0/ply_addr]  [get_bd_pins ply_bram/addrb]
connect_bd_net [get_bd_pins ply_bram/doutb]            [get_bd_pins fir_pl_direct_0/ply_rdata]

# ---- 抓取缓冲（C3，2026-10-29）：4096×32，低 16 位进核 / 高 16 位出核 ----
# A 口给 PS 读（经 fir_pl_regs 的 0x78/0x7C 窗口），B 口给 fir_pl_direct 的写口。
# 4096 点 @ 48.8 kHz ≈ 84 ms 历史，够画时域波形和做 4096 点 FFT。
set cap_bram [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 cap_bram]
set_property -dict [list \
  CONFIG.Memory_Type {True_Dual_Port_RAM} \
  CONFIG.Write_Width_A {32} \
  CONFIG.Write_Depth_A {4096} \
  CONFIG.Use_Byte_Write_Enable {false} \
  CONFIG.Enable_A {Always_Enabled} \
  CONFIG.Enable_B {Use_ENB_Pin} \
  CONFIG.Register_PortA_Enable_of_Memory_Primitives {false} \
  CONFIG.Register_PortB_Enable_of_Memory_Primitives {false} \
] $cap_bram
# 2026-10-30 夜：**不再挂 axi_bram_ctrl**。
# 板上实测：0x43C50000（波表）和 0x43C60000（抓取）都是「写能进去、读回来恒 0x8」，
# 而同一块 AXI 上的 axi_gpio / fir_pl_regs 读得好好的 —— 是 BRAM 控制器这条读通路
# 不通，不是地址映射。与其赌再编一版还是不通，改成把 A 口直接交给 fir_pl_regs：
#   写 0x78 选下标 → 读 0x7C 取该字（寄存器窗口的读是验过的）
# 抓取一冻结环形缓冲就不再变，4096 点分 8192 次 MMIO 读完约 10 ms。
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0]         [get_bd_pins cap_bram/clka]
connect_bd_net [get_bd_pins fir_pl_regs_0/cap_raddr] [get_bd_pins cap_bram/addra]
connect_bd_net [get_bd_pins cap_bram/douta]          [get_bd_pins fir_pl_regs_0/cap_rdata]
# 端口 A 只读：ena 必须常 1（这版 blk_mem_gen 的 Enable_A 参数是禁用的，
# 改不了成 Always_Enabled —— 之前漏接 ena，douta 永远不更新，读回来全 0，
# 2026-10-30 首版就是这样，白编了一版）。wea 是 4 位字节写使能（Use_Byte_Write_Enable
# 同样被禁用），接 4 位常量 0；dina 接 32 位常量 0。
set const0 [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant const0]
set_property -dict [list CONFIG.CONST_WIDTH {32} CONFIG.CONST_VAL {0}] $const0
connect_bd_net [get_bd_pins const0/dout]             [get_bd_pins cap_bram/dina]
set const1 [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant const1]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] $const1
set const2 [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant const2]
set_property -dict [list CONFIG.CONST_WIDTH {4} CONFIG.CONST_VAL {0}] $const2
connect_bd_net [get_bd_pins const2/dout]             [get_bd_pins cap_bram/wea]
set const3 [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant const3]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $const3
connect_bd_net [get_bd_pins const3/dout]             [get_bd_pins cap_bram/ena]
connect_bd_net [get_bd_pins ps7_0/FCLK_CLK0]          [get_bd_pins cap_bram/clkb]
connect_bd_net [get_bd_pins fir_pl_direct_0/cap_we]   [get_bd_pins cap_bram/web]
# enb 漏接 = 永远不使能 = 一个采样都没写进去（首版就是这个坑：读回来全 0）
connect_bd_net [get_bd_pins fir_pl_direct_0/cap_we]   [get_bd_pins cap_bram/enb]
connect_bd_net [get_bd_pins fir_pl_direct_0/cap_addr] [get_bd_pins cap_bram/addrb]
connect_bd_net [get_bd_pins fir_pl_direct_0/cap_din]  [get_bd_pins cap_bram/dinb]

# 抓取控制与指针：0x64 bit0 = snap（1 = 冻结写指针），0x6C = 写指针
connect_bd_net [get_bd_pins fir_pl_regs_0/scope_ctl]  [get_bd_pins fir_pl_direct_0/scope_snap]
connect_bd_net [get_bd_pins fir_pl_direct_0/scope_wr] [get_bd_pins fir_pl_regs_0/scope_wr]

# 播放控制来自寄存器（ply_ctl[0] en、[1] loop；ply_len = 波表字数）
connect_bd_net [get_bd_pins fir_pl_regs_0/ply_ctl] [get_bd_pins fir_pl_direct_0/ply_ctl]
connect_bd_net [get_bd_pins fir_pl_regs_0/ply_len] [get_bd_pins fir_pl_direct_0/ply_len]

# 2026-10-29 夜：测试音（B4 测听）+ MPO 限幅（B6）
#   0x58 TONE_SEL / 0x5C TONE_AMP → direct 内部的相位累加器与幅度
#   0x48 MPO_TH  / 0x4C MPO_REL   → 输出软限幅的阈值
#   0x50 MPO_CNT / 0x54 MPO_PEAK  ← 被限幅的采样数 / 见过的最大输出
connect_bd_net [get_bd_pins fir_pl_regs_0/tone_sel]  [get_bd_pins fir_pl_direct_0/tone_sel]
connect_bd_net [get_bd_pins fir_pl_regs_0/tone_amp]  [get_bd_pins fir_pl_direct_0/tone_amp]
connect_bd_net [get_bd_pins fir_pl_regs_0/mpo_th]    [get_bd_pins fir_pl_direct_0/mpo_th]
connect_bd_net [get_bd_pins fir_pl_regs_0/mpo_rel]   [get_bd_pins fir_pl_direct_0/mpo_rel]
connect_bd_net [get_bd_pins fir_pl_direct_0/mpo_cnt] [get_bd_pins fir_pl_regs_0/mpo_cnt]
connect_bd_net [get_bd_pins fir_pl_direct_0/mpo_peak] [get_bd_pins fir_pl_regs_0/mpo_peak]

puts "==== fir_pl_regs_0 的 AXI 接口 ===="
foreach ip [get_bd_intf_pins -quiet fir_pl_regs_0/*] { puts "  intf $ip" }
if {[catch {
  connect_bd_intf_net -intf_net axi_ic_m04 [get_bd_intf_pins axi_ic/M04_AXI]                       [get_bd_intf_pins fir_pl_regs_0/S_AXI]
} _e]} {
  puts "### M04 连接失败: $_e"
  error $_e
}

if {[catch {
  connect_bd_intf_net -intf_net axi_ic_m05 [get_bd_intf_pins axi_ic/M05_AXI]                       [get_bd_intf_pins ply_ctrl/S_AXI]
} _e]} {
  puts "### M05 连接失败: $_e"
  error $_e
}

connect_bd_net [get_bd_pins gpio_ctrl/gpio_io_o]  [get_bd_pins fir_pl_direct_0/ctrl]
connect_bd_net [get_bd_pins gpio_stat/gpio_io_i]  [get_bd_pins fir_pl_direct_0/stat0]
connect_bd_net [get_bd_pins gpio_stat/gpio2_io_i] [get_bd_pins fir_pl_direct_0/stat1]
connect_bd_net [get_bd_pins gpio_stat2/gpio_io_i] [get_bd_pins fir_pl_direct_0/stat2]
connect_bd_net [get_bd_pins gpio_dbg/gpio_io_i]   [get_bd_pins fir_pl_direct_0/dbg0]
connect_bd_net [get_bd_pins gpio_dbg/gpio2_io_i]  [get_bd_pins fir_pl_direct_0/dbg1]

# ---- 运行期参数：增益/斜坡 + DRC 四参数（2026-10-28 批 2）----
connect_bd_net [get_bd_pins fir_pl_regs_0/gain_b0]     [get_bd_pins fir_pl_direct_0/gain_pack]
connect_bd_net [get_bd_pins fir_pl_regs_0/ramp_div_b0] [get_bd_pins fir_pl_direct_0/ramp_div]
connect_bd_net [get_bd_pins fir_pl_direct_0/ramp_busy] [get_bd_pins fir_pl_regs_0/ramp_busy_b0]
foreach pk {thr ratio atk rel} {
  connect_bd_net [get_bd_pins fir_pl_regs_0/${pk}_b0] [get_bd_pins fir_pl_direct_0/${pk}_pack]
}

# ---- 双耳（2026-10-29）：bank1 那套参数也从寄存器来 ----
# 以前这四组和 gain_b1 都接到常量 0，右耳永远是静音；现在接真的。
connect_bd_net [get_bd_pins fir_pl_regs_0/gain_b1]  [get_bd_pins fir_pl_direct_0/gain_pack_b1]
connect_bd_net [get_bd_pins fir_pl_direct_0/ramp_busy_b1] [get_bd_pins fir_pl_regs_0/ramp_busy_b1]
foreach pk {thr ratio atk rel} {
  connect_bd_net [get_bd_pins fir_pl_regs_0/${pk}_b1] [get_bd_pins fir_pl_direct_0/${pk}_pack_b1]
}

# ---- 观察量接真信号（2026-10-28 批 1）：env/gr/peak 不再是假线 ----
connect_bd_net [get_bd_pins fir_pl_direct_0/env_b0]   [get_bd_pins fir_pl_regs_0/env_b0]
connect_bd_net [get_bd_pins fir_pl_direct_0/gr_b0]    [get_bd_pins fir_pl_regs_0/gr_b0]
connect_bd_net [get_bd_pins fir_pl_direct_0/env_b1]   [get_bd_pins fir_pl_regs_0/env_b1]
connect_bd_net [get_bd_pins fir_pl_direct_0/gr_b1]    [get_bd_pins fir_pl_regs_0/gr_b1]
connect_bd_net [get_bd_pins fir_pl_direct_0/peak_in]  [get_bd_pins fir_pl_regs_0/peak_in]
connect_bd_net [get_bd_pins fir_pl_direct_0/peak_out] [get_bd_pins fir_pl_regs_0/peak_out]

# ---- 只读输入：先接 0，等电平表/抓取做出来再换 ----
connect_bd_net [get_bd_pins fir_pl_direct_0/stat0] [get_bd_pins fir_pl_regs_0/stat0]
connect_bd_net [get_bd_pins fir_pl_direct_0/stat1] [get_bd_pins fir_pl_regs_0/stat1]
connect_bd_net [get_bd_pins fir_pl_direct_0/stat2] [get_bd_pins fir_pl_regs_0/stat2]


foreach pin {smp_cnt frm_cnt ply_rd ply_und lvl_in_lo lvl_in_hi lvl_out_lo lvl_out_hi scope_seq} {
  connect_bd_net [get_bd_pins z32/dout] [get_bd_pins fir_pl_regs_0/$pin]
}

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
                              [get_bd_pins axi_ic/M04_ACLK]                               [get_bd_pins axi_ic/M05_ACLK] \
                              [get_bd_pins fir_pl_regs_0/clk] \
               [get_bd_pins gpio_ctrl/s_axi_aclk] \
               [get_bd_pins gpio_stat/s_axi_aclk] \
               [get_bd_pins gpio_stat2/s_axi_aclk] \
               [get_bd_pins gpio_dbg/s_axi_aclk] \
                              [get_bd_pins ply_ctrl/s_axi_aclk] \
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
                              [get_bd_pins axi_ic/M04_ARESETN]                               [get_bd_pins axi_ic/M05_ARESETN] \
                              [get_bd_pins fir_pl_regs_0/rst_n] \
               [get_bd_pins gpio_ctrl/s_axi_aresetn] \
               [get_bd_pins gpio_stat/s_axi_aresetn] \
               [get_bd_pins gpio_stat2/s_axi_aresetn] \
               [get_bd_pins gpio_dbg/s_axi_aresetn]                               [get_bd_pins ply_ctrl/s_axi_aresetn] \
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

# fir_pl_regs 只译码 12 位地址(0x000-0x27F 在用),4 KB 就够。
# 2026-10-28 补：之前只挂了 M04 却没给地址,validate 一直报
#   CRITICAL WARNING: [BD 41-1356] Slave segment </fir_pl_regs_0/s_axi/reg0>
#   is not assigned into address space </ps7_0/Data>
# 它是 CRITICAL WARNING 不是 ERROR,所以 BD_ONLY 照样"通过",位流照样编得出来 ——
# 但 PS 读 0x43C40000 会直接总线错。**告警必须归零,不能靠 exit code 判。**
assign_bd_address -offset 0x43C40000 -range 0x00001000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin fir_pl_regs_0/S_AXI] -force

# 波表 BRAM：16384 字 × 4 B = 64 KB（环形,PS 持续填）
assign_bd_address -offset 0x43C50000 -range 0x00010000 \
  -target_address_space [get_bd_addr_spaces ps7_0/Data] [seg_of_pin ply_ctrl/S_AXI] -force

# 抓取缓冲不再占 AXI 地址：改由 fir_pl_regs 的 0x78/0x7C 窗口读（见上）

puts "==== ADDR MAP ===="
foreach s [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps7_0/Data]] {
  puts "  [get_property NAME $s]  offset=[get_property offset $s]  range=[get_property range $s]"
}
# 五个从机一个都不能少。少一个也只会是 CRITICAL WARNING,不会中断构建,
# 所以这里主动数一遍,缺了就停 —— 免得又编出一块"寄存器写不进去"的位流。
set want {SEG_gpio_ctrl_Reg SEG_gpio_stat_Reg SEG_gpio_stat2_Reg SEG_gpio_dbg_Reg SEG_fir_pl_regs_0_reg0}
set have {}
foreach s [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps7_0/Data]] {
  lappend have [get_property NAME $s]
}
foreach w $want {
  if {[lsearch -exact $have $w] < 0} {
    error "ADDR MAP 缺 $w,现有:$have"
  }
}
# 波表 BRAM 从机也在里面（段名带 mem0 后缀,提前写死容易错,按"至少 6 个从机"判）。
# 2026-10-30 夜撤掉 cap_ctrl 后从 7 个降到 6 个：4 个 gpio + regs + 波表。
if {[llength $have] < 6} {
  error "ADDR MAP 只有 [llength $have] 个从机,少了 ply_ctrl 的段。现有:$have"
}
puts "### ADDR MAP OK ($have)"

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

# 2026-10-30：默认策略下最后只剩 1 个端点差 21 ps（DRC 里 p_gr → yv 那条：
# 减法 → 绝对值钳位 → 取负 → 饱和，四级组合逻辑串在一起）。
# 不动 RTL（加一拍会改整条链的延迟对齐，得重跑全部 tb），先用布局布线的
# ExtraTimingOpt 把这点余量抠出来。想要复现默认结果就 IMPL_DIRECTIVE=Default。
if {[info exists ::env(IMPL_DIRECTIVE)] && $::env(IMPL_DIRECTIVE) ne ""} {
  set impl_dir $::env(IMPL_DIRECTIVE)
  puts "### impl directive = $impl_dir"
  set_property STEPS.PLACE_DESIGN.ARGS.DIRECTIVE $impl_dir [get_runs impl_1]
  set_property STEPS.PHYS_OPT_DESIGN.IS_ENABLED true        [get_runs impl_1]
  set_property STEPS.PHYS_OPT_DESIGN.ARGS.DIRECTIVE Explore [get_runs impl_1]
  set_property STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE $impl_dir [get_runs impl_1]
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
puts "\
               ### RESULT"
puts "### TX_TARGET = $tx_target"
puts "### WNS_ns = $wns"
puts "### bit    = [file join $script_dir $out_name.bit]"
puts "### hwh    = $hwh_path"
puts "### DONE"
exit
