/* tb_drc_chan —— 单段压缩器 vs HLS 公式（行为模型对拍）
 *
 * 2026-10-28 批 2：thr / ratio / atk / rel 从编译期 localparam 改成运行期端口。
 * 判据不能再只靠"和改动前的 RTL 逐位相同"（参数都能变了，那条只能覆盖出厂值），
 * 所以这里**直接和 HLS 的式子对**：
 *
 *   模式 A  atk≠0 或 rel≠0  -> HLS 的 wdrc()：包络一阶平滑 + 静态曲线减 gr
 *   模式 B  atk==0 且 rel==0 -> HLS 的 drc()：无记忆，每个采样独立压
 *   模式 C  ratio=1.0        -> 压缩器不动作，输出必须**逐位等于输入**
 *            （这一条不靠模型，是可证的死判据：也证明端口真的驱动了通路）
 *
 * 模式 B 在 RTL 里不是另一条数据路，而是把包络更新改成"写瞬时幅度"。
 * 闭合形式上：
 *     RTL  y = |x| − floor(d),  d = (|x|−thr)(1−ratio)
 *     HLS  y = floor(thr + (|x|−thr)·ratio) = |x| − ceil(d)
 * 两者最多差 1 个 LSB（d 是整数时相等），所以模式 B 按 ±1 判。
 * 模式 A 是逐步同构的，按 0 失配判。
 *
 * 行为模型的定点语义逐拍照抄 wr dc()/drc()（drc_t = 24 位 Q2.22，
 * 每次赋值向负无穷取整、饱和），和 RTL 的流水级一一对应：
 *   级 0  band>>10 饱和   级 1  ×gain>>8 饱和   级 2  |x|
 *   级 3  env 更新         级 5  env−thr        级 6/7  gr 乘 + 饱和
 *   级 8  |x|−gr 钳零、贴符号
 */
`timescale 1ns / 1ps

module tb_drc_chan;

    localparam integer DW  = 24;
    localparam integer SHA = 10;      // Q8.32 -> Q2.22
    localparam integer FRC = 22;
    localparam integer NMAX = 4096;

    reg clk = 0, rst = 1, in_vld = 0;
    reg signed [39:0] band;
    reg signed [15:0] gain;
    reg signed [23:0] thr, ratio, atk, rel;

    wire signed [23:0] yv, env_o, gr_o;
    wire               yv_vld;

    fir_drc4_chan #(.BW(40), .DW(DW)) dut (
        .clk(clk), .rst(rst), .in_vld(in_vld), .band(band),
        .gain_i(gain), .thr_i(thr), .ratio_i(ratio),
        .atk_i(atk), .rel_i(rel),
        .yv(yv), .yv_vld(yv_vld), .env_o(env_o), .gr_o(gr_o)
    );

    always #5 clk = ~clk;

    /* ---------------- 行为模型 ---------------- */

    function signed [DW-1:0] satd;
        input signed [47:0] v;
        begin
            if (v > 48'sd8388607)       satd = 24'sh7FFFFF;
            else if (v < -48'sd8388608) satd = 24'sh800000;
            else                        satd = v[DW-1:0];
        end
    endfunction

    reg signed [DW-1:0] m_env;
    reg signed [DW-1:0] exp_mem [0:NMAX-1];
    reg signed [DW-1:0] exp_grm [0:NMAX-1];
    integer             n_fed;

    /* x 就是 |给定| 的 drc_t 输入。band 按 x<<10 构造，所以级 0 是恒等，
     * 模型从级 1（×gain）开始算。 */
    task automatic model(input signed [DW-1:0] x);
        reg signed [39:0]   gmul;
        reg signed [47:0]   gse, gsh;
        reg signed [DW-1:0] gb1, a, inc_c;
        reg signed [24:0]   diff, ex;
        reg signed [DW-1:0] step, enp;
        reg signed [48:0]   prod, grfull;
        /* 移位结果必须先落到 signed 线再参与加法。写成
         *   satd({{24{m_env[DW-1]}}, m_env} + (prod >>> FRC))
         * 时，拼接是无符号的，整个加法被当成无符号 —— `>>>` 就变成逻辑右移，
         * prod 为负时 −51 会算成 +67,108,813，包络一步顶到饱和值。
         * 现象：a < env（release 段）的第一个采样之后，包络永远停在 8388607。
         * 这和 RTL 顶注释里记的坑是同一个。 */
        reg signed [48:0]   inc_s;
        reg signed [23:0]   omr;
        reg signed [DW-1:0] gr, a2r;
        reg                 neg, epos;
        begin
            /* 级 1：× 增益 */
            gmul = x * gain;
            gse  = {{8{gmul[39]}}, gmul};
            gsh  = gse >>> 8;
            gb1  = satd(gsh);
            /* 级 2：|x|。（拼接是无符号的，先落到 signed 再取负） */
            neg = gb1[DW-1];
            a   = neg ? satd(-{{24{gb1[DW-1]}}, gb1}) : gb1;
            /* 级 3/4：包络。atk==rel==0 -> 瞬时幅度（对应 HLS 的 drc()） */
            if ((atk != 24'sd0) || (rel != 24'sd0)) begin
                diff = {{1{a[DW-1]}}, a} - {{1{m_env[DW-1]}}, m_env};
                step = (diff > 0) ? atk : rel;
                prod = diff * step;
                inc_s = (prod >>> FRC);          // 先落到 signed 线，别再让它被拼接拖成无符号
                inc_c = satd({{24{m_env[DW-1]}}, m_env} + inc_s);
                enp   = (inc_c < 0) ? {DW{1'b0}} : inc_c;
                m_env = enp;
            end else begin
                m_env = a;
            end
            /* 级 5 */
            ex   = {{1{m_env[DW-1]}}, m_env} - {{1{thr[DW-1]}}, thr};
            epos = (m_env > thr);
            /* 级 6/7 */
            omr    = (24'sd1 <<< FRC) - ratio;
            grfull = ex * omr;
            gr     = epos ? satd(grfull >>> FRC) : 24'sd0;
            /* 级 8 */
            a2r = {{1{a[DW-1]}}, a} - {{1{gr[DW-1]}}, gr};
            if (a2r < 0) a2r = 24'sd0;
            exp_mem[n_fed] = neg ? -a2r : a2r;
            exp_grm[n_fed] = gr;
            n_fed = n_fed + 1;
        end
    endtask

    /* ---------------- 采样并比较 ---------------- */

    integer out_idx, errs, n_out, tol;
    reg signed [DW-1:0] gr_d;

    always @(posedge clk) gr_d <= gr_o;

    always @(negedge clk) begin
        if (!rst && yv_vld) begin
            if (yv > exp_mem[out_idx] + tol || yv < exp_mem[out_idx] - tol) begin
                errs = errs + 1;
                if (errs <= 8)
                    $display("  失配 y[%0d]: rtl=%0d 模型=%0d (env_o=%0d gr_o=%0d)",
                             out_idx, $signed(yv), $signed(exp_mem[out_idx]),
                             $signed(env_o), $signed(gr_o));
            end
            /* gr_o 比 yv 早一拍（p_gr 在级 7，yv 在级 8），所以比 gr_d */
            if (gr_d !== exp_grm[out_idx]) begin
                errs = errs + 1;
                if (errs <= 8)
                    $display("  失配 gr[%0d]: rtl=%0d 模型=%0d",
                             out_idx, $signed(gr_d), $signed(exp_grm[out_idx]));
            end
            out_idx = out_idx + 1;
            n_out   = n_out   + 1;
        end
    end

    /* ---------------- 激励 ---------------- */

    integer seed = 32'h0BADF00D;
    integer i, k;
    reg signed [23:0] xs;
    reg signed [23:0] sig [0:NMAX-1];

    /* 一次喂一个采样：band = x<<10（Q2.22 的 x），间隔 3 拍（和前端同节拍）。
     *
     * 激励**必须从负沿给**。最早写成「in_vld=1; @(posedge clk); in_vld=0;」，
     * 清 in_vld 和 DUT 的 always @(posedge) 采样落在同一个时间步里，谁先执行
     * 由仿真器决定：偶尔 DUT 看到的是已经清掉的 0，那一个采样被吞掉，之后
     * 所有输出整体错一位（现象是 rtl[i] 恒等于 model[i-1]，看着像流水级数
     * 算错了）。从负沿给就和正沿隔了半个周期，不会再撞。
     *
     * 模型在喂的同一拍更新；RTL 在自己的流水里更新同一个采样的包络。
     * 间隔取 5 拍：RTL 流水 10 级、包络在 t+6 落定，间隔必须 > 该延迟才不会
     * 让下一个采样的差值用上还没更新的包络（2026-10-28 批 2 多了一级流水，
     * 原来写的 3 拍正好卡在旧版的边界上）。 */
    task automatic feed(input signed [DW-1:0] x);
        begin
            model(x);
            @(negedge clk);
            band   = ({{16{x[DW-1]}}, x}) <<< SHA;
            in_vld = 1'b1;
            @(negedge clk);
            in_vld = 1'b0;
            @(negedge clk);
        end
    endtask

    task automatic do_reset;
        begin
            @(negedge clk);
            rst    = 1'b1;
            in_vld = 1'b0;
            repeat (3) @(negedge clk);
            rst     = 1'b0;
            m_env   = 24'sd0;
            n_fed   = 0;
            out_idx = 0;
            n_out   = 0;
            errs    = 0;
            repeat (2) @(negedge clk);
        end
    endtask

    task automatic make_sig(input integer n);
        begin
            for (i = 0; i < n; i = i + 1) begin
                xs = $random(seed) % 4194304;      // ±1.0 的 Q2.22 范围
                sig[i] = xs;
            end
        end
    endtask

    task automatic play(input integer n);
        begin
            for (k = 0; k < n; k = k + 1) feed(sig[k]);
            repeat (16) @(negedge clk);
        end
    endtask

    integer errs_a, k_passthru_bad;

    /* 一组参数跑一遍。WDRC（atk|rel != 0）按 0 失配判，无记忆按 ±1 判。
     * 抽出来是为了后面的参数扫描 —— 报告里那张对拍表就是这一串。 */
    task automatic one_case(input signed [23:0] t_, r_, k_, e_,
                            input integer tol_, p_n, input [255:0] label);
        begin
            thr = t_; ratio = r_; atk = k_; rel = e_; tol = tol_;
            do_reset;
            play(p_n);
            if (errs == 0)
                $display("  PASS  %0s  (thr=%0d ratio=%0d atk=%0d rel=%0d, %0d 采样, tol=%0d)",
                         label, t_, r_, k_, e_, n_out, tol_);
            else
                $display("  FAIL  %0s  %0d 处失配 (thr=%0d ratio=%0d atk=%0d rel=%0d)",
                         label, errs, t_, r_, k_, e_);
            errs_a = errs_a + errs;
        end
    endtask

    initial begin
        band = 0; gain = 16'sd256;
        thr = 24'sd419456; ratio = 24'sd2936064;
        atk = 24'sd17408;  rel = 24'sd896;
        errs = 0; n_out = 0; out_idx = 0; n_fed = 0;

        make_sig(1200);

        /* ---------- 模式 A：wdrc()，按 0 失配判 ---------- */
        $display("=== 模式 A：atk=17408 rel=896，对 HLS wdrc() ===");
        tol = 0;
        do_reset;
        play(1200);
        errs_a = errs;
        if (errs == 0 && n_out > 1180)
            $display("  PASS  %0d 个采样全部逐位一致", n_out);
        else
            $display("  FAIL  %0d 个采样里 %0d 处失配（应为 0）", n_out, errs);

        /* ---------- 模式 B：drc()（无记忆），按 ±1 判 ---------- */
        $display("=== 模式 B：atk=rel=0，对 HLS drc()（±1 LSB）===");
        tol = 1;
        atk = 24'sd0; rel = 24'sd0;
        do_reset;
        play(1200);
        if (errs == 0 && n_out > 1180)
            $display("  PASS  %0d 个采样，与 drc() 的差不超过 1 LSB", n_out);
        else
            $display("  FAIL  %0d 个采样里 %0d 处超过 1 LSB", n_out, errs);
        errs_a = errs_a + errs;

        /* ---------- 模式 C：ratio=1.0 -> 输出逐位等于输入 ---------- */
        $display("=== 模式 C：thr=0.5 ratio=1.0，输出应逐位等于输入 ===");
        tol = 0;
        atk   = 24'sd17408;
        rel   = 24'sd896;
        thr   = 24'sd2097152;    // 0.5
        ratio = 24'sd4194304;    // 1.0 = 不压
        do_reset;
        play(600);
        k_passthru_bad = 0;
        for (k = 0; k < 600; k = k + 1)
            if (exp_mem[k] !== sig[k]) k_passthru_bad = k_passthru_bad + 1;
        if (errs == 0 && k_passthru_bad == 0 && n_out > 580)
            $display("  PASS  %0d 个采样：模型=输入，RTL=模型", n_out);
        else
            $display("  FAIL  模型/输入不符 %0d 处，RTL/模型不符 %0d 处",
                     k_passthru_bad, errs);
        errs_a = errs_a + errs + k_passthru_bad;

        /* ---------- 模式 D：参数扫描 ----------
         * 上面三条各只覆盖一组参数。这里换 7 组，覆盖：
         *   ① 出厂值  ② 高门限（永不压缩）③ 低门限 + 大比率  ④ 快攻慢放
         *   ⑤ 慢攻快放  ⑥ 无记忆 + 不同门限  ⑦ ratio 极小（几乎限幅）
         * 每组都对比行为模型；WDRC 要逐位一致，无记忆按 ±1。 */
        $display("=== 模式 D：参数扫描（7 组）===");
        one_case(24'sd419456,  24'sd2936064, 24'sd17408, 24'sd896,   0, 400, "factory");
        one_case(24'sd3000000, 24'sd2936064, 24'sd17408, 24'sd896,   0, 400, "high-thr");
        one_case(24'sd1048576, 24'sd3774873, 24'sd17408, 24'sd896,   0, 400, "low-thr+big-ratio");
        one_case(24'sd2097152, 24'sd2097152, 24'sd65536, 24'sd256,   0, 400, "fast-atk/slow-rel");
        one_case(24'sd2097152, 24'sd2097152, 24'sd256,   24'sd65536, 0, 400, "slow-atk/fast-rel");
        one_case(24'sd1048576, 24'sd2936064, 24'sd0,     24'sd0,     1, 400, "memoryless");
        one_case(24'sd419430,  24'sd4190208, 24'sd17408, 24'sd896,   0, 400, "ratio~1/32");

        if (errs_a == 0) $display("=== 全部通过 ===");
        else             $display("=== 有失配 ===");
        $finish;
    end

    initial begin
        #2000000;
        $display("TIMEOUT");
        $finish;
    end

endmodule
