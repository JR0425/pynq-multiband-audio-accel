/* tb_drc_gain —— fir_drc4 增益端口化的两条判据
 *
 * 判据 1（逐位不变）：把新模块的 gain 端口绑 256，喂和改动前完全一样的四段
 *   序列，逐拍比较 y / y_vld。参考模块是改动前的 fir_drc4 副本
 *   （sim/rtl_sim/ref/fir_drc4_ref.v，模块名加了 _ref 后缀）。
 *
 * 判据 2（增益真的生效）：换到 +18 dB（Q7.8 的 2032），检查输出
 *   · 幅度约是 0 dB 时的 8 倍（压缩没动，因为压缩在增益之后，输入小的时候
 *     包络也在门限以下 —— 所以这里用很小的输入，让压缩器不进动作区）
 *   · 大输入 + 大增益时输出饱和在 32767 / -32768，不翻转
 */
`timescale 1ns / 1ps

module tb_drc_gain;

    localparam integer AW = 40;
    reg clk = 0, rst = 1, in_vld = 0;
    reg signed [AW-1:0] b0, b1, b2, b3;
    reg signed [15:0]  g0, g1, g2, g3;

    wire signed [15:0] y_ref, y_new;
    wire               v_ref, v_new;

    fir_drc4_ref #(.BW(AW), .DW(24)) u_ref (
        .clk(clk), .rst(rst), .in_vld(in_vld),
        .band0(b0), .band1(b1), .band2(b2), .band3(b3),
        .y(y_ref), .y_vld(v_ref)
    );

    fir_drc4 #(.BW(AW), .DW(24)) u_new (
        .clk(clk), .rst(rst), .in_vld(in_vld),
        .band0(b0), .band1(b1), .band2(b2), .band3(b3),
        .gain0(g0), .gain1(g1), .gain2(g2), .gain3(g3),
        .y(y_new), .y_vld(v_new)
    );

    always #5 clk = ~clk;

    integer errs = 0, nout = 0, i, cyc;
    reg cmp_en = 1'b1;
    reg meas   = 1'b1;     // 只在稳定段统计幅度，避开复位后的流水尾巴
    integer maxabs = 0;
    reg [31:0] lfsr = 32'h1234_5678;

    task feed(input integer idx);
        begin
            /* 40 位序列：用 LFSR 造 ±2^30 量级（Q8.32 的 0.25 左右），
             * 四段各自取不同的抽头，保证既有正负也有大小起伏 */
            lfsr = {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
            b0 = {{9{lfsr[31]}}, lfsr} >>> 5;
            lfsr = {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
            b1 = {{9{lfsr[31]}}, lfsr} >>> 6;
            lfsr = {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
            b2 = {{9{lfsr[31]}}, lfsr} >>> 7;
            lfsr = {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
            b3 = {{9{lfsr[31]}}, lfsr} >>> 8;
        end
    endtask

    /* 每拍比一次：两个模块的 y/y_vld 是同拍输出。
     * maxabs 的统计必须在 cmp_en 之外 —— 判据 2/3 关掉比对之后还要看幅度。 */
    always @(negedge clk) begin
        if (!rst) begin
            if (cmp_en) begin
                if (v_ref !== v_new) begin
                    errs = errs + 1;
                    if (errs <= 5) $display("  失配 vld: ref=%b new=%b", v_ref, v_new);
                end
                if (v_ref && (y_ref !== y_new)) begin
                    errs = errs + 1;
                    if (errs <= 5)
                        $display("  失配 y: ref=%0d new=%0d", $signed(y_ref), $signed(y_new));
                end
            end
            if (v_new && meas) begin
                nout = nout + 1;
                if (y_new[15] ? (-$signed(y_new) > maxabs) : ($signed(y_new) > maxabs))
                    maxabs = y_new[15] ? -$signed(y_new) : $signed(y_new);
            end
        end
    end

    integer n_hi = 0, n_lo = 0;
    integer ymax_db0 = 0, ymax_db18 = 0;

    initial begin
        g0 = 16'sd256; g1 = 16'sd256; g2 = 16'sd256; g3 = 16'sd256;
        b0 = 0; b1 = 0; b2 = 0; b3 = 0;

        repeat (4) @(posedge clk);
        rst = 0;
        repeat (4) @(posedge clk);

        /* ---------- 判据 1：3000 个采样，gain=256，逐位比 ---------- */
        $display("=== 判据 1：gain=256 与改动前逐位对拍 ===");
        for (i = 0; i < 3000; i = i + 1) begin
            feed(i);
            in_vld = 1;
            @(posedge clk);
            in_vld = 0;
            repeat (2) @(posedge clk);
        end
        repeat (16) @(posedge clk);
        if (errs == 0 && nout > 2900)
            $display("  PASS  输出 %0d 个采样，逐位 0 失配，max|y|=%0d", nout, maxabs);
        else
            $display("  FAIL  输出 %0d 个采样，%0d 处失配，max|y|=%0d", nout, errs, maxabs);

        /* ---------- 判据 2：增益生效与饱和 ---------- */
        $display("=== 判据 2：+18 dB 到位、饱和不翻转 ===");
        cmp_en = 1'b0;          /* 参考模块不能换增益，这一段只测新模块 */
        rst = 1; repeat (2) @(posedge clk); rst = 0;

        /* 小信号：band0 = 1.0/16（Q8.32 的 0x0100_0000），四段同值，
         * 压缩器门限 −20 dBFS，这个量级的包络在门限以下，不会被压 */
        g0 = 16'sd256; g1 = 16'sd256; g2 = 16'sd256; g3 = 16'sd256;
        meas = 1'b0;
        repeat (2) @(posedge clk);
        for (i = 0; i < 40; i = i + 1) begin
            b0 = 40'sh0100_0000; b1 = 40'sh0100_0000;
            b2 = 40'sh0100_0000; b3 = 40'sh0100_0000;
            in_vld = 1; @(posedge clk); in_vld = 0; repeat (2) @(posedge clk);
            if (i == 5) begin maxabs = 0; meas = 1'b1; end   /* 前面几个是流水尾巴 */
        end
        repeat (16) @(posedge clk);
        ymax_db0 = maxabs;

        g0 = 16'sd2032; g1 = 16'sd2032; g2 = 16'sd2032; g3 = 16'sd2032;
        meas = 1'b0;
        repeat (4) @(posedge clk);       /* 增益直接换（这里测的是乘法，不是斜坡） */
        for (i = 0; i < 40; i = i + 1) begin
            if (i == 10) begin maxabs = 0; meas = 1'b1; end
            b0 = 40'sh0100_0000; b1 = 40'sh0100_0000;
            b2 = 40'sh0100_0000; b3 = 40'sh0100_0000;
            in_vld = 1; @(posedge clk); in_vld = 0; repeat (2) @(posedge clk);
        end
        repeat (16) @(posedge clk);
        ymax_db18 = maxabs;

        if (ymax_db0 > 0 && ymax_db18 >= 7.5 * ymax_db0 && ymax_db18 <= 8.5 * ymax_db0)
            $display("  PASS  0 dB max=%0d → +18 dB max=%0d（比值 %.2f，期望 8）",
                     ymax_db0, ymax_db18, ymax_db18 / (ymax_db0 * 1.0));
        else
            $display("  FAIL  0 dB max=%0d → +18 dB max=%0d（比值 %.2f，期望 8）",
                     ymax_db0, ymax_db18, ymax_db0 ? ymax_db18 / (ymax_db0 * 1.0) : 0.0);

        /* 大信号 + 大增益：必须停在 32767，不能翻到负 */
        rst = 1; repeat (2) @(posedge clk); rst = 0;
        g0 = 16'sd4063; g1 = 16'sd4063; g2 = 16'sd4063; g3 = 16'sd4063;   /* +24 dB */
        meas = 1'b0;
        for (i = 0; i < 40; i = i + 1) begin
            if (i == 10) begin maxabs = 0; meas = 1'b1; end
            b0 = 40'sh7000_0000; b1 = 40'sh7000_0000;
            b2 = 40'sh7000_0000; b3 = 40'sh7000_0000;
            in_vld = 1; @(posedge clk); in_vld = 0; repeat (2) @(posedge clk);
        end
        repeat (16) @(posedge clk);
        if (maxabs == 32767)
            $display("  PASS  大信号 +24 dB 饱和在 %0d，没有翻转", maxabs);
        else
            $display("  FAIL  大信号 +24 dB 的 max|y|=%0d（应为 32767）", maxabs);

        if (errs == 0) $display("=== 全部通过 ===");
        else           $display("=== 有失配 %0d 处 ===", errs);
        $finish;
    end

    initial begin
        #400000;
        $display("TIMEOUT");
        $finish;
    end

endmodule
