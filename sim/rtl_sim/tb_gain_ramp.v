/* tb_gain_ramp —— 四段增益斜坡的三条判据
 *
 * 判据 1  大步长不咔：256(0 dB) → 2032(+18 dB)，检查
 *            · 单调不回退
 *            · 每一步的变化量 ≤ ceil(差值/2^div)+1，没有阶跃
 *            · 600 个采样内收敛（div=6 时理论 64 个采样）
 *            · busy 在收敛前为 1、收敛后为 0
 * 判据 2  小步长也能到底：256 → 257 必须彻底对齐（"至少走 1 LSB"那条规则），
 *         否则 busy 永远不落，网页的"已生效"灯永远不灭。
 * 判据 3  clr 直接对齐目标（flush 之后不该带着旧斜坡跑）。
 */
`timescale 1ns / 1ps

module tb_gain_ramp;

    reg clk = 0, rst_n = 0, clr = 0, tick = 0;
    reg signed [15:0] t0, t1, t2, t3;
    reg [15:0] div = 16'd6;

    wire signed [15:0] c0, c1, c2, c3;
    wire busy;

    fir_gain_ramp dut (
        .clk(clk), .rst_n(rst_n), .clr(clr), .tick(tick),
        .tgt0(t0), .tgt1(t1), .tgt2(t2), .tgt3(t3), .div(div),
        .cur0(c0), .cur1(c1), .cur2(c2), .cur3(c3), .busy(busy)
    );

    always #5 clk = ~clk;

    integer errs = 0;
    integer i, n;
    reg signed [15:0] prev0;
    integer maxstep;

    task step_once;
        begin
            tick = 1;
            @(posedge clk);
            #1;
            tick = 0;
            /* 真实链路里 tick = fe_dout_vld，前端 3 拍吐一个采样，
             * tick 间隔 ≥ 2 拍。2026-10-28 斜坡改成两级流水后这成了硬前提
             * （step 寄存拍与 c 加法拍之间 c 不动）；TB 原来 tick 几乎常高
             * （间隔 1 拍），那个节奏在硬件上根本不会发生。 */
            repeat (2) @(posedge clk);
            #1;
        end
    endtask

    initial begin
        t0 = 16'sd256; t1 = 16'sd256; t2 = 16'sd256; t3 = 16'sd256;
        repeat (4) @(posedge clk);
        rst_n = 1;
        repeat (2) @(posedge clk);

        /* ---------- 上电阶段：c 从 0（静音防爆音）爬到默认 256 ----------
         * 2026-10-28 起上电不再是"复位直接装载 256"，而是从 0 逐采样爬。
         * 注意 >>>div 是指数收敛（每步走剩余的 1/2^div，64 采样是时间常数
         * 不是收敛步数），所以这里等 busy 落零，不数固定步数。 */
        i = 0;
        while (busy !== 1'b0 && i < 2000) begin
            step_once;
            i = i + 1;
        end
        if (c0 !== 16'sd256) begin
            $display("FAIL 上电收敛后 c0=%0d（期望 256）", c0);
            errs = errs + 1;
        end

        /* ---------- 判据 1：256 → 2032 ---------- */
        $display("=== 判据 1：256 → 2032（0 dB → +18 dB），div=6 ===");
        t0 = 16'sd2032; t1 = 16'sd2032; t2 = 16'sd2032; t3 = 16'sd2032;
        prev0 = c0;
        maxstep = 0;
        n = -1;
        for (i = 0; i < 2000; i = i + 1) begin
            step_once;
            /* 单调：不许回退 */
            if (c0 < prev0) begin
                errs = errs + 1;
                if (errs <= 5) $display("  回退: %0d → %0d @%0d", prev0, c0, i);
            end
            if ((c0 - prev0) > maxstep) maxstep = c0 - prev0;
            if (n < 0 && !busy) n = i + 1;
            prev0 = c0;
        end
        $display("  收敛用了 %0d 个采样（期望 ~64），最大单步 %0d（理论上限 %0d）",
                 n, maxstep, (2032 - 256) / 64 + 2);
        if (c0 == 2032 && c1 == 2032 && c2 == 2032 && c3 == 2032 && n > 0 && n < 600)
            $display("  PASS  四段都到 2032，busy 已落");
        else begin
            errs = errs + 1;
            $display("  FAIL  c=%0d/%0d/%0d/%0d busy=%b n=%0d", c0, c1, c2, c3, busy, n);
        end
        if (maxstep <= (2032 - 256) / 64 + 2)
            $display("  PASS  最大单步 %0d，无阶跃", maxstep);
        else begin
            errs = errs + 1;
            $display("  FAIL  最大单步 %0d 太大", maxstep);
        end

        /* ---------- 判据 2：256 → 257（小步长必须到底） ---------- */
        $display("=== 判据 2：256 → 257，小步长也要落到目标 ===");
        t0 = 16'sd256; t1 = 16'sd256; t2 = 16'sd256; t3 = 16'sd256;
        for (i = 0; i < 600; i = i + 1) step_once;   /* 2032 → 256 要走满 ~310 步 */
        t0 = 16'sd257; t1 = 16'sd257; t2 = 16'sd257; t3 = 16'sd257;
        for (i = 0; i < 8; i = i + 1) step_once;
        if (c0 == 257 && !busy)
            $display("  PASS  8 个采样内到位，busy=0");
        else begin
            errs = errs + 1;
            $display("  FAIL  c0=%0d busy=%b", c0, busy);
        end

        /* ---------- 判据 3：clr 直接对齐 ---------- */
        $display("=== 判据 3：clr 直接对齐目标 ===");
        t0 = 16'sd16; t1 = 16'sd16; t2 = 16'sd16; t3 = 16'sd16;   /* −24 dB */
        step_once;
        if (c0 == 257) begin
            /* 正常：还只走了一步 */
        end
        clr = 1;
        @(posedge clk);
        #1;
        clr = 0;
        if (c0 == 16 && !busy)
            $display("  PASS  clr 后立刻等于 16（−24 dB），busy=0");
        else begin
            errs = errs + 1;
            $display("  FAIL  clr 后 c0=%0d busy=%b", c0, busy);
        end

        /* ---------- 附加：div 很大时也不许卡住 ---------- */
        $display("=== 附加：div=15（极慢）也必须收敛 ===");
        div = 16'd15;
        t0 = 16'sd2032; t1 = 16'sd2032; t2 = 16'sd2032; t3 = 16'sd2032;
        for (i = 0; i < 40000; i = i + 1) step_once;
        if (c0 == 2032 && !busy)
            $display("  PASS  div=15 也在 40000 个采样内到位");
        else begin
            errs = errs + 1;
            $display("  FAIL  div=15 时 c0=%0d busy=%b", c0, busy);
        end

        if (errs == 0) $display("=== 全部通过 ===");
        else           $display("=== 有失败 %0d 处 ===", errs);
        $finish;
    end

endmodule
