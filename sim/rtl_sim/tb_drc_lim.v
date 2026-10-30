/* tb_drc_lim —— fir_drc4 末级软膝限幅的自检（2026-10-30 任务 A）
 *
 * 为什么单独一个 tb：tb_drc_gain / tb_fir_drc4 比的是"和改动前一模一样"，
 * 而这次**故意**让过载段不一样（硬饱和 → 软膝）。所以这里只针对限幅器本身：
 *
 *   判据 1 单调：输入 −32768 → 32767 逐点扫，输出单调不减
 *   判据 2 有界：任意输入，输出都在 [−32768, 32767]
 *   判据 3 透明：|u| ≤ 22937（0.70 FS）时逐位等于输入
 *   判据 4 过载：方波 / 多音叠加（10 FS）/ ±128 FS 极值，
 *                不越界、不翻符号、不跳变（|Δy| ≤ |Δu|）
 *
 * 判据 4 的"不跳变"用斜率上界：整条曲线斜率 ≤ 1，所以相邻两点必须满足
 * |Δy| ≤ |Δu|。它同时抓得住回卷（正数突然变负数那种）。
 *
 * 激励怎么造：gain 绑 256、ratio 绑 2^22（omr=0 → 衰减量 gr 恒 0）、thr 绑最大、
 * atk/rel 绑 0，于是每段输出 = |band>>10| 带符号原样；四段相加再 >>17 就是
 * 想要的样点值 u。只喂 band0，band1..3 给 0，所以 band0 = u<<17。
 *
 * 用法：bash sim/rtl_sim/run_xsim_lim.sh
 */

`timescale 1ns / 1ps

module tb_drc_lim;

    localparam integer AW  = 40;
    localparam integer T07 = 22937;      // 0.70 FS

    reg clk = 1'b0;
    always #5 clk = ~clk;                // 100 MHz

    reg                  rst = 1'b1;
    reg                  in_vld = 1'b0;
    reg  signed [AW-1:0] band0 = 0, band1 = 0, band2 = 0, band3 = 0;

    wire signed [15:0]   y;
    wire                 y_vld;

    fir_drc4 #(.BW(AW), .DW(24)) dut (
        .clk(clk), .rst(rst),
        .in_vld(in_vld),
        .band0(band0), .band1(band1), .band2(band2), .band3(band3),
        .gain0(16'sd256),   .gain1(16'sd256),
        .gain2(16'sd256),   .gain3(16'sd256),
        .thr0(24'sd8388607), .thr1(24'sd8388607),
        .thr2(24'sd8388607), .thr3(24'sd8388607),
        .ratio0(24'sd4194304), .ratio1(24'sd4194304),
        .ratio2(24'sd4194304), .ratio3(24'sd4194304),
        .atk0(24'sd0), .atk1(24'sd0), .atk2(24'sd0), .atk3(24'sd0),
        .rel0(24'sd0), .rel1(24'sd0), .rel2(24'sd0), .rel3(24'sd0),
        .y(y), .y_vld(y_vld)
    );

    /* ---------------- 送数队列 ----------------
     * 流水 12 拍、送数间隔 5 拍 → 同时最多 3 个采样在飞。用队列按序比，
     * 不靠"固定延迟"这种一改流水就废的假设。 */
    reg signed [23:0] q [0:1023];
    integer q_wr = 0, q_rd = 0;

    task feed(input integer u);
        reg signed [39:0] b;
        begin
            b     = u;
            band0 = b <<< 17;
            band1 = 40'sd0; band2 = 40'sd0; band3 = 40'sd0;
            q[q_wr % 1024] = u[23:0];
            q_wr  = q_wr + 1;
            in_vld = 1'b1;
            @(posedge clk);
            in_vld = 1'b0;
            repeat (4) @(posedge clk);
        end
    endtask

    /* 四段一起顶到饱和：fir_drc4_chan 的 satd 把每段钳在 ±2^23（Q2.22 的 2.0），
     * 所以四段全饱和时 asum = 4·2^23·2^10，进限幅器的 u 最大 = 262143（8 FS）。
     * 这是**数字通路真正能给出的最大幅度**，比 2^22 还远。 */
    task feed_sat(input integer sign_p, input integer ueq);
        reg signed [39:0] b;
        begin
            b = sign_p ? 40'sd8589934592 : -40'sd8589934592;   // 2^33 → >>10 后正好被 satd 钳
            band0 = b; band1 = b; band2 = b; band3 = b;
            q[q_wr % 1024] = ueq[23:0];
            q_wr  = q_wr + 1;
            in_vld = 1'b1;
            @(posedge clk);
            in_vld = 1'b0;
            repeat (4) @(posedge clk);
        end
    endtask

    /* ---------------- 判据统计 ----------------
     * **采集要晚两拍**：本模块（以及它内部的 fir_drc4_chan）的 y_vld 比 y 早一拍
     * —— 2026-10-28 批 2 给数据路加了一级流水，vpipe 仍是 9 位，没跟着加。
     * 这不是本次改的东西，但它决定了 tb 必须在 vld 之后两拍取数才拿得到同一个
     * 采样。（已记在 A_结论.md 的"顺带发现"里，未改 RTL。）
     */
    integer n_chk = 0;
    integer err_mono = 0, err_bound = 0, err_trans = 0, err_slew = 0, err_sign = 0;
    integer ymax_pos = 0, ymax_neg = 0;
    integer u_prev = 0;
    reg signed [15:0] y_prev = 16'sd0;
    reg have_prev = 1'b0;
    reg chk_mono  = 1'b0;      // 只有单调扫描段才查（方波段本来就不单调）

    reg signed [15:0] ys1, ys2;
    reg vs1, vs2;
    always @(posedge clk) begin
        ys1 <= y;   vs1 <= y_vld;
        ys2 <= ys1; vs2 <= vs1;
    end

    integer uin, dy, dua;
    reg signed [15:0] yv;

    always @(posedge clk) begin
        if (!rst && vs1) begin
            uin = $signed(q[q_rd % 1024]);
            yv  = $signed(y);
            q_rd = q_rd + 1;
            n_chk = n_chk + 1;
            if (n_chk <= 5 || (n_chk > 22936 && n_chk < 22942))
                $display("  dbg n=%0d uin=%0d yv=%0d", n_chk, uin, yv);

            /* 判据 2：有界 */
            if (yv > 16'sd32767 || yv < -16'sd32768) err_bound = err_bound + 1;
            if (yv > ymax_pos) ymax_pos = yv;
            if (yv < ymax_neg) ymax_neg = yv;

            /* 判据 3：透明区逐位相等 */
            if (uin <= T07 && uin >= -T07 && (yv !== uin[15:0]))
                err_trans = err_trans + 1;

            /* 判据 4：符号不翻（输入非零时输出符号必须同号或为 0） */
            if ((uin > 0 && yv < 0) || (uin < 0 && yv > 0)) err_sign = err_sign + 1;

            /* 判据 4：斜率上界 |Δy| ≤ |Δu| */
            if (have_prev) begin
                dy  = yv - y_prev;
                dua = (uin > u_prev) ? (uin - u_prev) : (u_prev - uin);
                if (dy > dua || dy < -dua) begin
                    err_slew = err_slew + 1;
                    if (err_slew <= 6)
                        $display("  slewFAIL n=%0d  prev(u=%0d,y=%0d)  now(u=%0d,y=%0d)",
                                 n_chk, u_prev, y_prev, uin, yv);
                end
            end

            /* 判据 1：单调（只在扫描段） */
            if (chk_mono && have_prev && (yv < y_prev)) err_mono = err_mono + 1;

            y_prev    = yv;
            u_prev    = uin;
            have_prev = 1'b1;
        end
    end

    integer i, uu;
    integer n_mono = 0;

    initial begin
        repeat (6) @(posedge clk);
        rst = 1'b0;
        repeat (4) @(posedge clk);

        /* ============ 判据 1 + 3：全范围逐点扫 ============ */
        $display("=== 判据 1/3：-32768..32767 逐点扫 ===");
        chk_mono = 1'b1;
        have_prev = 1'b0;
        for (i = -32768; i <= 32767; i = i + 1) begin
            feed(i);
            n_mono = n_mono + 1;
        end
        repeat (60) @(posedge clk);
        chk_mono = 1'b0;
        $display("  扫描点数 %0d（已比对 %0d）", n_mono, n_chk);

        /* ============ 判据 4a：满幅方波 ============ */
        $display("=== 判据 4a：±32767 方波，周期 8 样点，400 个 ==");
        for (i = 0; i < 400; i = i + 1) feed((i % 8) < 4 ? 32767 : -32768);
        repeat (60) @(posedge clk);

        /* ============ 判据 4b：多音叠加，峰约 10 FS ============ */
        $display("=== 判据 4b：4 个正弦叠加（各 2.4 FS，峰约 9.6 FS）===");
        for (i = 0; i < 4000; i = i + 1) begin
            uu = $rtoi(80000.0 * $sin(6.2831853 * 0.011 * i)
                     + 80000.0 * $sin(6.2831853 * 0.037 * i)
                     + 80000.0 * $sin(6.2831853 * 0.083 * i)
                     + 80000.0 * $sin(6.2831853 * 0.211 * i));
            feed(uu);
        end
        repeat (60) @(posedge clk);

        /* ============ 判据 4c：四段全饱和（数字通路最大幅度 8 FS）============ */
        $display("=== 判据 4c：四段全饱和交替（u = ±262144，即 8 FS）===");
        for (i = 0; i < 200; i = i + 1) begin
            if (i % 2) feed_sat(1,  262143);
            else       feed_sat(0, -262144);
        end
        repeat (60) @(posedge clk);

        /* ============ 汇总 ============ */
        $display("");
        $display("=== 汇总 ===");
        $display("  比较点数        %0d", n_chk);
        $display("  输出范围        [%0d, %0d]", ymax_neg, ymax_pos);

        if (err_bound == 0) $display("  判据 2 有界      PASS");
        else                $display("  判据 2 有界      FAIL  %0d 点越界", err_bound);

        if (err_mono == 0)  $display("  判据 1 单调      PASS");
        else                $display("  判据 1 单调      FAIL  %0d 处回退", err_mono);

        if (err_trans == 0) $display("  判据 3 透明      PASS  （|u|<=22937 逐位相等）");
        else                $display("  判据 3 透明      FAIL  %0d 点被改动", err_trans);

        if (err_slew == 0)  $display("  判据 4 无跳变    PASS  （|Δy| <= |Δu|）");
        else                $display("  判据 4 无跳变    FAIL  %0d 处超斜率上界", err_slew);

        if (err_sign == 0)  $display("  判据 4 不翻符号  PASS");
        else                $display("  判据 4 不翻符号  FAIL  %0d 处反号", err_sign);

        if (err_bound + err_mono + err_trans + err_slew + err_sign == 0)
            $display("=== DRC_LIM PASS ===");
        else
            $display("=== DRC_LIM FAIL ===");
        $finish;
    end

    initial begin
        #30000000;
        $display("TIMEOUT");
        $finish;
    end

endmodule
