# 一键综合（C synthesis）—— 出资源占用表和 II（启动间隔）表
#
# 用法（在仓库根目录跑）：
#
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v0_baseline \
#     cmd /c "E:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat -f build\hls\run_synth.tcl"
#
# 要带上优化指令的版本，再加一个变量：
#
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v1_partition \
#     HLS_DIRECTIVES=build/hls/directives/v1_partition.tcl \
#     cmd /c "E:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat -f build\hls\run_synth.tcl"
#
# 干什么：
#   建工程 -> 挂核 -> 综合 -> 把报告拷到 build/hls/reports/<HLS_LABEL>/
#   再用 src/python/parse_hls_csynth.py 把所有版本的数拉成一张对比表。
#
# ---- 为什么优化指令走 Tcl 而不是写在 .cpp 里 ----
#   `#pragma HLS ...` 写在源码里，要对比 5 个版本就得复制 5 份 .cpp，
#   而且一旦手滑改到算法，前面测出的数全部作废。
#   Vitis HLS 支持在 Tcl 里用 set_directive_* 下同样的指令，效果完全一样，
#   源码可以一个字不动 —— 而这份源码的算法是已经验过对的（csim 相关系数 1.000000000）。
#   于是「对比优化前后的资源与吞吐」变成「同一份源码 + 5 个指令文件」，
#   每个版本都是可复现的，不是靠回滚手改。
#
# ---- 为什么时钟是 10 ns ----
#   10 ns = 100 MHz，就是 base overlay 里 AXI 从接口那圈的时钟。
#   核将来就挂在这个时钟域上，按它综合出来的资源数才是能上板的那组数。

proc unixify {p} {
    return [string map {\\ /} $p]
}

# 用来在目录名和报告里标识"这是哪一版"。没给就退到一个显眼的名字。
if {[info exists ::env(HLS_LABEL)] && [string length $::env(HLS_LABEL)] > 0} {
    set label $::env(HLS_LABEL)
} else {
    set label "unlabeled"
}

set script_dir [file normalize [file dirname [info script]]]
set repo_dir   [file normalize [file join $script_dir .. ..]]

set src_file [file join $repo_dir src hls fir_multiband.cpp]
set tb_file  [file join $repo_dir src hls fir_tb.cpp]

# 优化指令文件是可选的，路径按仓库根目录的相对路径给。
set has_directives 0
if {[info exists ::env(HLS_DIRECTIVES)] && [string length $::env(HLS_DIRECTIVES)] > 0} {
    set directives_file [file normalize [file join $repo_dir $::env(HLS_DIRECTIVES)]]
    if {![file exists $directives_file]} {
        puts "ERROR: directives file not found: $directives_file"
        exit 1
    }
    set has_directives 1
}

# C 编译选项也是可选的，主要用来切 FIR_PARTIAL 这种结构开关。
# 例：HLS_CFLAGS=-DFIR_PARTIAL=1
set cflags ""
if {[info exists ::env(HLS_CFLAGS)]} {
    set cflags $::env(HLS_CFLAGS)
}

puts "==== C synthesis ===="
puts "label : $label"
puts "repo  : $repo_dir"
puts "src   : $src_file"
puts "cflags: $cflags"

# open_project 要的是**工程名**不是路径（传盘符/斜杠会报 [HLS 200-70]），
# 所以先切到 build/hls 再建工程，工程目录就是 build/hls/synth_proj。
cd $script_dir
open_project -reset synth_proj
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
    # set_directive_* 必须在下完指令之后再 csynth_design，所以这里 source。
    source $directives_file
} else {
    puts "==== no directives (baseline) ===="
}

csynth_design

# ---- 把报告拷出来存档 ----
# 每版必须分开存，否则下一版一 reset 就把上一版的数冲掉了 ——
# 而报告要的正是"优化前后对比"，没有旧版就等于没有对比。
set report_src [file join $script_dir synth_proj solution1 syn report]
set report_dst [file join $script_dir reports $label]
file mkdir $report_dst

set n_copied 0
foreach f [glob -nocomplain [file join $report_src *.rpt]] {
    file copy -force $f $report_dst
    incr n_copied
}
puts "==== reports copied: $n_copied -> $report_dst ===="

close_project
puts "==== C synthesis finished: $label ===="
exit
