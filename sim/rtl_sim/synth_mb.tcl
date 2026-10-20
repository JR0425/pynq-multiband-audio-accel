# fir_mb_fe 的 out-of-context 综合，只为出一张资源/时序表。
#
#   cd sim/rtl_sim
#   vivado -mode batch -source synth_mb.tcl -log synth_mb.log -journal synth_mb.jou
#
# 为什么 OOC 而不是走板级工程：
#   板级工程里 PS7 + AXI 会把资源数字搅浑，看不出前端自己花了多少。
#   OOC 只综合这一个模块、不插 IO buffer，得到的 DSP/LUT/FF 就是这个前端的。
#
# 为什么要 cd 到 build/rtl_synth 再综合：
#   $readmemh 用的是固定文件名（路径不上命令行的原因见 src/rtl/fir_lp.v），
#   综合器按进程当前目录找这几个 .mem，所以先把它们复制进去。
#
# 时钟 10 ns = 100 MHz，和 HLS 那版的目标频率一致，两边才能放在一张表里比。

set script_dir [file dirname [file normalize [info script]]]
set root       [file normalize [file join $script_dir ../..]]
set work       [file join $root build rtl_synth]
set out        [file join $root data results]

file mkdir $work
cd $work

foreach n {coeffs_lp500 coeffs_lp1000 coeffs_lp2000} {
    file copy -force [file join $root src rtl ${n}_n193.mem] [file join $work $n.mem]
}

read_verilog [file join $root src rtl fir_mb_fe.v]

synth_design -top fir_mb_fe -part xc7z020clg400-1 -mode out_of_context -flatten_hierarchy full

# 时钟必须**在 synth_design 之后**建：非工程模式下 create_clock 要一个
# 已经打开的设计，放前面报 "No open design"。
create_clock -period 10.000 -name clk [get_ports clk]

report_utilization -file [file join $out rtl_fe_util.rpt]
report_timing_summary -delay_type max -max_paths 10 \
    -file [file join $out rtl_fe_timing.rpt]

set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -delay_type max]]
set dsps [get_cells -hier -filter {REF_NAME =~ DSP48*}]
set ff   [get_cells -hier -filter {PRIMITIVE_GROUP == FLOP_LATCH}]
set lut  [get_cells -hier -filter {PRIMITIVE_GROUP == LUT}]
puts "### RESULT"
puts "### WNS_ns            = $wns"
puts "### DSP48_cells       = [llength $dsps]"
puts "### FF_cells          = [llength $ff]"
puts "### LUT_cells         = [llength $lut]"
puts "### cycles_per_sample = 3 (由相位机决定，仿真日志里可数)"

puts "### DONE"
