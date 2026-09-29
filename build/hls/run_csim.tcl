# 一键跑 C 仿真（不综合，只验算法）
#
# 用法：
#   MSYS_NO_PATHCONV=1 cmd /c "E:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat -f build\hls\run_csim.tcl"
#
# 干什么：建工程 -> 挂上核和测试台 -> csim_design
#         结果写到 data/results/hw_output.txt，再用
#         src/python/compare_golden_vs_hw.py 和 Python 黄金参考比对。
#
# 为什么路径要在这里算：csim 是在它自己临时建的目录里跑程序的，
# 相对路径到不了仓库根目录。所以这里算成绝对路径，用 -argv 传进去。

proc unixify {p} {
    # Tcl 在 Windows 上给出的是反斜杠路径，传给 csim 的 argv 前换成正斜杠
    return [string map {\\ /} $p]
}

set script_dir [file normalize [file dirname [info script]]]
set repo_dir   [file normalize [file join $script_dir .. ..]]

set src_file   [file join $repo_dir src hls fir_multiband.cpp]
set tb_file    [file join $repo_dir src hls fir_tb.cpp]
set in_file    [file join $repo_dir data audio test_input.txt]
set out_file   [file join $repo_dir data results hw_output.txt]

# 输出文件可以被 HLS_OUT 覆盖（相对仓库根），用于位宽扫描时每个组合存一份输出。
# 例：HLS_OUT=data/results/fixed_dw16_cw18.txt
if {[info exists ::env(HLS_OUT)] && [string length $::env(HLS_OUT)] > 0} {
    set out_file [file join $repo_dir $::env(HLS_OUT)]
}

# C 编译选项（可选）—— 用来切 FIR_PARTIAL / FIR_FIXED 这类开关。
# 例：HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18"
set cflags ""
if {[info exists ::env(HLS_CFLAGS)]} {
    set cflags $::env(HLS_CFLAGS)
}

puts "==== C simulation only ===="
puts "repo : $repo_dir"
puts "input: $in_file"
puts "out  : $out_file"
puts "cflags: $cflags"

# open_project 要的是**工程名**，不是路径 —— 传带盘符或斜杠的路径进去会报
# [HLS 200-70] contains illegal character ':' / '/'。所以先切到该目录再建工程，
# 工程目录仍然是 build/hls/csim_proj。
cd $script_dir
open_project -reset csim_proj
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

csim_design -argv "[unixify $in_file] [unixify $out_file]"

puts "==== C simulation finished ===="
exit
