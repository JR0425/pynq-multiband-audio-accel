/* fir_pl_direct 的测试台 —— 复现板上"flush 后立刻放行，数据不出"的现象。
 *
 * 板上观察到（2026-10-25）：
 *   跑起来之后 ctrl=4(flush, run=0) 20ms -> ctrl=1(只 run)：
 *       录像 FIFO 一直被排空、播放 FIFO 只进过 1 个字、之后什么都不出。
 *   中间只要 run=0 停 1 秒（让录像 FIFO 灌满）再 run=1，就正常。
 *   => 收字没坏，是"前端拿得到字但不往外发"。
 *
 * 这里把同样的时序喂进去，并打印内部相位/valid 流水，定位是哪一级停了。
 *
 * sdata_i 直接接 1（采样恒为全 1），只看数据流是否通，不看数值。
 */

`timescale 1ns / 1ps

module tb_fir_pl_direct;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    always #5 clk = ~clk;          // 100 MHz

    reg [31:0] ctrl = 32'd0;

    wire [31:0] stat0, stat1, stat2;
    wire        bclk, lrclk, sdata_o;

    fir_pl_direct #(.TX_TARGET(128)) dut (
        .clk     (clk),
        .rst_n   (rst_n),
        .ctrl    (ctrl),
        .stat0   (stat0),
        .stat1   (stat1),
        .stat2   (stat2),
        .bclk    (bclk),
        .lrclk   (lrclk),
        .sdata_o (sdata_o),
        .sdata_i (1'b1)
    );

    /* 统计 */
    integer n_din_vld = 0;
    integer n_out_vld = 0;
    integer n_ofifo_wr = 0;
    integer n_ply_tr   = 0;
    integer n_cap_rd   = 0;

    always @(posedge clk) begin
        if (dut.fe_din_vld)          n_din_vld <= n_din_vld + 1;
        if (dut.out_vld)             n_out_vld <= n_out_vld + 1;
        if (dut.ofifo_wr)            n_ofifo_wr <= n_ofifo_wr + 1;
        if (dut.ply_tvalid && dut.ply_tready) n_ply_tr <= n_ply_tr + 1;
        if (dut.cap_tvalid && dut.cap_tready) n_cap_rd <= n_cap_rd + 1;
    end

    task show(input [255:0] tag);
        begin
            $display("[%s] t=%0t us ph=%0d fe_rst=%b v_sr=%b cap_lvl=%0d tx_lvl=%0d ofifo=%0d | din_vld=%0d cap_rd=%0d out_vld=%0d fifo_wr=%0d ply=%0d stat0=%08x stat2=%08x",
                     tag, $time / 1000, dut.u_fe.ph, dut.fe_rst, dut.u_fe.v_sr,
                     dut.cap_lvl, dut.tx_lvl, dut.ofifo_cnt,
                     n_din_vld, n_cap_rd, n_out_vld, n_ofifo_wr, n_ply_tr,
                     stat0, stat2);
            $display("           dbg0=%08x (din=%0d dout=%0d)  dbg1=%08x",
                     dut.dbg0, dut.dbg0[15:0], dut.dbg0[31:16], dut.dbg1);
        end
    endtask

    initial begin
        #100 rst_n = 1'b1;

        #20000;                       // 上电后什么都不做，录像 FIFO 会灌满
        show("idle");

        ctrl = 32'd4;                 // flush，run=0
        #20000;
        show("flush");

        ctrl = 32'd1;                 // 松开 flush，立刻放行
        #20000;
        show("run+20us");
        #100000;
        show("run+120us");
        #500000;
        show("run+620us");
        #1000000;
        show("run+1.6ms");

        ctrl = 32'd0;                 // 停 1 秒（仿真里缩到 1 ms，够灌满了）
        #1000000;
        show("stop");

        ctrl = 32'd1;                 // 再放行
        #200000;
        show("rerun");

        $finish;
    end

    initial begin
        #5000000;
        $display("[tb] 超时退出");
        $finish;
    end

endmodule
