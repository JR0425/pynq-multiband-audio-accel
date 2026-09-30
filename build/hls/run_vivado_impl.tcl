# 拿 HLS 生成的 RTL 直接跑 Vivado 综合 + 布局布线 —— 目的是要**实测**资源数和时序
#
# ============================ 为什么不用 HLS 的 export_design ============================
# `export_design -flow impl` 第一步是打包 IP，那一步会 `set_property core_revision`
# 一个 YYMMDDHHMM 形式的日期戳（2026-09-29 19:29 → 2609291929）。
# 这个数**超过 32 位有符号整数上限 2147483647**（`ap_decl.h` 那边的老约束），
# 于是 Vivado 抛
#     bad lexical cast: source type value could not be interpreted as target
#     ERROR: [IMPL 213-28] Failed to generate IP.
# 然后整条实现流程一步都没跑就退了。换 `-format syn_dcp` 也一样 ——
# 打包 IP 是无条件先跑的，跟导出格式无关。
# 这是 Vivado 2020.2 的 bug：任何 2022 年之后的日期都会溢出。跟设计本身无关。
#
# ============================ 为什么用非工程模式 ============================
# 先用工程模式（create_project + add_files + launch_runs）试过，能跑通、能出报告，
# 但一加时序约束就崩：把 XDC 挂进 constrs_1 之后，Vivado 抛
#     can not find channel named "stdout"
# 然后整个脚本在下一个 `puts` 上死掉。fileset 这套东西在这个版本上太脆。
# 非工程模式没有 fileset、没有工程目录，所有步骤显式顺序执行，反而干净。
#
# ============================ 两个踩过的坑 ============================
# 1. **必须自己写 `create_clock`。** 不写的话 Vivado 不知道该约束哪个时钟 ——
#    综合照样跑完、报告照样生成，但时序那一栏全是空的（日志里会出现
#    "N register/latch pins with no clock"）。**空报告看起来像「没问题」，
#    其实什么都没量。** 非工程模式下设计是打开的，所以 create_clock 可以直接调。
# 2. **系数 ROM 靠 `$readmemh "./...dat"` 读**，所以综合前必须把工作目录切到
#    放着那个 .dat 的地方，否则综合出一张空 ROM 而且只是 INFO 级提示，很容易漏。
#
# 用法（在仓库根目录）：
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v11_fixed_nosat_mul5 \
#     cmd /c "E:\Xilinx\Vivado\2020.2\bin\vivado.bat -notrace -mode batch -source build\hls\run_vivado_impl.tcl"
#
#   量别的版本时用 HLS_SOL 指到另一份 solution 目录（HLS 每次综合都会覆盖
#   impl_proj，所以要先 `cp -r impl_proj/solution1 <别处>` 把 RTL 存下来）。
#
#   **定点版（`-DFIR_FIXED=1`）走这条路是必选，不是备选** —— 它没有 Xilinx IP，
#   不受上面那个 Y2K22 影响（export_design 照样在打包那步失败，但 RTL 已经生成好了）。
#
# 产物：build/hls/reports/<label>_impl/ 下的实测报告 + 布线后的 checkpoint

set script_dir [file normalize [file dirname [info script]]]
set repo_dir   [file normalize [file join $script_dir .. ..]]

set label "v11_fixed_nosat_mul5"
if {[info exists ::env(HLS_LABEL)] && [string length $::env(HLS_LABEL)] > 0} {
    set label $::env(HLS_LABEL)
}

set sol_dir [file join $repo_dir build hls impl_proj solution1]
if {[info exists ::env(HLS_SOL)] && [string length $::env(HLS_SOL)] > 0} {
    set sol_dir [file normalize [file join $repo_dir $::env(HLS_SOL)]]
}

set rtl_dir  [file join $sol_dir syn verilog]
set ip_dir   [file join $sol_dir impl ip]
set work_dir [file join $repo_dir build hls vivado_work $label]
set rpt_dir  [file join $repo_dir build hls reports ${label}_impl]

# 子核 tcl 只有**浮点版**才有（HLS 会给每个 floating_point IP 打包一个）。
# 定点版（`-DFIR_FIXED=1`）乘加全落在 DSP48 上，一个 Xilinx IP 都不生成，
# `subcore` 是空目录 —— 这时候**没有 IP 要实例化**，不是出错。
# 所以这里不能硬报错：判据改成"有 tcl 才 source"，没有就跳过下面整段 IP 处理。
set subcore_tcls [glob -nocomplain [file join $ip_dir subcore *.tcl]]
if {[llength $subcore_tcls] == 0} {
    puts "NOTE: $ip_dir/subcore 是空的 —— 本版没有 Xilinx IP 子核（定点版正常如此），跳过 IP 处理。"
}

# 工作目录每次从零开始。**别复用** —— 里面留着上一次跑出来的
# `.srcs/.../ip/<名字>/` 时，`create_ip` 发现目录重名会给新 IP 加 `_1` 后缀
# （实测见过 `FAcc` 和 `FAcc_1` 同时存在）。目录是脚本自己生成的中间产物，
# 清掉不影响任何东西；留下来的代价是同一个模块两份定义。
if {[file exists $work_dir]} { file delete -force $work_dir }
file mkdir $work_dir
file mkdir $rpt_dir

# 系数 ROM 是 `$readmemh "./....dat"` 读的，所以综合前工作目录里必须有那个 .dat。
# **文件名别写死** —— 浮点版叫 `..._FIR_COEFFS_rom.dat`，定点版叫
# `..._FIR_COEFFS_V_rom.dat`（模块名不同所以文件名不同）。一律拷过去最省事。
foreach d [glob -nocomplain [file join $rtl_dir *.dat]] {
    file copy -force $d $work_dir
}
cd $work_dir

puts "==== Vivado implementation on HLS-generated RTL ===="
puts "label  : $label"
puts "rtl    : $rtl_dir"
puts "work   : $work_dir"
puts "report : $rpt_dir"

# ---- IP 处理：这一步折腾了很久，记一下结论 ----
# HLS 生成的 RTL 里那些 `fpext` / `fadd` / `fmul` / `FAcc` 都是 Xilinx 自带的
# floating_point IP（v7.1）。理论上让 Vivado 走正常 IP 机制就行，实测走不通：
#   · `create_ip` + `source subcore/*.tcl`：v11 只有一个 IP 时能认出来；
#     浮点版有四个时，`FAcc` 报 `module 'FAcc' not found`（另外三个正常）。
#   · `read_ip` 直接读打包时生成的 xci：同样是 `FAcc` 找不到。
# 而 `.gen/.../FAcc/synth/FAcc.v` 里明明写着 `module FAcc (...)`，文件是好的，
# 纯粹是没被纳入综合。
# **所以这里不依赖 Vivado 的 IP 机制，直接把它生成出来的 HDL 读进来。**
# 代价是可能和工程里的 IP 定义重名（Vivado 会告警，通常无害）。
if {[llength $subcore_tcls] > 0} {
    # `create_ip` 要有一个打开的工程（非工程模式下直接调会报 "No open project"）。
    # 开一个**内存工程**当落脚点：不落盘、不建 fileset，只为把 IP 生成出来。
    create_project -in_memory -part xc7z020clg400-1

    foreach t $subcore_tcls {
        source $t
    }
    generate_target synthesis [get_ips]

    # IP 生成的 HDL **必须手动读进来**。
    # `create_ip` 已经把每个 IP 作为 sub-design（.xci）挂进工程了，但非工程模式下
    # `synth_design` 不会顺着 .xci 去链它生成的 HDL —— 实测删掉这段就报
    #     ERROR: [Synth 8-439] module 'FAcc' not found
    # 所以这段不是冗余，是必需的。
    #
    # 代价是这样会跟 .xci 里那份定义撞名（Vivado 报
    #     CRITICAL WARNING: [Synth 8-2490] overwriting previous definition of module ...
    # 但两份内容同源、参数相同，实测无害：v11 用这份脚本跑出的资源数和时序，
    # 跟完全不用这份脚本的项目模式流程逐位相同。**只要每种 IP 只有一份**。
    # 多份的来源是工作目录残留（见上面 file delete 那段）。
    set ip_gen_root [file join $work_dir .gen sources_1 ip]
    foreach d [glob -nocomplain [file join $ip_gen_root *]] {
        # 先 hdl（IP 真正的实现），后 synth（包在外面的那层壳）
        foreach f [lsort [glob -nocomplain [file join $d hdl *.v]]]   { read_verilog $f }
        foreach f [lsort [glob -nocomplain [file join $d synth *.v]]] { read_verilog $f }
    }
}

# HLS 生成的 RTL（跳过 *_ip.tcl，那是 IP 描述不是 RTL）
foreach f [glob -nocomplain [file join $rtl_dir *.v]] {
    read_verilog $f
}

# ---------- 综合 ----------
puts "==== synth_design ===="
# PerformanceOptimized：默认那一档在这种扁平 RTL 上调度很差，
# 实测最差路径 12.9 ns 里有 7.7 ns 是走线，逻辑只有 7 级 —— 不是逻辑深，是布得散。
#
# -mode out_of_context（OOC）—— **这一条是必需的，不是优化**：
#   普通模式下 Vivado 把顶层端口当"真管脚"，布局第一步就得给它们找物理位置。
#   而本核的顶层有 185 个端口位（in_r 32 + out_r 32 + length_r 32 + 两个
#   DRC 参数数组的存储器接口 80 + 握手位），clg400 封装**总共只有 125 个可用管脚位**，
#   于是 place_design 在第一步就退：
#       ERROR: [Place 30-58] IO placement is infeasible.
#       Number of unplaced terminals (178) is greater than number of available sites (125).
#   这不是设计的问题 —— 核将来是**被实例化进 overlay 的**（参数走 AXI-Lite 寄存器、
#   数据走 DMA），从来没有"核自己是一颗芯片"这个用法。
#   OOC 模式不插 I/O 缓冲，顶层端口不是物理对象，布局就不需要给它们找位置。
#   这正是测一颗 IP 核该用的模式。
#
#   代价：v10 / v11 那两行实测是在普通模式下量的，和 OOC 的数不能逐位对照
#   （I/O 缓冲那一小块没了，其余逻辑相同）。要复现它们就 HLS_OOC=0。
set ooc "-mode out_of_context"
if {[info exists ::env(HLS_OOC)] && $::env(HLS_OOC) == "0"} {
    puts "NOTE: HLS_OOC=0 —— 按普通模式综合（顶层端口要占真管脚，端口多的版本会布局失败）"
    set ooc ""
}
synth_design -top fir_multiband -part xc7z020clg400-1 -directive PerformanceOptimized {*}$ooc

# 时序约束：默认目标 100 MHz（10 ns），和 HLS 里 `create_clock -period 10` 是同一条。
#
# 可以用 HLS_CLK_MHZ 改成别的频率。**为什么需要**：这个核是整条音频通路里唯一的
# 运算单元，它的时序只跟自己有关；时钟降下来不动功能，只是每块多花几微秒
# （48 kHz 下每采样有 1/f s 的预算，余量本来就很大）。
# 什么时候用：100 MHz 下差一点点过不去时，先看**降频**是不是比"加硬件"更划算。
# 注意降频必须重新布局布线（不是重算报告）—— 周期变了 Vivado 会布出不同的结果，
# 拿 100 MHz 的布线去算 80 MHz 的 slack 只是个估计。想省时间就用
# `report_alt_clock.tcl` 先估，确认有戏再真跑一遍。
set clk_mhz 100
if {[info exists ::env(HLS_CLK_MHZ)] && [string length $::env(HLS_CLK_MHZ)] > 0} {
    set clk_mhz $::env(HLS_CLK_MHZ)
}
set clk_period [expr {1000.0 / double($clk_mhz)}]
puts "==== 时钟目标：$clk_mhz MHz（周期 $clk_period ns）===="
create_clock -period $clk_period -name ap_clk [get_ports ap_clk]

write_checkpoint -force [file join $rpt_dir post_synth.dcp]
report_utilization           -file [file join $rpt_dir utilization_synth.rpt]
report_utilization -hierarchical -file [file join $rpt_dir utilization_synth_hier.rpt]
report_timing_summary -delay_type max -max_paths 10 \
                             -file [file join $rpt_dir timing_synth.rpt]

# ---------- 布局布线 ----------
puts "==== opt / place / phys_opt / route（这一步最慢） ===="
opt_design
place_design -directive ExtraTimingOpt
phys_opt_design -directive AggressiveExplore
route_design -directive MoreGlobalIterations
phys_opt_design -directive AggressiveExplore

write_checkpoint -force [file join $rpt_dir post_route.dcp]
report_utilization              -file [file join $rpt_dir utilization_impl.rpt]
report_utilization -hierarchical -file [file join $rpt_dir utilization_impl_hier.rpt]
report_timing_summary -delay_type max -max_paths 10 \
                                -file [file join $rpt_dir timing_impl.rpt]
report_timing -delay_type max -max_paths 5 -path_type full_clock_expanded \
                                -file [file join $rpt_dir timing_impl_worst_paths.rpt]

# ---------- 带 I/O 假设的时序报告（可选，设 HLS_IO_DELAY 才跑） ----------
# 上面那几份报告**只量了寄存器到寄存器**。check_timing 里那两行
#     checking no_input_delay (66)
#     checking no_output_delay (36)
# 就是在说：66 个输入位、36 个输出位（`in_r[31:0]` + `length_r[31:0]` + `ap_start` + `ap_rst`
# 恰好 66；`out_r[31:0]` + 4 个握手位恰好 36）从管脚到第一个寄存器那一段**没被约束**。
# 所以那份报告里的 WNS 不能直接当成"这个设计在板子上能跑 100 MHz"。
#
# `set_input_delay` 要给一个"上一级寄存器出来到我这根管脚花多久"的数 ——
# 真上板时这个数取决于外面套的壳（AXI-Stream FIFO 还是别的），现在还没有壳。
# 所以这里**不猜**，由调用方通过 HLS_IO_DELAY 传进来，报告文件名里带上这个假设值。
# 片内寄存器到寄存器的经验值取 1.5 ns（时钟到输出 + 走线）。
# 布线已经完成，加约束只是重算要求时间、不重布线，所以不额外花时间。
if {[info exists ::env(HLS_IO_DELAY)] && [string length $::env(HLS_IO_DELAY)] > 0} {
    set iod $::env(HLS_IO_DELAY)
    puts "==== 加 I/O 假设 ${iod} ns，出第二份时序报告 ===="
    # ap_clk 本身不能给 input_delay；ap_rst 是异步复位、不是数据路径，也排除。
    # **不要用 `remove_from_collection`** —— 那是工程模式的命令，非工程模式下会报
    # `invalid command name "remove_from_collection"`，而且**退出码还是 0**。
    # 直接点名端口最稳（顶层就 in_r / out_r / length_r + 几个握手位）。
    set_input_delay  -clock ap_clk $iod [get_ports {in_r length_r ap_start}]
    set_output_delay -clock ap_clk $iod [get_ports {out_r ap_done ap_idle ap_ready out_r_ap_vld}]
    report_timing_summary -delay_type max -max_paths 10 \
        -file [file join $rpt_dir timing_impl_io_${iod}ns.rpt]
    report_timing -delay_type max -max_paths 5 -path_type full_clock_expanded \
        -file [file join $rpt_dir timing_impl_io_${iod}ns_worst_paths.rpt]
}

puts "==== done: $label ===="
puts "报告在 $rpt_dir"
exit
