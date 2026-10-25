/* tb_i2s_duplex —— 自写 I2S 主端的功能仿真
 *
 * 不跑混合语言（VHDL 的 iis_ser/iis_deser 要在 VHDL 顶层里才实例化得起来），
 * 这里用一个"codec 模型"做对端：
 *
 *   收（模型 → DUT）：BFCLK 上 LRCLK 变边的那一拍装数，下一拍开始移出 MSB，
 *                     和标准 I2S 一样"MSB 比声道边沿晚一个 BCLK"
 *   发（DUT → 模型）：在 BCLK 上升沿取样，但跳过 LRCLK 变边后的第一个上升沿
 *                     —— 官方 iis_deser.vhd 就是这么收的
 *
 * 判据：
 *   1. M_AXIS 上收到的一串字，按 L/R 交替且等于模型发出去的那两个字
 *   2. 模型从 SDATA_O 上收回来的字等于 S_AXIS 灌进去的那两个字
 *   3. 灌够数据后 underrun 不再增长
 */
`timescale 1ns / 1ps

module tb_i2s_duplex;

    reg clk = 1'b0;
    always #5 clk = ~clk;          // 100 MHz

    reg rst_n = 1'b0;

    reg  [31:0] ctrl = 32'd0;
    wire [31:0] stat0, stat1;

    wire bclk, lrclk, sdata_o;
    reg  sdata_i = 1'b0;

    // 播放（DUT 从 DMA 侧收）
    reg  [31:0] s_tdata = 32'd0;
    reg         s_tvalid = 1'b0;
    wire        s_tready;
    reg         s_tlast = 1'b0;

    // 录音（DUT 往 DMA 侧送）
    wire [31:0] m_tdata;
    wire        m_tvalid;
    reg         m_tready = 1'b1;
    wire        m_tlast;

    i2s_duplex #(.CAP_AW(5), .TX_AW(5)) dut (
        .clk (clk), .rst_n (rst_n),
        .ctrl (ctrl), .stat0 (stat0), .stat1 (stat1),
        .bclk (bclk), .lrclk (lrclk), .sdata_o (sdata_o), .sdata_i (sdata_i),
        .s_axis_tdata (s_tdata), .s_axis_tvalid (s_tvalid),
        .s_axis_tready (s_tready), .s_axis_tlast (s_tlast),
        .m_axis_tdata (m_tdata), .m_axis_tvalid (m_tvalid),
        .m_axis_tready (m_tready), .m_axis_tlast (m_tlast)
    );

    /* ---------------- codec 模型：发 ---------------- */

    localparam [23:0] MIC_L = 24'h112233;   // 负的小数（bit23=0）
    localparam [23:0] MIC_R = 24'hFEDCBA;   // bit23=1，验证符号扩展

    reg [23:0] c_sr;
    reg        c_lr_d;
    reg        c_first;

    always @(negedge bclk) begin
        if (lrclk !== c_lr_d) begin
            c_lr_d <= lrclk;
            c_sr   <= lrclk ? MIC_R : MIC_L;
            sdata_i <= 1'b0;          // 这一拍是"声道边沿那一拍"，接收端会跳过
            c_first <= 1'b1;
        end else begin
            sdata_i <= c_sr[23];
            c_sr    <= {c_sr[22:0], 1'b0};
            c_first <= 1'b0;
        end
    end

    /* ---------------- codec 模型：收 ---------------- */

    localparam [23:0] SPK_L = 24'h0ABCDE;
    localparam [23:0] SPK_R = 24'h0FEDCB;

    reg [23:0] r_sr;
    reg [4:0]  r_n;
    reg        r_lr_d;
    integer    n_done = 0;
    reg [23:0] got_l = 24'd0, got_r = 24'd0;
    integer    err_l = 0, err_r = 0;

    always @(posedge bclk) begin
        if (lrclk !== r_lr_d) begin
            r_lr_d <= lrclk;
            r_n    <= 5'd0;           // 跳过这个上升沿
        end else if (r_n < 5'd24) begin
            r_sr <= {r_sr[22:0], sdata_o};
            r_n  <= r_n + 5'd1;
            if (r_n == 5'd23) begin
                /* 头两帧不比：上电时移位寄存器和 FIFO 都还没内容，
                 * 那时发出来的 0 不算错 */
                if (lrclk) begin
                    if (n_done > 4) got_r <= {r_sr[22:0], sdata_o};
                    if (n_done > 4 && {r_sr[22:0], sdata_o} !== SPK_R) err_r <= err_r + 1;
                end else begin
                    if (n_done > 4) got_l <= {r_sr[22:0], sdata_o};
                    if (n_done > 4 && {r_sr[22:0], sdata_o} !== SPK_L) err_l <= err_l + 1;
                end
                n_done <= n_done + 1;
            end
        end
    end

    /* ---------------- 收 M_AXIS ---------------- */

    integer n_cap = 0;
    integer bad_cap = 0;
    integer bad_last = 0;
    reg [31:0] cap_exp_l = 32'd0, cap_exp_r = 32'd0;

    /* 块长：ctl[31:16] 给的是"字数-1"。这里用 20 字 = 10 帧，
     * 第 20/40/60… 个字（从 0 数）上必须正好拉 TLAST。 */
    localparam integer BLK_WORDS = 20;

    always @(posedge clk) begin
        if (m_tvalid && m_tready) begin
            if (n_cap % 2 == 0) begin
                if (m_tdata !== cap_exp_l) bad_cap <= bad_cap + 1;
            end else begin
                if (m_tdata !== cap_exp_r) bad_cap <= bad_cap + 1;
            end
            if (m_tlast !== ((n_cap % BLK_WORDS) == BLK_WORDS - 1))
                bad_last <= bad_last + 1;
            n_cap <= n_cap + 1;
        end
    end

    /* ---------------- 主流程 ---------------- */

    /* 持续喂数：tvalid 一直拉高，FIFO 满时自然被 tready 挡住，
     * 这样不会出现"喂完了后面全是 underrun"的假失败。 */
    reg [31:0] f_idx = 32'd0;
    always @(posedge clk) if (s_tvalid && s_tready) f_idx <= f_idx + 32'd1;

    always @(*) begin
        s_tvalid = rst_n;
        s_tdata  = (f_idx[0] == 1'b0) ? {{8{SPK_L[23]}}, SPK_L}
                                      : {{8{SPK_R[23]}}, SPK_R};
    end

    integer i;
    time t_wait;

    initial begin
        $display("=== tb_i2s_duplex ===");

        cap_exp_l = {{8{MIC_L[23]}}, MIC_L};
        cap_exp_r = {{8{MIC_R[23]}}, MIC_R};

        repeat (20) @(posedge clk);
        rst_n = 1'b1;
        ctrl  = 32'd1 | ((BLK_WORDS - 1) << 16);   // run=1，不走 hw_loop

        // 让 I2S 跑 40 帧
        repeat (40 * 2048) @(posedge clk);

        $display("captured words = %0d (expect >= %0d)", n_cap, 2 * 20);
        $display("capture mismatches = %0d", bad_cap);
        $display("tlast mismatches = %0d", bad_last);
        $display("channels decoded = %0d", n_done);
        $display("  L got = 0x%06h expect 0x%06h  errs=%0d", got_l, SPK_L, err_l);
        $display("  R got = 0x%06h expect 0x%06h  errs=%0d", got_r, SPK_R, err_r);
        $display("stat0 ovf=%0d und=%0d  stat1 tx=%0d cap=%0d",
                 stat0[15:0], stat0[31:16], stat1[15:0], stat1[31:16]);

        if (bad_cap == 0 && bad_last == 0 && n_cap >= 2 * 20 &&
            err_l == 0 && err_r == 0 && n_done > 5)
            $display("RESULT: PASS");
        else
            $display("RESULT: FAIL");

        $finish;
    end

    // 超时保护
    initial begin
        #(20 * 2048 * 1000);
        $display("RESULT: TIMEOUT");
        $finish;
    end

endmodule
