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

if {[llength [glob -nocomplain [file join $ip_dir subcore *.tcl]]] == 0} {
    puts "ERROR: 没找到 IP 的子核 tcl：$ip_dir/subcore"
    puts "       先让 HLS 跑一次 export_design（会在打包那步失败，但文件都生成好了）。"
    exit 1
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
# `create_ip` 要有一个打开的工程（非工程模式下直接调会报 "No open project"）。
# 开一个**内存工程**当落脚点：不落盘、不建 fileset，只为把 IP 生成出来。
create_project -in_memory -part xc7z020clg400-1

foreach t [glob -nocomplain [file join $sol_dir impl ip subcore *.tcl]] {
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

# HLS 生成的 RTL（跳过 *_ip.tcl，那是 IP 描述不是 RTL）
foreach f [glob -nocomplain [file join $rtl_dir *.v]] {
    read_verilog $f
}

# ---------- 综合 ----------
puts "==== synth_design ===="
# PerformanceOptimized：默认那一档在这种扁平 RTL 上调度很差，
# 实测最差路径 12.9 ns 里有 7.7 ns 是走线，逻辑只有 7 级 —— 不是逻辑深，是布得散。
synth_design -top fir_multiband -part xc7z020clg400-1 -directive PerformanceOptimized

# 时序约束：目标 100 MHz（10 ns），和 HLS 里 `create_clock -period 10` 是同一条。
create_clock -period 10.000 -name ap_clk [get_ports ap_clk]

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
