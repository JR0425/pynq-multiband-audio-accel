/* 替换用的 fir_pl_direct —— 只换发送器，端口完全一致
 *
 * 用途：绕开 Vivado 在 Windows 上 `create_bd_design` 随机"couldn't read file"的老毛病。
 * 做法不是重建 BD，而是复制现成的 build/vivado/audio_pl 工程，把这个文件替换掉
 * 原来的 fir_pl_direct.v（模块名保持一致，BD 里的 fir_pl_direct_0 不用动），
 * 再重跑综合/实现。发送器换成官方 iis_ser.vhd。
 *
 * 时钟：和官方 user_logic.vhd 一样，bclk = cntr[4]，lrclk = cntr[10]。
 * ctrl[0] = 1 出声（440 Hz 方波，−12 dBFS）。
 */

`timescale 1ns / 1ps

module fir_pl_direct (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [31:0] ctrl,
    output wire [31:0] stat0,
    output wire [31:0] stat1,
    output wire [31:0] stat2,
    output wire [31:0] dbg0,
    output wire [31:0] dbg1,
    output wire        bclk,
    output wire        lrclk,
    output wire        sdata_o,
    input  wire        sdata_i
);

    reg [10:0] cntr;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) cntr <= 11'd0;
        else        cntr <= cntr + 11'd1;
    end

    wire sclk_i  = cntr[4];
    wire lrclk_i = cntr[10];

    reg [15:0] ph;
    reg        lr_d;
    wire       tick = lrclk_i & ~lr_d;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ph   <= 16'd0;
            lr_d <= 1'b0;
        end else begin
            lr_d <= lrclk_i;
            if (tick && ctrl[0]) ph <= ph + 16'd590;
        end
    end

    wire [23:0] tone24 = ph[15] ? 24'h200000 : 24'hE00000;

    iis_ser u_tx (
        .CLK_100MHZ (clk),
        .SCLK       (sclk_i),
        .LRCLK      (lrclk_i),
        .SDATA      (sdata_o),
        .EN         (ctrl[0]),
        .LDATA      (tone24),
        .RDATA      (tone24)
    );

    assign bclk  = sclk_i;
    assign lrclk = lrclk_i;
    assign stat0 = 32'd0;
    assign stat1 = 32'd0;
    assign stat2 = 32'd0;
    assign dbg0  = {16'd0, ph};
    assign dbg1  = {31'd0, sdata_o};

endmodule
