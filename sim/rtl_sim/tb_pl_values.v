/* tb_pl_values —— 喂真实 I2S 串行数据，看整条链到底吐不吐非零样本
 *
 * 背景：板上 `fir_pl.bit` 跑起来 din/dout 都是 48.8k/s、und 不涨、codec 已解除静音，
 * 耳机却一点声都没有。数字侧唯一没被证明的就是"样本值本身是不是 0"。
 * 之前的 tb_pl_channel 只喂 sdata_i 一个方波，从不看数值。
 *
 * 这份 tb：
 *   1. 按 I2S 规矩（BCLK 下降沿换数据、MSB 先出、比 LRCLK 边沿晚一位）
 *      把 1 kHz 正弦串行打进 sdata_i，幅度 ±800（约 -32 dBFS，和真实话筒同量级）；
 *   2. 直接监视内部节点：u_fe.dout（前端输出）、ofifo_q / ply_tdata（交给 I2S 的字）；
 *   3. 打印各自的最大绝对值。
 *
 * 判据：
 *   u_fe.dout 最大绝对值 ≈ 800（±20%）  -> 前端没问题，问题在 codec/模拟侧
 *   u_fe.dout 恒 0 / 极小                -> 前端把信号吃掉了，就是它
 *
 *   bash sim/rtl_sim/run_xsim_pl_values.sh
 */

`timescale 1ns / 1ps

module tb_pl_values;

    reg clk   = 1'b0;
    reg rst_n = 1'b0;
    always #5 clk = ~clk;              // 100 MHz

    reg  [31:0] ctrl = 32'd0;
    wire [31:0] stat0, stat1, stat2, dbg0, dbg1;
    wire        bclk, lrclk, sdata_o;
    reg         sdata_i = 1'b0;

    /* 2026-10-29：参数包以前是悬空的（z），drc 一开输出全是饱和值，这份 tb 只能看
     * "通不通"。现在接成"直通参数"：增益 0 dB、门限 0、ratio=1.0（压缩器不动作）、
     * atk=rel=0。这样 drc 开和 drc 关的**数值必须一样**，顺带把双耳两条通路
     * （左右各一份 DRC、FIFO 写左右两个字）一起验了。 */
    localparam [23:0] RATIO_ONE = 24'sd4194304;      // Q2.22 的 1.0
    reg [63:0] gain_pack = {4{16'd256}};
    reg [95:0] thr_pack   = 96'd0;
    reg [95:0] ratio_pack = {4{RATIO_ONE}};
    reg [95:0] atk_pack   = 96'd0;
    reg [95:0] rel_pack   = 96'd0;

    fir_pl_direct #(.TX_TARGET(128)) dut (
        .clk(clk), .rst_n(rst_n), .ctrl(ctrl),
        .stat0(stat0), .stat1(stat1), .stat2(stat2),
        .dbg0(dbg0), .dbg1(dbg1),
        .bclk(bclk), .lrclk(lrclk), .sdata_o(sdata_o), .sdata_i(sdata_i),
        .gain_pack(gain_pack), .thr_pack(thr_pack), .ratio_pack(ratio_pack),
        .atk_pack(atk_pack), .rel_pack(rel_pack),
        .gain_pack_b1(gain_pack), .thr_pack_b1(thr_pack),
        .ratio_pack_b1(ratio_pack), .atk_pack_b1(atk_pack), .rel_pack_b1(rel_pack),
        .ramp_div(16'd6), .ramp_div_b1(16'd6),
        .ply_rdata(32'd0), .ply_ctl(32'd0), .ply_len(32'd0)
    );

    /* ---------------- I2S 从机（codec 那一侧） ---------------- */

    reg        bclk_q = 1'b0, lrclk_q = 1'b0;
    always @(posedge clk) begin
        bclk_q  <= bclk;
        lrclk_q <= lrclk;
    end
    wire bclk_fall = ~bclk & bclk_q;
    wire lr_edge   = lrclk ^ lrclk_q;

    reg [5:0]  kbit;              // 本半帧里第几个 BCLK 下降沿
    integer   nframe;
    reg [23:0] word_l, word_r;
    real      ph;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            kbit    <= 6'd0;
            nframe  <= 0;
            sdata_i <= 1'b0;
            word_l  <= 24'd0;
            word_r  <= 24'd0;
            ph      <= 0.0;
        end else if (lr_edge) begin
            kbit <= 6'd0;
            if (lrclk_q && !lrclk) begin       // 左声道开始：在这里换新样本
                nframe <= nframe + 1;
                ph     <= ph + 2.0 * 3.14159265358979 * 1000.0 / 48828.0;
                word_l <= (($rtoi(800.0 * $sin(ph)) & 16'hFFFF) << 8);
                word_r <= (($rtoi(800.0 * $sin(ph)) & 16'hFFFF) << 8);
            end
        end else if (bclk_fall) begin
            kbit <= kbit + 6'd1;
            // 第 1 个下降沿对应"被跳过"的那一位；第 2..25 个送 bit23..bit0
            if (lrclk_q && !lrclk) begin       // 这一半是左声道
                sdata_i <= (kbit + 6'd1 >= 6'd3 && kbit + 6'd1 <= 6'd26)
                           ? word_l[26 - (kbit + 6'd1)] : 1'b0;
            end else begin
                sdata_i <= (kbit + 6'd1 >= 6'd3 && kbit + 6'd1 <= 6'd26)
                           ? word_r[26 - (kbit + 6'd1)] : 1'b0;
            end
        end
    end

    /* 注意上面用 lrclk_q（当前半帧的极性），lrclk=0 表示左半帧 */

    /* ---------------- 统计 ---------------- */

    integer fe_max = 0, ply_max = 0, fin_max = 0, dbg_n = 0;
    integer fe_n   = 0;
    reg [15:0] din_last = 0, dout_last = 0;

    always @(posedge clk) begin
        if (dut.fe_din_vld) begin
            if (dbg_n < 8) begin
                dbg_n = dbg_n + 1;
                $display("    din=%0d (0x%04h)  word_l=0x%06h", dut.fe_din, dut.fe_din, word_l);
            end
            if (dut.fe_din > fin_max)  fin_max = dut.fe_din;
            if (-dut.fe_din > fin_max) fin_max = -dut.fe_din;
        end
        if (dut.fe_dout_vld) begin
            fe_n = fe_n + 1;
            if (dut.fe_dout > fe_max)  fe_max = dut.fe_dout;
            if (-dut.fe_dout > fe_max) fe_max = -dut.fe_dout;
        end
        if (dut.ply_tvalid && dut.ply_tready) begin
            if ($signed(dut.ply_tdata[23:8]) > ply_max)  ply_max = $signed(dut.ply_tdata[23:8]);
            if (-$signed(dut.ply_tdata[23:8]) > ply_max) ply_max = -$signed(dut.ply_tdata[23:8]);
        end
        din_last  <= dbg0[15:0];
        dout_last <= dbg0[31:16];
    end

    initial begin
        rst_n = 1'b0;
        #200 rst_n = 1'b1;
        #200;
        ctrl = 32'h0000_0007;      // run | drc | flush
        #2000;
        ctrl = 32'h0000_0103;      // 松 flush，清计数
        #50;
        ctrl = 32'h0000_0001;      // run，drc 关闭：只看前端

        $display("=== 前 40 ms（drc 关） ===");
        #12_000_000;
        $display("  fe 输出个数=%0d  max|fe.din|=%0d  max|fe.dout|=%0d   max|ply|=%0d", fe_n, fin_max, fe_max, ply_max);
        $display("  帧数=%0d  din=%0d dout=%0d", nframe, din_last, dout_last);

        fe_max = 0; ply_max = 0; fe_n = 0;
        ctrl = 32'h0000_0003;      // 打开 drc
        $display("=== 再 40 ms（drc 开） ===");
        #12_000_000;
        $display("  fe 输出个数=%0d  max|fe.din|=%0d  max|fe.dout|=%0d   max|ply|=%0d", fe_n, fin_max, fe_max, ply_max);

        ctrl = 32'h0;
        $display("[done]");
        $finish;
    end

endmodule
