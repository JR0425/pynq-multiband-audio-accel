`timescale 1ns / 1ps
module tb_dbg_align;
    reg clk = 0, rst = 1, in_vld = 0;
    reg signed [39:0] band;
    wire signed [23:0] yv, env_o, gr_o;
    wire yv_vld;
    fir_drc4_chan #(.BW(40), .DW(24)) dut (
        .clk(clk), .rst(rst), .in_vld(in_vld), .band(band),
        .gain_i(16'sd256), .thr_i(24'sd0), .ratio_i(24'sd4194304),
        .atk_i(24'sd17408), .rel_i(24'sd896),
        .yv(yv), .yv_vld(yv_vld), .env_o(env_o), .gr_o(gr_o));
    always #5 clk = ~clk;
    integer cyc = 0, k;
    /* 只在负沿看：显示的是本拍（上一正沿写入）的寄存器值 */
    always @(negedge clk) begin
        cyc = cyc + 1;
        if (!rst && yv_vld) $display("  neg%0d  yv_vld=1 yv=%0d", cyc, $signed(yv));
        if (!rst && in_vld) $display("  neg%0d  in_vld=1 band=%0d", cyc, $signed(band));
    end
    initial begin
        band = 0;
        repeat (4) @(posedge clk);
        rst = 0;
        for (k = 5; k <= 12; k = k + 1) begin
            @(negedge clk);
            band = k * 1024;      // gb0 = band>>10 = k，和复位值 0 区分开
            in_vld = 1;
            @(negedge clk);
            in_vld = 0;
            repeat (2) @(negedge clk);
        end
        repeat (30) @(negedge clk);
        $finish;
    end
endmodule
