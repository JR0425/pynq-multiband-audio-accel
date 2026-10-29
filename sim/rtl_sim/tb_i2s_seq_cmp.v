/* tb_i2s_seq_cmp —— 用**逐帧变化**的数据比我们的 TX 和官方 iis_ser
 *
 * 上一版 `tb_i2s_vs_official` 用的是常量数据(两侧都恒为 0x345678),
 * 常量下"逐拍一致"是很弱的证据:取样顺序、帧内错位、L/R 互换都看不出来。
 *
 * 这一版两侧都换成**逐帧递增**的数据,再用同一个 I2S 解码规矩把两边发出的
 * 比特流解回样本序列,直接比序列。
 *
 *   bash sim/rtl_sim/run_xsim_i2s_seq.sh
 */

`timescale 1ns / 1ps

module tb_i2s_seq_cmp;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    always #5 clk = ~clk;

    reg  [31:0] ctrl = 32'd0;
    wire [31:0] stat0, stat1;
    wire        bclk, lrclk, ours, m_tlast;
    wire [31:0] m_tdata;
    wire        m_tvalid;
    wire        s_tready;

    function [23:0] f;
        input integer k;
        begin
            f = 24'h010000 + (k * 257);      // 每帧 +0x101
        end
    endfunction

    /* 我们的 TX：每收到两个字算一帧 */
    integer     word_cnt = 0;
    reg  [31:0] tx_word  = 32'h0000_0000;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            word_cnt <= 0;
            tx_word  <= 32'd0;
        end else if (s_tready) begin
            word_cnt <= word_cnt + 1;
            tx_word  <= {8'h00, f((word_cnt + 1) / 2)};
        end
    end

    i2s_duplex dut (
        .clk           (clk),
        .rst_n         (rst_n),
        .ctrl          (ctrl),
        .stat0         (stat0),
        .stat1         (stat1),
        .bclk          (bclk),
        .lrclk         (lrclk),
        .sdata_o       (ours),
        .sdata_i       (1'b0),
        .s_axis_tdata  (tx_word),
        .s_axis_tvalid (1'b1),
        .s_axis_tready (s_tready),
        .s_axis_tlast  (1'b0),
        .m_axis_tdata  (m_tdata),
        .m_axis_tvalid (m_tvalid),
        .m_axis_tready (1'b0),
        .m_axis_tlast  (m_tlast)
    );

    /* 官方发送器：帧边界后 4 拍换数据，避开 start_left 那一拍的锁存 */
    reg [23:0] ref_word  = 24'h000000;
    integer    ref_frame = 0;
    reg        lr_d      = 1'b0;
    reg [2:0]  upd_dly   = 3'd0;

    wire ref_sdata;

    iis_ser u_ref (
        .CLK_100MHZ (clk),
        .SCLK       (bclk),
        .LRCLK      (lrclk),
        .SDATA      (ref_sdata),
        .EN         (1'b1),
        .LDATA      (ref_word),
        .RDATA      (ref_word)
    );

    /* 解码：BCLK 上升沿采样，收满 25 位就取 sh[24:1]（跳过 I2S 的一比特延迟） */
    reg [24:0]  sh_ours = 25'd0, sh_ref = 25'd0;
    integer     bit_ours = 0,    bit_ref = 0;
    reg [23:0]  got_ours [0:63];
    reg [23:0]  got_ref  [0:63];
    integer     n_ours = 0,      n_ref = 0;
    reg         bclk_d = 1'b0;

    always @(posedge clk) begin
        lr_d   <= lrclk;
        bclk_d <= bclk;

        if (lrclk != lr_d) upd_dly <= 3'd4;
        else if (upd_dly != 0) upd_dly <= upd_dly - 3'd1;
        else if (ctrl[0] && lrclk && !lr_d) begin
            /* 占位：真正的更新在下面 */
        end

        if (upd_dly == 3'd1) begin
            ref_frame = ref_frame + 1;
            ref_word  = f(ref_frame);
        end

        if (lrclk != lr_d) begin
            /* 新的一帧：解码器重新对齐 */
            bit_ours = 0;
            sh_ours  = 25'd0;
            bit_ref  = 0;
            sh_ref   = 25'd0;
        end

        if (bclk && !bclk_d) begin
            sh_ours  = {sh_ours[23:0], ours};
            sh_ref   = {sh_ref[23:0],  ref_sdata};
            bit_ours = bit_ours + 1;
            bit_ref  = bit_ref + 1;

            if (bit_ours == 25) begin
                if (n_ours < 64) got_ours[n_ours] = sh_ours[24:1];
                n_ours   = n_ours + 1;
                bit_ours = 0;
                sh_ours  = 25'd0;
            end
            if (bit_ref == 25) begin
                if (n_ref < 64) got_ref[n_ref] = sh_ref[24:1];
                n_ref   = n_ref + 1;
                bit_ref = 0;
                sh_ref  = 25'd0;
            end
        end
    end

    integer i, j, best, bestcnt, same;

    initial begin
        $display("=== tb_i2s_seq_cmp：逐帧变化的数据，比两边的样本序列 ===");
        #100 rst_n = 1'b1;
        #1000 ctrl = 32'd1;
        #3000000;                       // 3 ms ≈ 146 帧

        $display("  我们: %h %h %h %h %h %h",
                 got_ours[2], got_ours[3], got_ours[4], got_ours[5], got_ours[6], got_ours[7]);
        $display("  官方: %h %h %h %h %h %h",
                 got_ref[2], got_ref[3], got_ref[4], got_ref[5], got_ref[6], got_ref[7]);

        best = 0; bestcnt = -1;
        for (j = 0; j < 8; j = j + 1) begin
            same = 0;
            for (i = 0; i < 20; i = i + 1)
                if (got_ours[10 + i] == got_ref[10 + j + i]) same = same + 1;
            if (same > bestcnt) begin bestcnt = same; best = j; end
        end
        $display("  最佳对齐：错开 %0d 帧，20 个里相同 %0d 个", best, bestcnt);
        if (bestcnt == 20) $display("PASS 逐帧变化下两边样本序列一致");
        else               $display("FAIL 不一致（错开 %0d 帧只对上 %0d/20）", best, bestcnt);
        $display("[done]");
        $finish;
    end

    initial begin
        #8000000;
        $display("FAIL 超时");
        $finish;
    end

endmodule
