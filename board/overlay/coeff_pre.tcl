# 综合前钩子（build_rtl.tcl 通过 STEPS.SYNTH_DESIGN.TCL.PRE 挂上它）
#
# fir_mb_fe.v 用 $readmemh 按**相对文件名**读三张系数表。项目模式下综合在
# <proj>.runs/synth_1 里跑，cwd 就是那个目录；文件不在那儿时 Vivado 只报
# warning、不报错，ROM 会变成全 X —— 板上现象是输出全零，而 xsim 一直是
# 9 路 1000/1000 全绿，两边不矛盾，会白查一天。
#
# 所以这里既拷贝、又硬断言。

set repo $::env(FIR_RTL_REPO)
if {![info exists repo] || $repo eq ""} {
    error "环境变量 FIR_RTL_REPO 没设（应该由 build_rtl.tcl 传进来）"
}
if {![file isdirectory $repo]} {
    error "FIR_RTL_REPO 指的不是目录：$repo"
}

foreach n {coeffs_lp500 coeffs_lp1000 coeffs_lp2000} {
    set src [file join $repo src rtl ${n}_n193.mem]
    if {![file exists $src]} { error "缺系数表 $src" }
    file copy -force $src [file join [pwd] ${n}.mem]
}

foreach n {coeffs_lp500 coeffs_lp1000 coeffs_lp2000} {
    if {![file exists [file join [pwd] ${n}.mem]]} {
        error "系数表没就位：${n}.mem 不在 [pwd] —— 综合会静默全 X，直接停"
    }
}

puts "### coeff mems in \[pwd\] : [glob -nocomplain *.mem]"
