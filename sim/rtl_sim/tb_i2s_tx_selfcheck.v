/* tb_i2s_tx_selfcheck —— 用"codec 的眼睛"看我们发出去的 I2S 流
 *
 * 现象(2026-10-27):出货 fir.bit 有声;我们的 fir_dma / i2sdbg 无声。
 * 引脚、MCLK 10 MHz、BCLK=100MHz/32、LRCLK=100MHz/2048 都和官方
 * iis_ser.vhd 一致(user_logic.vhd 第 201-203 行),codec 寄存器也一致。
 * 于是只剩一种可能:数据线上的**比特位置/极性**和 codec 期望的不一样。
 *
 * 这份 tb 就干一件事:按标准 I2S 接收端的规矩(LRCLK 跳变后、BCLK 上升沿采样)
 * 把 sdata_o 收下来,打印每一帧收到的 26 个比特,和期望的样本对齐看。
 *
 *   bash sim/rtl_sim/run_xsim_i2s_tx.sh
 *
 * 期望:喂 0x1234_5678(低 24 位 0x34_5678),解出来应当是
 *       0x34 5678 的比特串,起始位置 0(左对齐无延迟)或 1(I2S 一比特延迟)。
 */

`timescale 1ns / 1ps

module tb_i2s_tx_selfcheck;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    always #5 clk = ~clk;

    reg  [31:0] ctrl = 32'd0;
    wire [31:0] stat0, stat1;
    wire        bclk, lrclk, sdata_o, m_tlast;
    wire [31:0] m_tdata;
    wire        m_tvalid;
    reg         m_tready = 1'b0;

    i2s_duplex dut (
        .clk           (clk),
        .rst_n         (rst_n),
        .ctrl          (ctrl),
        .stat0         (stat0),
        .stat1         (stat1),
        .bclk          (bclk),
        .lrclk         (lrclk),
        .sdata_o       (sdata_o),
        .sdata_i       (1'b0),
        .s_axis_tdata  (32'h1234_5678),
        .s_axis_tvalid (1'b1),
        .s_axis_tready (),
        .s_axis_tlast  (1'b0),
        .m_axis_tdata  (m_tdata),
        .m_axis_tvalid (m_tvalid),
        .m_axis_tready (m_tready),
        .m_axis_tlast  (m_tlast)
    );

    /* ---- 模拟 codec：BCLK 上升沿采样，LRCLK 边沿分帧 ---- */
    reg        lr_d   = 1'b0;
    reg        bclk_d = 1'b0;
    integer    bitpos = 0;
    reg [31:0] sh     = 32'd0;
    integer    frames = 0;

    always @(posedge clk) begin
        lr_d   <= lrclk;
        bclk_d <= bclk;
        if (lrclk != lr_d) begin
            /* 一帧的一半开始：把刚才收的比特打出来 */
            if (frames < 6) begin
                $display("  frame %0d ch=%s  bits[25:0]=0b%026b  0x%06x",
                         frames, lr_d ? "L->R" : "R->L",
                         sh[25:0], sh[25:0]);
            end
            frames = frames + 1;
            bitpos = 0;
            sh     = 32'd0;
        end else if (bclk && !bclk_d) begin
            /* BCLK 上升沿 = codec 采样的那一刻 */
            sh     = {sh[30:0], sdata_o};
            bitpos = bitpos + 1;
        end
    end

    initial begin
        $display("=== tb_i2s_tx_selfcheck：我们发出去的 I2S 流，用 codec 的规矩解 ===");
        $display("    喂进去的样本 = 0x34_5678 (16 位样本 0x5678 左对齐到 24 位)");
        $display("    期望比特串   = 001101000101011001111000");
        #100 rst_n = 1'b1;
        #1000 ctrl = 32'd1;          // run
        #2000000;                    // 2 ms ≈ 96 帧
        $display("  共 %0d 个半帧", frames);
        $display("[done]");
        $finish;
    end

    initial begin
        #5000000;
        $display("FAIL 超时");
        $finish;
    end

endmodule
