/* tb_pl_channel —— 验「每帧只喂一个采样、每帧吐两个字」这条比例（2026-10-27）
 *
 * 背景：i2s_duplex 的接收侧每帧写两个字（L、R）。原来 fir_pl_direct 把每个字
 * 都当采样喂进前端，于是前端实际跑在 97.7 kHz、四个分频点全部翻倍。
 * 修法是在 fir_pl_direct 里按字翻转取一个（L 进前端、R 弹掉），
 * 输出侧再把一个采样写两遍补回立体声。
 *
 * 这份 tb 不看数值，只看比例：
 *
 *   帧数（LRCLK 上升沿）   ≈ 前端吃进的采样数（fe_din_vld）
 *   前端吐出的采样数 × 2  ≈ 播放侧取走的字数（ply_tvalid & tready）
 *
 * 判据：① fed 与 frames 差 ≤ 3；② ply 与 2×frames 差 ≤ 8；
 *       ③ 后半段 und 不再增长（稳态不再断流）；④ dup_drop 恒 0。
 *
 *   bash sim/rtl_sim/run_xsim_pl_ch.sh
 *
 * sdata_i 用 1 kHz 方波（不与 BCLK 对齐）—— 内容对这份判据没意义，
 * 只关心"一帧一个字"这条节拍。
 */

`timescale 1ns / 1ps

module tb_pl_channel;

    reg clk   = 1'b0;
    reg rst_n = 1'b0;
    always #5 clk = ~clk;              // 100 MHz

    reg  [31:0] ctrl = 32'd0;
    wire [31:0] stat0, stat1, stat2, dbg0, dbg1;
    wire        bclk, lrclk, sdata_o;
    reg         sdata_i = 1'b0;

    fir_pl_direct #(.TX_TARGET(128)) dut (
        .clk     (clk),
        .rst_n   (rst_n),
        .ctrl    (ctrl),
        .stat0   (stat0),
        .stat1   (stat1),
        .stat2   (stat2),
        .dbg0    (dbg0),
        .dbg1    (dbg1),
        .bclk    (bclk),
        .lrclk   (lrclk),
        .sdata_o (sdata_o),
        .sdata_i (sdata_i)
    );

    /* sdata_i：1 kHz 方波，纯当"有数据在动" */
    reg [15:0] div = 16'd0;
    always @(posedge clk) begin
        div <= div + 16'd1;
        if (div == 16'd0) sdata_i <= ~sdata_i;      // 100MHz/65536 ≈ 1.5 kHz
    end

    /* 统计窗口 */
    reg     en    = 1'b0;
    integer frames = 0;
    integer fed    = 0;
    integer ply    = 0;
    integer und_10 = 0;
    integer und_end= 0;
    integer drop   = 0;

    reg lr_d = 1'b0;
    always @(posedge clk) begin
        lr_d <= lrclk;
        if (en) begin
            if (lrclk && !lr_d)                  frames  <= frames + 1;
            if (dut.fe_din_vld)                  fed     <= fed + 1;
            if (dut.ply_tvalid && dut.ply_tready) ply    <= ply + 1;
            if (dut.dup_drop)                    drop    <= drop + 1;
        end
    end

    integer f0, fd0, pl0;

    initial begin
        $display("=== tb_pl_channel：一帧一个采样 / 一帧两个字 ===");
        #100 rst_n = 1'b1;

        #100000;                     // 100 us：让系数表读完、前端脱离复位
        ctrl = 32'd4;                // flush，run=0
        #1000;
        ctrl = 32'd1;                // 放行
        #2000;

        /* 从这里开始计数 */
        frames = 0; fed = 0; ply = 0; drop = 0;
        en = 1'b1;

        #10000000;                   // 10 ms
        und_10 = stat0[15:0];
        $display("  +10 ms: frames=%0d fed=%0d ply=%0d und=%0d", frames, fed, ply, und_10);

        #10000000;                   // 再 10 ms
        en = 1'b0;
        und_end = stat0[15:0];

        $display("  +20 ms: frames=%0d fed=%0d ply=%0d und=%0d drop=%0d",
                 frames, fed, ply, und_end, drop);
        $display("         每帧采样数 fed/frames = %0.3f （要 1.000，原来是 2.000）",
                 fed * 1.0 / (frames == 0 ? 1 : frames));
        $display("         每帧字数    ply/frames = %0.3f （要 2.000）",
                 ply * 1.0 / (frames == 0 ? 1 : frames));

        if (frames < 900) begin
            $display("FAIL 帧数太少（%0d），仿真没跑起来", frames);
        end else if ((fed - frames > 3) || (frames - fed > 3)) begin
            $display("FAIL 前端吃进的采样数和帧数不等：fed=%0d frames=%0d（差 %0d）",
                     fed, frames, fed - frames);
        end else if ((ply - 2 * frames > 8) || (2 * frames - ply > 8)) begin
            $display("FAIL 播放侧字数不是两倍帧数：ply=%0d 2*frames=%0d", ply, 2 * frames);
        end else if (und_end != und_10) begin
            $display("FAIL 后半段还在断流：und %0d -> %0d", und_10, und_end);
        end else if (drop != 0) begin
            $display("FAIL 有成对写入被丢：dup_drop=%0d", drop);
        end else begin
            $display("PASS 一帧一个采样、一帧两个字，稳态 und 不涨");
        end

        $display("[done]");
        $finish;
    end

    /* 卡死保护 */
    initial begin
        #40000000;
        $display("FAIL 超时 40 ms 没跑完");
        $finish;
    end

endmodule
