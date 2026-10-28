# 复用 22:49 那个工程（BD 已经是批 2 的新接线：去掉了 gain_slice、四组参数包直连）
# 只重跑综合→实现→位流，绕开 create_bd_design 的随机 rsb 失败。
# RTL 源是绝对路径引用，reset_run synth_1 会读到改过的 fir_drc4.v。
open_project D:/pynq-multiband-audio-accel/build/vivado/pl_4_1028_224158/audio_pl.xpr

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
