/* axis_fifo —— 32 位同步 FIFO，写口/读口都在 100 MHz 上
 *
 * 为什么不用 fifo_generator：模块引用（module reference）在 BD 里跑，
 * 再挂一个 IP 核就得让它跟着主综合跑 OOC/产物，路径上多一个坑；
 * 这里只要 1024×32，自己写 30 行、行为可查，省事。
 *
 * dout 是"当前队头"（FWFT）：empty=0 时 dout 就是下一个要读的数，
 * rd_en 一拍之后 dout 自动换成下一个。这样接 AXIS 的 tvalid/tdata
 * 不需要再垫一拍。
 *
 * 读通路只有一处 mem[] 读，综合成一块 BRAM（1 写口 + 1 读口）。
 */
`timescale 1ns / 1ps

module axis_fifo #(
    parameter integer AW = 10,      // 深度 2^AW
    parameter integer DW = 32
) (
    input  wire          clk,
    input  wire          rst_n,
    input  wire          wr_en,
    input  wire [DW-1:0] din,
    input  wire          rd_en,
    input  wire          flush,     // 电平：高的时候 FIFO 一直保持空
    output wire [DW-1:0] dout,
    output wire          empty,
    output wire          full,
    output wire [AW:0]   level
);

    reg [DW-1:0] mem [0:(1<<AW)-1];
    reg [AW-1:0] wp, rp;
    reg [AW:0]   cnt;
    reg [DW-1:0] dout_r;

    assign empty = (cnt == 0);
    assign full  = (cnt == (1 << AW));
    assign level = cnt;
    assign dout  = dout_r;

    wire wr_fire = wr_en && !full;
    wire rd_fire = rd_en && !empty;

    always @(posedge clk) begin
        if (wr_fire) mem[wp] <= din;
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || flush) begin
            wp     <= {AW{1'b0}};
            rp     <= {AW{1'b0}};
            cnt    <= {(AW+1){1'b0}};
            dout_r <= {DW{1'b0}};
        end else begin
            if (wr_fire) wp <= wp + 1'b1;
            if (rd_fire) rp <= rp + 1'b1;
            cnt <= cnt + (wr_fire ? 1'b1 : 1'b0) - (rd_fire ? 1'b1 : 1'b0);

            if (empty) begin
                /* 写进空 FIFO：这一拍写进去的数就是队头，直接端出去，
                 * 否则要等下一次 rd_en 才能读出来（AXIS 会白丢一拍）。 */
                if (wr_fire) dout_r <= din;
            end else if (rd_fire) begin
                /* 同一拍"读走最后一个 + 写进一个新的"：新数就在 rp+1，
                 * 但同一地址的 BRAM 读-写在 READ_FIRST 模式下读到的是旧值，
                 * 所以这一种情况必须走 din 旁路。 */
                if (wr_fire && cnt == 1) dout_r <= din;
                else                     dout_r <= mem[rp + 1'b1];
            end
        end
    end

endmodule
