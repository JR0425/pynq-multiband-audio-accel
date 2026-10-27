/* tb_i2s_vs_official —— 我们的 TX 和官方 iis_ser 逐拍对撞
 *
 * 2026-10-27:出货 fir.bit 有声,我们的 fir_dma / i2sdbg 无声。引脚、MCLK、
 * BCLK/LRCLK 分频(user_logic.vhd 第 201-203 行 clk_cntr(4) / clk_cntr(10))
 * 都和官方一致,codec 寄存器也一致。剩下唯一没比的:数据线上的比特时序。
 *
 * 做法:同一个 100 MHz、同一个 bclk/lrclk(都由 i2s_duplex 产生),
 * 官方 iis_ser 喂 0x345678,我们的 TX 也喂 0x345678,逐拍比 sdata_o。
 *
 *   完全一致 → TX 时序没问题,问题在别处(数据本身是 0、或 codec 侧)
 *   有差异   → 打印第一处不一致的时刻和前后的比特,就是根因
 *
 *   bash sim/rtl_sim/run_xsim_i2s_cmp.sh
 */

`timescale 1ns / 1ps

module tb_i2s_vs_official;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    always #5 clk = ~clk;

    reg  [31:0] ctrl = 32'd0;
    wire [31:0] stat0, stat1;
    wire        bclk, lrclk, ours, m_tlast;
    wire [31:0] m_tdata;
    wire        m_tvalid;

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
        .s_axis_tdata  (32'h1234_5678),
        .s_axis_tvalid (1'b1),
        .s_axis_tready (),
        .s_axis_tlast  (1'b0),
        .m_axis_tdata  (m_tdata),
        .m_axis_tvalid (m_tvalid),
        .m_axis_tready (1'b0),
        .m_axis_tlast  (m_tlast)
    );

    /* 官方发送器：同一个 bclk/lrclk，同一份数据 */
    wire ref_sdata;

    iis_ser u_ref (
        .CLK_100MHZ (clk),
        .SCLK       (bclk),
        .LRCLK      (lrclk),
        .SDATA      (ref_sdata),
        .EN         (1'b1),
        .LDATA      (24'h345678),
        .RDATA      (24'h345678)
    );

    /* ---- 逐拍比对（只在前 40 帧内统计，跳过上电抖动） ---- */
    integer cmp_on  = 0;
    integer n_cmp   = 0;
    integer n_diff  = 0;
    integer shown   = 0;
    reg     ref_d   = 1'b0;
    reg     ours_d  = 1'b0;

    always @(posedge clk) begin
        if (cmp_on) begin
            n_cmp = n_cmp + 1;
            if (ours !== ref_sdata) begin
                n_diff = n_diff + 1;
                if (shown < 12) begin
                    $display("  diff @%0t ns  ours=%b ref=%b  (lrclk=%b bclk=%b)",
                             $time, ours, ref_sdata, lrclk, bclk);
                    shown = shown + 1;
                end
            end
        end
        ref_d  <= ref_sdata;
        ours_d <= ours;
    end

    initial begin
        $display("=== tb_i2s_vs_official：我们的 TX vs 官方 iis_ser ===");
        #100 rst_n = 1'b1;
        #1000 ctrl = 32'd1;
        #200000;                    // 200 us：等 TX 起转
        cmp_on = 1;
        #1000000;                   // 1 ms
        cmp_on = 0;
        $display("  比对 %0d 拍，不一致 %0d 拍", n_cmp, n_diff);
        if (n_diff == 0) $display("PASS 我们的 sdata_o 与官方逐拍一致");
        else             $display("FAIL 有 %0d 拍不一致（见上面前 12 处）", n_diff);
        $display("[done]");
        $finish;
    end

    initial begin
        #3000000;
        $display("FAIL 超时");
        $finish;
    end

endmodule
