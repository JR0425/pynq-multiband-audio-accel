# 单模块资源测量：把 fir_drc4 例化 1 份 / 2 份，各综合一次，
# 差值就是一套 DRC4 的 DSP/LUT 开销。用来决定"双耳"能不能直接例化两份。
#   vivado -mode batch -source build/probe_drc_dsp.tcl
set root [file dirname [file normalize [info script]]]/..
set rtl  "$root/src/rtl"

read_verilog [file join $rtl fir_drc4.v]

# ---- 包装：例化 N 份，端口全引出，避免被优化掉 ----
proc wrap {n} {
    set f [open "drc_wrap_$n.v" w]
    puts $f "module drc_wrap_$n ("
    puts $f "    input  wire clk, input wire rst_w, input wire in_vld,"
    puts $f "    input  wire signed \[15:0\] b0, b1, b2, b3,"
    puts $f "    input  wire signed \[15:0\] g0, g1, g2, g3,"
    puts $f "    input  wire \[23:0\] thr, ratio, atk, rel,"
    puts $f "    output wire \[15:0\] y_sum, output wire yv_sum,"
    puts $f "    output wire \[95:0\] env_sum, output wire \[95:0\] gr_sum);"
    puts $f "    wire \[${n}\*16-1:0\] ys; wire \[${n}-1:0\] vs;"
    for {set i 0} {$i < $n} {incr i} {
        puts $f "    fir_drc4 u$i (.clk(clk), .rst(rst_w), .in_vld(in_vld),"
        puts $f "        .band0(b0), .band1(b1), .band2(b2), .band3(b3),"
        puts $f "        .gain0(g0), .gain1(g1), .gain2(g2), .gain3(g3),"
        puts $f "        .thr0(thr), .thr1(thr), .thr2(thr), .thr3(thr),"
        puts $f "        .ratio0(ratio), .ratio1(ratio), .ratio2(ratio), .ratio3(ratio),"
        puts $f "        .atk0(atk), .atk1(atk), .atk2(atk), .atk3(atk),"
        puts $f "        .rel0(rel), .rel1(rel), .rel2(rel), .rel3(rel),"
        puts $f "        .y(ys\[$i\*16 +: 16\]), .y_vld(vs\[$i\]),"
        puts $f "        .env_o(env_sum), .gr_o(gr_sum));"
    }
    puts $f "    assign y_sum = ys\[0 +: 16\];"
    puts $f "    assign yv_sum = vs\[0\];"
    puts $f "endmodule"
    close $f
}

foreach n {1 2} {
    wrap $n
    read_verilog "drc_wrap_$n.v"
    synth_design -top "drc_wrap_$n" -part xc7z020clg400-1 -mode out_of_context
    set dsp [get_cells -hier -filter {REF_NAME =~ DSP48*}] 
    set lut [llength [get_cells -hier -filter {REF_NAME =~ LUT*}]]
    set ff  [llength [get_cells -hier -filter {REF_NAME =~ FD*}]]
    puts "### DRC4 x$n  DSP=[llength $dsp]  LUT=$lut  FF=$ff"
    close_design
}
puts "### probe done"
