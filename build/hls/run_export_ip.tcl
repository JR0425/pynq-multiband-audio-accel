# run_export_ip.tcl —— 把带 AXI 外壳的核打包成「能挂进 block design 的 IP」
#
# 为什么不能直接跑 export_design：
#   `export_design -format ip_catalog` 在 2022 年之后**必挂**（Vivado 的 Y2K22 bug）。
#   挂的位置很靠后：生成出来的 run_ippack.tcl 里那条
#       set_property core_revision $Revision $core
#   会被喂一个 10 位数（YYMMDDHHMM，例：2026-09-29 20:00 → 2609292000），
#   **超过 32 位有符号整数上限 2147483647**，rdi 报
#       bad lexical cast: source type value could not be interpreted as target
#   然后 ERROR: [IMPL 213-28] Failed to generate IP.
#
#   关键是：**失败之前该生成的东西全都生成好了** ——
#   接口定义、RTL、xgui 模板、run_ippack.tcl 本体，一个不少，全在 impl/ip/ 里。
#   它只是在最后「写进 component.xml」那一步倒下的。
#
# 所以做法是三步：
#   ① 正常跑 export_design（预期它会挂，用 catch 接住，别让脚本整个退出）
#   ② 把生成出来的 run_ippack.tcl 里那行 Revision 改成一个小数字
#   ③ 重新跑打包（等价于那个目录里的 pack.bat），这次就过了
#
# 官方补丁（AMD 支持文章 76960 的 y2k22_patch-1.2.zip）要登录才能下，
# 但补丁干的事也就是这个 —— 我们自己做一遍。
#
# 用法（在仓库根目录）：
#   MSYS_NO_PATHCONV=1 HLS_LABEL=v18_axi_shell \
#     HLS_DIRECTIVES=build/hls/directives/v18_axi_shell.tcl \
#     HLS_CFLAGS="-DFIR_PARTIAL=5 -DFIR_FIXED=1 -DFIR_DW=16 -DFIR_CW=18 -DFIR_ACC_SAT=0 -DFIR_N_TAPS=193" \
#     cmd /c "E:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat -f build\hls\run_export_ip.tcl"
#
# 产物：
#   build/vivado/ip_repo/fir_multiband_v1_0/component.xml   ← block design 用它
#   build/hls/impl_proj/solution1/impl/ip/                  ← 原始打包目录（留档）

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
    set label "export_ip_unlabeled"
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

# Vivado 的路径：优先用环境变量，取不到就退回本机装的那个
if {[info exists ::env(XILINX_VIVADO)] && [string length $::env(XILINX_VIVADO)] > 0} {
    set vivado_bat [file join $::env(XILINX_VIVADO) bin vivado.bat]
} else {
    set vivado_bat "E:/Xilinx/Vivado/2020.2/bin/vivado.bat"
}
if {![file exists $vivado_bat]} {
    puts "ERROR: 找不到 vivado.bat：$vivado_bat"
    exit 1
}

puts "==== 打包 IP：$label ===="
puts "cflags: $cflags"

# 单独一个工程目录，别把 run_synth.tcl / run_impl.tcl 的工程覆盖掉
cd $script_dir
open_project -reset impl_ip_proj
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

# ---- ① 跑 export，预期会挂 ----
puts "==== ① export_design -format ip_catalog（预期在最后一步挂，正常现象）===="
set rc [catch {export_design -rtl verilog -format ip_catalog} err]
puts "export_design 返回码 $rc"
if {$rc != 0} {
    puts "（这就是 Y2K22 那个坑，下面把它绕过去）"
}

# ---- ② 改 Revision ----
set ip_dir [file join $script_dir impl_ip_proj solution1 impl ip]
set pack_tcl [file join $ip_dir run_ippack.tcl]

if {![file exists $pack_tcl]} {
    puts "\nERROR: 没找到 $pack_tcl"
    puts "说明 export_design 连打包脚本都没生成出来，得看它的完整输出。"
    exit 1
}

set fh [open $pack_tcl r]
set txt [read $fh]
close $fh

# 原始行长这样：  set Revision    "2609292000"
if {![regexp -line {^set Revision[^\n]*} $txt old_line]} {
    puts "\nERROR: run_ippack.tcl 里没找到 'set Revision' 那一行，格式可能变了。"
    puts "手工看一眼：$pack_tcl"
    exit 1
}
puts "② 改掉这一行：$old_line"
regsub -line {^set Revision[^\n]*} $txt {set Revision    "1"} txt

set fh [open $pack_tcl w]
puts -nonewline $fh $txt
close $fh

set fh [open $pack_tcl r]
gets $fh line1
close $fh
puts "   改完头一行是：$line1"

# ---- ③ 重跑打包 ----
puts "==== ③ 重新打包（就是重跑那个目录里的 pack.bat）===="
set old_cwd [pwd]
cd $ip_dir

# 关键寄存器/接口就藏在 component.xml 里，打完要看它存在
set comp_xml [file join $ip_dir component.xml]
file delete -force $comp_xml

set rc2 [catch {exec $vivado_bat -notrace -mode batch -source run_ippack.tcl} out]
if {$rc2 != 0} {
    puts "\n打包还是没过。手工执行这一句看完整输出："
    puts "  cd \"[unixify $ip_dir]\""
    puts "  E:\\Xilinx\\Vivado\\2020.2\\bin\\vivado.bat -notrace -mode batch -source run_ippack.tcl"
    puts $out
    cd $old_cwd
    exit 1
}

if {![file exists $comp_xml]} {
    puts "\nERROR: 跑完了但没生成 component.xml，说明打包没真正完成。"
    cd $old_cwd
    exit 1
}

puts "    component.xml 生成了 （[file size $comp_xml] 字节）"
cd $old_cwd

# ---- 搬到 ip_repo，给 block design 用 ----
set ip_repo_dst [file join $repo_dir build vivado ip_repo fir_multiband_v1_0]
file delete -force $ip_repo_dst
file mkdir [file dirname $ip_repo_dst]
file copy $ip_dir $ip_repo_dst
puts "    IP 已拷到：$ip_repo_dst"

puts "\n==== 打包完成：$label ===="
puts "下一步：引用它的 block design 脚本里加"
puts "  set_property ip_repo_paths \[list <音频IP仓库> $ip_repo_dst\] \[current_project\]"
exit
