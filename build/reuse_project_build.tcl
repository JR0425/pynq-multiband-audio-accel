# 复用 16:14 成功的工程直接跑综合→实现→位流，**完全绕开 create_bd_design**
# （rsb 加载随机失败卡了两天，重试窗口太小；这份工程里 BD/地址/wrapper 全是好的，
#  RTL 源是绝对路径引用，fir_gain_ramp.v 改过两级流水后 reset_run 会自动读到新版）
open_project D:/pynq-multiband-audio-accel/build/vivado/pl_2_1028_160719/audio_pl.xpr

reset_run synth_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    error "synth_1 没跑完: [get_property STATUS [get_runs synth_1]]"
}
puts "### SYNTH OK"

launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    error "impl_1 没跑完: [get_property STATUS [get_runs impl_1]]"
}
puts "### IMPL OK"

set bit [file join [get_property DIRECTORY [get_runs impl_1]] audio_pl_wrapper.bit]
if {![file exists $bit]} { error "位流不在 $bit" }
file copy -force $bit D:/pynq-multiband-audio-accel/board/overlay/fir_pl.bit
puts "### BIT COPIED $bit"

# 时序读数（要人眼看，别让违例位流悄悄过）
set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -delay_type max]]
puts "### WNS_ns = $wns"
puts "### DONE"
