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
# 直通检验的输出（压缩比设成 1.0 跑的那一遍）—— 见 src/hls/fir_tb.cpp 文件头
set tr_file    [file join $repo_dir data results hw_transparent_i16.txt]

# 输出文件可以被 HLS_OUT 覆盖（相对仓库根），用于位宽扫描时每个组合存一份输出。
# 例：HLS_OUT=data/results/fixed_dw16_cw18.txt
if {[info exists ::env(HLS_OUT)] && [string length $::env(HLS_OUT)] > 0} {
    set out_file [file join $repo_dir $::env(HLS_OUT)]
}

# 直通检验这一路同理：**每跑一次就被覆盖**。做位宽扫描时必须跟 HLS_OUT 一起指定，
# 否则扫完一轮，data/results/hw_transparent_i16.txt 里剩下的是**最后一档**的直通输出，
# 而文件名一点没变 —— 文件在、数看着也在，只是已经不是选定配置那一份了。
# 例：HLS_TR=data/results/fixed_dw16_cw18_transparent.txt
if {[info exists ::env(HLS_TR)] && [string length $::env(HLS_TR)] > 0} {
    set tr_file [file join $repo_dir $::env(HLS_TR)]
}

# C 编译选项（可选）—— 用来切 FIR_PARTIAL / FIR_FIXED / FIR_N_TAPS 这类开关。
# 例：HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18 -DFIR_N_TAPS=193"
#
# ⚠️ 这些开关**必须同时加给测试台**，不能只加给核。
#    测试台自己也 include 了 fir_coeffs.h，靠它拿 N_TAPS / N_BANDS / FIR_GROUP_DELAY。
#    只给核加的话两边编译出的抽头数就不一样了：核按 193 跑，测试台却以为 D = 32
#    （65 抽头的默认值），而它的位移搜索范围是 2D+8 = 72，**够不到真正的延迟 96**，
#    于是印出 "best lag = 14, mismatches = 927/928" —— 看着像结构坏了，
#    其实只是测试台把延迟数错了。实测就是这么翻的车。
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
    add_files -tb $tb_file -cflags $cflags
} else {
    add_files $src_file
    add_files -tb $tb_file
}

open_solution -reset "solution1" -flow_target vivado
set_part xc7z020clg400-1
create_clock -period 10 -name default

csim_design -argv "[unixify $in_file] [unixify $out_file] [unixify $tr_file]"

puts "==== C simulation finished ===="
exit
