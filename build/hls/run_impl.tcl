# 跑完整实现流程（export_design -flow impl）—— 把 csynth 的估算值换成实测值
#
# 为什么单独一个脚本：
#   `csynth_design` 给的资源数是**估算** —— HLS 按调度结果推算"这些运算大概要多少 LUT/DSP"。
#   真正能上板的数是**布局布线之后**的。两者能差多少，没人能预先说准。
#   这个脚本在综合之后接着调 Vivado 跑 RTL 综合 + 实现，把实测报告留出来。
#
# 用法（在仓库根目录）：
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v11_fixed_nosat_mul5 \
#     HLS_DIRECTIVES=build/hls/directives/v11_fixed_nosat_mul5.tcl \
#     HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18 -DFIR_ACC_SAT=0" \
#     cmd /c "E:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat -f build\hls\run_impl.tcl"
#
# 耗时：比 csynth 长得多（RTL 综合 + 布局布线），几十分钟量级。
# 产物：build/hls/impl_proj/solution1/impl/report/ 下的实测报告
#       （不 reset 的话会一直留着，可以慢慢看）

proc unixify {p} {
    return [string map {\\ /} $p]
}

set script_dir [file normalize [file dirname [info script]]]
set repo_dir   [file normalize [file join $script_dir .. ..]]

set src_file [file join $repo_dir src hls fir_multiband.cpp]
set tb_file  [file join $repo_dir src hls fir_tb.cpp]

if {[info exists ::env(HLS_LABEL)] && [string length $::env(HLS_LABEL)] > 0} {
    set label $::env(HLS_LABEL)
} else {
    set label "impl_unlabeled"
}

set has_directives 0
if {[info exists ::env(HLS_DIRECTIVES)] && [string length $::env(HLS_DIRECTIVES)] > 0} {
    set directives_file [file normalize [file join $repo_dir $::env(HLS_DIRECTIVES)]]
    if {![file exists $directives_file]} {
        puts "ERROR: directives file not found: $directives_file"
        exit 1
    }
    set has_directives 1
}

set cflags ""
if {[info exists ::env(HLS_CFLAGS)]} {
    set cflags $::env(HLS_CFLAGS)
}

puts "==== C synthesis + implementation ===="
puts "label : $label"
puts "cflags: $cflags"

# 单独一个工程目录，别把 run_synth.tcl 那个 reset 掉
cd $script_dir
open_project -reset impl_proj
set_top fir_multiband
if {[string length $cflags] > 0} {
    add_files $src_file -cflags $cflags
} else {
    add_files $src_file
}
add_files -tb $tb_file

open_solution -reset "solution1" -flow_target vivado
set_part xc7z020clg400-1
create_clock -period 10 -name default

if {$has_directives} {
    puts "==== directives: $directives_file ===="
    source $directives_file
} else {
    puts "==== no directives (baseline) ===="
}

csynth_design

# 这一步才是把估算换成实测：调 Vivado 跑 RTL 综合 + 布局布线
#
# ⚠️ format 用 syn_dcp 而不是默认的 ip_catalog —— 后者在 2022 年之后必挂：
#     IP 打包那一步会设 `core_revision`，值是 YYMMDDHHMM 格式的日期戳
#     （2026-09-29 19:27 → 2609291927），**超过 32 位有符号整数上限 2147483647**，
#     于是 Vivado 报 `bad lexical cast: source type value could not be interpreted
#     as target` / `ERROR: [IMPL 213-28] Failed to generate IP.`。
#     实测 2026-09-29：整个 export 只跑 16 秒就崩，**实现压根没开始**。
#     这是 Vivado 2020.2 的工具 bug，不是设计的问题。
#     syn_dcp 只导综合后的 DCP，不走 IP 打包，因此绕得过去。
puts "==== export_design -flow impl （这一步很慢） ===="
export_design -flow impl -rtl verilog -format syn_dcp

puts "==== implementation finished: $label ===="
puts "报告位置：build/hls/impl_proj/solution1/impl/report/"
exit
