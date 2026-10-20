# fir_mb_fe 的 OOC 实现（综合 + 布局 + 布线），出的是**真实布线后**的时序。
#
#   cd sim/rtl_sim
#   vivado -mode batch -source impl_mb.tcl -log impl_mb.log -journal impl_mb.jou
#
# 为什么还要这一份：
#   synth_mb.tcl 只到 synthesis，布线长度是综合器估的，通常偏乐观。
#   WNS 在综合后是 +3.2 ns，布完还能剩多少，得让 place & route 说了算。
#   两份报告都留着：综合那份和 HLS 的 OOC 数字同级，
#   布线这份是"这个东西真能跑到 100 MHz"的证据。
#
# 时钟 10 ns、器件、OOC 方式和 synth_mb.tcl 完全一致，两边可比。

set script_dir [file dirname [file normalize [info script]]]
set root       [file normalize [file join $script_dir ../..]]
set work       [file join $root build rtl_impl]
set out        [file join $root data results]

file mkdir $work
cd $work

foreach n {coeffs_lp500 coeffs_lp1000 coeffs_lp2000} {
    file copy -force [file join $root src rtl ${n}_n193.mem] [file join $work $n.mem]
}

read_verilog [file join $root src rtl fir_mb_fe.v]

synth_design -top fir_mb_fe -part xc7z020clg400-1 -mode out_of_context -flatten_hierarchy full
create_clock -period 10.000 -name clk [get_ports clk]

opt_design -directive Explore
place_design -directive Explore
phys_opt_design
route_design -directive Explore
phys_opt_design

report_utilization      -file [file join $out rtl_fe_impl_util.rpt]
report_timing_summary -delay_type max -max_paths 10 \
                        -file [file join $out rtl_fe_impl_timing.rpt]

set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -delay_type max]]
set dsps [get_cells -hier -filter {REF_NAME =~ DSP48*}]
puts "### RESULT"
puts "### WNS_ns  = $wns"
puts "### DSP48_cells = [llength $dsps]"
puts "### DONE"
