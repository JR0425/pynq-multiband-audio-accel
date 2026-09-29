# 给**已经布好线**的设计补一份「带 I/O 假设」的时序报告。
#
# 为什么不直接在 run_vivado_impl.tcl 里出：
#   那个脚本要跑综合 + 布局布线，一次十几分钟。而已有的设计都留了
#   `reports/<label>_impl/post_route.dcp`，打开它加个约束重算时序就行，
#   两三分钟出结果。**加 input/output delay 不改变布线**，只改要求时间，
#   所以在布线后的 checkpoint 上加是等价的。
#
# 为什么需要这份报告：
#   run_vivado_impl.tcl 出的 timing_impl.rpt **只量了寄存器到寄存器**。
#   顶层只有 10 个端口（`in_r[31:0]`、`out_r[31:0]`、`length_r[31:0]`、
#   ap_clk/ap_rst/ap_start/ap_done/ap_idle/ap_ready/out_r_ap_vld），
#   从管脚到第一个寄存器那一段没有约束，check_timing 里报的
#   `no_input_delay (66)` / `no_output_delay (36)` 就是这件事
#   （66 = in_r 32 + length_r 32 + ap_start + ap_rst；36 = out_r 32 + 4 个握手位）。
#   真上板时外面会套一层壳（AXI-Stream 之类），那一段有真实延迟，
#   所以裸核的 WNS 不能直接当作整机能跑 100 MHz。
#
# 用法（在仓库根目录）：
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v11_fixed_nosat_mul5 HLS_IO_DELAY=1.5 \
#     cmd /c "E:\Xilinx\Vivado\2020.2\bin\vivado.bat -notrace -mode batch \
#             -source build\hls\report_io_timing.tcl"
#
# HLS_IO_DELAY 是要给的值，单位 ns。**这个数不该由脚本猜** —— 它等于
# 「上一级寄存器出来、走到我这根管脚花了多久」，取决于外面套什么壳。
# 现在还没有壳，所以调用方必须显式传，报告文件名里也会带上这个值。

set script_dir [file normalize [file dirname [info script]]]
set repo_dir   [file normalize [file join $script_dir .. ..]]

if {![info exists ::env(HLS_LABEL)] || [string length $::env(HLS_LABEL)] == 0} {
    puts "ERROR: 需要 HLS_LABEL"
    exit 1
}
if {![info exists ::env(HLS_IO_DELAY)] || [string length $::env(HLS_IO_DELAY)] == 0} {
    puts "ERROR: 需要 HLS_IO_DELAY（单位 ns，例如 1.5）"
    exit 1
}

set label $::env(HLS_LABEL)
set iod   $::env(HLS_IO_DELAY)
set rpt_dir [file join $repo_dir build hls reports ${label}_impl]
set dcp     [file join $rpt_dir post_route.dcp]

if {![file exists $dcp]} {
    puts "ERROR: 找不到布线后的 checkpoint：$dcp"
    puts "       先用 run_vivado_impl.tcl 跑一遍综合+布局布线。"
    exit 1
}

puts "==== 打开 $dcp ===="
open_checkpoint $dcp

# ap_clk 自己不能给 input_delay；ap_rst 是异步复位、不在数据路径上，也排除。
# **不要用 `remove_from_collection`** —— 那是工程模式的命令，非工程模式下（这里就是）
# 会报 `invalid command name "remove_from_collection"`，而且**退出码还是 0**，
# 不留神会把"没出报告"当成"报告没问题"。直接点名端口最稳，反正顶层就这 10 个。
set_input_delay  -clock ap_clk $iod [get_ports {in_r length_r ap_start}]
set_output_delay -clock ap_clk $iod [get_ports {out_r ap_done ap_idle ap_ready out_r_ap_vld}]

set out_rpt [file join $rpt_dir timing_impl_io_${iod}ns.rpt]
puts "==== 出报告（I/O 假设 ${iod} ns）：$out_rpt ===="
report_timing_summary -delay_type max -max_paths 10 -file $out_rpt
report_timing -delay_type max -max_paths 5 -path_type full_clock_expanded \
    -file [file join $rpt_dir timing_impl_io_${iod}ns_worst_paths.rpt]

exit
