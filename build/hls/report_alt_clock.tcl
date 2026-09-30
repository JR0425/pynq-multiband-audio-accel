# 用**另一个时钟频率**给已经布好线的设计重新算时序 —— 不重新布局布线。
#
# 为什么要有这个脚本：
#   布局布线一次十几分钟，而「换时钟频率」只改时序的要求时间，不改变布线，
#   在布线后的 checkpoint 上重算就够（几秒钟）。
#   什么时候需要：设计在 100 MHz 下时序不达标，想知道「降到多少 MHz 就够」。
#   这时候要的是**同一份布线在不同周期下的 slack**，反复重跑布局布线既慢、
#   又会因为布线结果的随机性让几个数不可比。
#
# 注意 slack 不是简单地随周期线性平移：时钟不确定性、偏斜、布线延迟都不变，
# 但要求时间变了，所以必须重算而不是用公式估。
#
# 用法（在仓库根目录）：
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v15_sub_n193_mul64 HLS_CLK_MHZ=80 \
#     cmd /c "E:\Xilinx\Vivado\2020.2\bin\vivado.bat -notrace -mode batch \
#             -source build\hls\report_alt_clock.tcl"
#
#   多个频率用逗号隔开：HLS_CLK_MHZ=100,90,80,75,66.7
#
# 产物：reports/<label>_impl/timing_impl_clk<N>MHz.rpt
#       （N 里的点会自动换成 p，例如 66.7 → 66p7）

set script_dir [file normalize [file dirname [info script]]]
set repo_dir   [file normalize [file join $script_dir .. ..]]

if {![info exists ::env(HLS_LABEL)] || [string length $::env(HLS_LABEL)] == 0} {
    puts "ERROR: 需要 HLS_LABEL"
    exit 1
}
if {![info exists ::env(HLS_CLK_MHZ)] || [string length $::env(HLS_CLK_MHZ)] == 0} {
    puts "ERROR: 需要 HLS_CLK_MHZ（例如 80，或 100,90,80）"
    exit 1
}

set label   $::env(HLS_LABEL)
set rpt_dir [file join $repo_dir build hls reports ${label}_impl]
set dcp     [file join $rpt_dir post_route.dcp]

if {![file exists $dcp]} {
    puts "ERROR: 找不到布线后的 checkpoint：$dcp"
    puts "       先用 run_vivado_impl.tcl 跑一遍综合+布局布线。"
    exit 1
}

puts "==== 打开 $dcp ===="
open_checkpoint $dcp

# 改周期用 `create_clock` **同名再建一次**就行 —— Vivado 会覆盖，只给一条
# WARNING: [Constraints 18-619] A clock with name 'ap_clk' already exists, overwriting ...
# **不要用 `delete_clock`**：从 checkpoint 恢复出来的时钟删不掉，会报
#     ERROR: [Vivado 12-811] Timing Results 'ap_clk' of type 'clock_network' were not found.
# 加 `-quiet` 则不报错、**但时钟照样还在**（实测：catch 拿到"成功"，`get_clocks`
# 里 ap_clk 一个没少）—— 那种"看起来做了、其实没做"最容易把人带沟里。
# `set_property PERIOD` 也不行，PERIOD 是只读属性。
foreach mhz_str [split $::env(HLS_CLK_MHZ) ,] {
    set mhz [string trim $mhz_str]
    if {[string length $mhz] == 0} { continue }
    set period [expr {1000.0 / double($mhz)}]
    set tag [string map {. p} $mhz]
    set out_rpt [file join $rpt_dir timing_impl_clk${tag}MHz.rpt]

    puts "==== $mhz MHz（周期 $period ns）-> $out_rpt ===="
    create_clock -period $period -name ap_clk [get_ports ap_clk]
    puts "     当前周期 = [get_property PERIOD [get_clocks ap_clk]] ns"
    report_timing_summary -delay_type max -max_paths 10 -file $out_rpt
    # 报告里也要能看出这一份对应哪个频率，不然过几天就分不清了
    set fh [open $out_rpt a]
    puts $fh ""
    puts $fh "# 本报告由 build/hls/report_alt_clock.tcl 生成："
    puts $fh "# 打开布线后的 checkpoint，把 ap_clk 周期改成 $period ns（$mhz MHz）后重算。"
    puts $fh "# 布线与 timing_impl.rpt 那一份完全相同，只有要求时间不同。"
    close $fh
}

puts "==== done: $label ===="
exit
