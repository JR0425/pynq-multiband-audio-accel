/* fir_wave_cap —— AXI-Stream 握手波形抓取（只给"出图"那版位流用）
 *
 * 为什么不用 Xilinx 的 ILA：
 *   · ila v6.2 的 IP-XACT 里根本没有 AXI4-Lite 从口（component.xml 只有
 *     TRIG_IN / TRIG_OUT / signal_clock / SLOT_x_AXI 监视槽），它只能由 JTAG
 *     经 dbg_hub 控制；PYNQ 板上的 JTAG 接在 PL 的 USB 口上，PS 够不着。
 *   · system_ila 有 AXI-Lite 从口，但它的寄存器映射和数据排布只写在 PG261
 *     的人读表里，Vivado 生成的 hwh 里一个寄存器也没有 —— 要在板上靠试错
 *     摸出来。抓取器自己写，寄存器就是下面这几个，没有未知量。
 *
 * 行为（一个 100 MHz 周期记一拍，连续 DEPTH 拍）：
 *   arm 上升沿清指针、开始等触发；    trig_sel 选触发点：
 *     0 = 第一个 in_valid & in_ready（块头）
 *     1 = 第一个 in_valid & in_ready & in_last（块尾）
 *     2 = 不等，arm 后立刻开始（能看见复位期间 tready 压低的样子）
 *   触发那一拍记为第 0 条；写满 DEPTH 拍后 done = 1，armed 自动落下，
 *   要再抓必须先把 arm 拉低再拉高。
 *
 * 数据分两路写进两块 1024×32 的 BRAM（一个写口一拍只能写一个字，放不进
 * 64 位，拆成输入侧/输出侧两块，PS 那边各读 4 KB）：
 *   输入侧  {in_tdata[15:0], 13'd0, in_last, in_ready, in_valid}
 *   输出侧  {out_tdata[15:0], 13'd0, out_last, out_ready, out_valid}
 *
 * 这一版位流**只出图不出数**：中间串的监视器多了一级组合逻辑，
 * 报告里的拍数和吞吐一律引不带抓取器的 fir_rtl_dma.bit。
 */

`timescale 1ns / 1ps

module fir_wave_cap #(
    parameter integer DEPTH = 1024,
    parameter integer AW    = 10
) (
    input  wire        clk,
    input  wire        rst_n,

    input  wire        arm,
    input  wire [1:0]  trig_sel,

    input  wire        in_valid,
    input  wire        in_ready,
    input  wire        in_last,
    input  wire [15:0] in_tdata,

    input  wire        out_valid,
    input  wire        out_ready,
    input  wire        out_last,
    input  wire [15:0] out_tdata,

    output reg         done,

    output reg              bram_en,
    output reg [AW-1:0]     bram_addr,
    output reg [31:0]       bram_din_in,
    output reg [31:0]       bram_din_out
);

    reg           armed;
    reg           trigged;
    reg  [AW-1:0] wr;
    reg  [1:0]    arm_q;          // 找 arm 上升沿

    wire trig_head = in_valid && in_ready;
    wire trig_tail = in_valid && in_ready && in_last;
    wire trig      = (trig_sel == 2'd1) ? trig_tail : trig_head;
    wire byp       = (trig_sel == 2'd2);   // 不等触发

    wire cap_now = armed && !done && (trigged || byp || trig);

    always @(posedge clk) begin
        if (!rst_n) begin
            armed        <= 1'b0;
            trigged      <= 1'b0;
            wr           <= {AW{1'b0}};
            arm_q        <= 2'b00;
            done         <= 1'b0;
            bram_en      <= 1'b0;
            bram_addr    <= {AW{1'b0}};
            bram_din_in  <= 32'd0;
            bram_din_out <= 32'd0;
        end else begin
            arm_q  <= {arm_q[0], arm};
            bram_en <= 1'b0;

            if (arm_q == 2'b01) begin
                armed   <= 1'b1;
                trigged <= 1'b0;
                done    <= 1'b0;
                wr      <= {AW{1'b0}};
            end else if (!arm) begin
                armed   <= 1'b0;      // 拉低就撤销，PS 靠这个重新武裝
            end

            if (cap_now) begin
                bram_en      <= 1'b1;
                bram_addr    <= wr;
                bram_din_in  <= {in_tdata, 13'd0, in_last, in_ready, in_valid};
                bram_din_out <= {out_tdata, 13'd0, out_last, out_ready, out_valid};
                trigged      <= 1'b1;
                if (wr == DEPTH - 1) begin
                    done  <= 1'b1;
                    armed <= 1'b0;
                end else begin
                    wr <= wr + 1'b1;
                end
            end
        end
    end

endmodule
