/* fir_stream_shell 的测试台 —— DMA 那版在综合前能验的全验掉
 *
 * 板上要走 axi_dma，这里就用"会说话的 AXI-Stream 主机/从机"代替：
 *
 *   · 主机：按 in_q15.mem 逐拍送采样，最后一个带 TLAST；只认 tvalid/tready，
 *           自己不节流（真正的节流来自前端的 din_ready）
 *   · 从机：两种模式 —— 永远 ready，和随机 ready（每拍 2/3 概率收）。
 *           第二遍专门用来验输出 FIFO：背压期间 tdata 不能变、不能丢、不能重。
 *
 * 验的断言（任何一条失败都让脚本非 0 退出）：
 *   ① 输出点数 == 输入点数，TLAST 恰好出现一次且在最后一拍
 *   ② 和黄金向量 golden/rtl_fe_out.txt 逐位相同
 *   ③ 高 16 位是低 16 位的符号扩展（PS 侧按 int32 读不会读错）
 *   ④ 随机背压下输出和常 ready 时**逐位相同**，拍数只会更多、不会更少
 *   ⑤ 连跑两块，第二块和第一块逐位相同（前端每块都被复位干净）
 *
 * 用法：run_xsim_stream.sh 负责把 in_q15.mem / coeffs_*.mem 摆进工作目录；
 *      黄金向量用 +GOLD=<文件> 传，缺省 rtl_fe_out.txt。
 */

`timescale 1ns / 1ps

module tb_fir_stream_shell;

    localparam integer MAXN = 8192;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    always #5 clk = ~clk;              // 100 MHz

    reg  [31:0] ctrl;
    wire [31:0] stat_lo, stat_hi;

    /* AXI-Stream 主机 */
    reg  [31:0] s_tdata  = 32'd0;
    reg         s_tvalid = 1'b0;
    wire        s_tready;
    reg         s_tlast  = 1'b0;

    /* AXI-Stream 从机 */
    wire [31:0] m_tdata;
    wire        m_tvalid;
    reg         m_tready = 1'b1;
    wire        m_tlast;

    fir_stream_shell #(.RST_CYC(8), .FIFO_AW(4), .FIFO_LIM(12)) dut (
        .clk(clk), .rst_n(rst_n),
        .ctrl(ctrl),
        .stat_lo(stat_lo), .stat_hi(stat_hi),
        .s_axis_tdata(s_tdata), .s_axis_tvalid(s_tvalid),
        .s_axis_tready(s_tready), .s_axis_tlast(s_tlast),
        .m_axis_tdata(m_tdata), .m_axis_tvalid(m_tvalid),
        .m_axis_tready(m_tready), .m_axis_tlast(m_tlast)
    );

    wire        done  = stat_hi[16];
    wire        busy  = stat_hi[17];
    wire        ovf   = stat_hi[18];
    wire [15:0] n_out = stat_hi[15:0];

    /* ---------------- 数据 ---------------- */
    reg signed [15:0] in_mem [0:MAXN-1];
    reg signed [15:0] gold   [0:MAXN-1];
    reg signed [15:0] cap_a  [0:MAXN-1];   // 第一遍（常 ready）
    reg        [31:0] cap32  [0:MAXN-1];   // 原始 32 位，用来查符号扩展
    reg signed [15:0] cap_b  [0:MAXN-1];   // 第二遍（随机背压）

    integer len, glen;
    integer f_rd, f_g, f_out;
    reg [8*1024-1:0] line;

    /* ---------------- 主机：把 in_mem 打成 AXI-Stream ---------------- */
    reg         feed_en = 1'b0;
    integer     idx     = 0;

    always @(negedge clk) begin
        if (!feed_en) begin
            s_tvalid <= 1'b0;
            s_tlast  <= 1'b0;
            s_tdata  <= 32'd0;
        end else if (s_tvalid && s_tready) begin
            /* 这一拍被收下了，换下一个采样（注意 idx 是非阻塞改的，
             * 下面必须用 idx+1 取数，不能用 idx） */
            if (idx + 1 < len) begin
                idx      <= idx + 1;
                s_tvalid <= 1'b1;
                s_tdata  <= {{16{in_mem[idx+1][15]}}, in_mem[idx+1]};
                s_tlast  <= ((idx + 1) == len - 1);
            end else begin
                idx      <= idx + 1;
                s_tvalid <= 1'b0;
                s_tlast  <= 1'b0;
            end
        end else if (!s_tvalid) begin
            if (idx < len) begin
                s_tvalid <= 1'b1;
                s_tdata  <= {{16{in_mem[idx][15]}}, in_mem[idx]};
                s_tlast  <= (idx == len - 1);
            end
        end
        /* s_tvalid && !s_tready：保持不动 —— 这是 AXI-Stream 的要求 */
    end

    /* ---------------- 从机：常 ready / 随机背压 ---------------- */
    reg bp_mode = 1'b0;
    integer seed = 32'h5EED;

    always @(negedge clk) begin
        if (!bp_mode) m_tready <= 1'b1;
        else          m_tready <= (($random(seed) & 32'h7FFFFFFF) % 3) != 0;  // 2/3 收
    end

    /* ---------------- 监视：收数、数拍、查符号扩展 ---------------- */
    integer n_cap = 0;
    integer n_tlast = 0;
    integer n_signerr = 0;
    integer n_stall = 0;      // m_tvalid && !m_tready 的拍数：用来确认背压真的发生了
    integer n_istall = 0;     // s_tvalid && !s_tready 的拍数：输入侧被 DUT 挡住

    always @(posedge clk) begin
        if (rst_n && m_tvalid && m_tready) begin
            if (n_cap < MAXN) begin
                cap32[n_cap]  = m_tdata;
                if (!bp_mode) cap_a[n_cap] = m_tdata[15:0];
                else          cap_b[n_cap] = m_tdata[15:0];
            end
            n_cap = n_cap + 1;
            if (m_tlast) begin
                n_tlast = n_tlast + 1;
                $display("[tb]   TLAST @%0t: stat_lo=%0d done=%0d n_out=%0d",
                         $time, stat_lo, done, n_out);
            end
            if (m_tdata[31:16] !== {16{m_tdata[15]}}) n_signerr = n_signerr + 1;
        end
        if (rst_n && m_tvalid && !m_tready) n_stall = n_stall + 1;
        if (rst_n && s_tvalid && !s_tready) n_istall = n_istall + 1;
    end

    /* ---------------- 跑一块 ---------------- */
    integer errs = 0;
    reg [31:0] cyc_a, cyc_b;

    task run_block;
        input  integer    n;
        input             rand_ready;
        output reg [31:0] cycles_out;
        integer guard;
        begin
            /* 等前端复位窗口过去（此时 tready 才可能拉高） */
            guard = 0;
            while (!s_tready && guard < 1000) begin
                @(negedge clk);
                guard = guard + 1;
            end

            n_cap   = 0;
            n_tlast = 0;
            n_stall = 0;
            n_istall = 0;
            bp_mode = rand_ready;
            idx     = 0;
            feed_en = 1'b1;

            guard = 0;
            while (n_cap < n && guard < 4000000) begin
                @(negedge clk);
                guard = guard + 1;
            end
            feed_en = 1'b0;

            if (n_cap < n) begin
                $display("[tb] ERROR: 超时，只收到 %0d / %0d 个输出", n_cap, n);
                errs = errs + 1;
            end
            repeat (4) @(negedge clk);
            cycles_out = stat_lo;
            bp_mode    = 1'b0;
        end
    endtask

    initial begin
        ctrl = 32'd0;
        if ($test$plusargs("DRC")) begin
            ctrl = 32'h1;                 // ctrl[0] = drc_en
            $display("[tb] 模式: 前端 + 四段增益 + WDRC");
        end else begin
            $display("[tb] 模式: 仅前端（bypass）");
        end

        f_rd = $fopen("in_q15.mem", "r");
        len = 0;
        while ($fgets(line, f_rd) != 0) len = len + 1;
        $fclose(f_rd);
        $display("[tb] 输入采样 %0d 个", len);
        if (len <= 0 || len > MAXN) begin
            $display("[tb] ERROR: 输入 %0d 行，超出 1..%0d", len, MAXN);
            $finish;
        end
        $readmemh("in_q15.mem", in_mem);

        if (!$value$plusargs("GOLD=%s", line)) line = "rtl_fe_out.txt";
        f_g = $fopen(line, "r");
        if (f_g == 0) begin
            $display("[tb] ERROR: 打不开黄金向量 %0s", line);
            $finish;
        end
        glen = 0;
        while ($fscanf(f_g, "%d\n", gold[glen]) == 1) glen = glen + 1;
        $fclose(f_g);
        $display("[tb] 黄金向量 %0d 个", glen);
        if (glen != len) begin
            $display("[tb] ERROR: 长度不一致 in=%0d gold=%0d", len, glen);
            errs = errs + 1;
        end

        repeat (20) @(posedge clk);
        rst_n = 1'b1;

        /* ---------- 第一遍：常 ready ---------- */
        run_block(len, 1'b0, cyc_a);
        $display("[tb] 第一遍: n_out=%0d overflow=%0d cycles=%0d (%.3f 拍/采样) 停顿=%0d",
                 n_cap, ovf, cyc_a, cyc_a * 1.0 / (len - 1), n_stall);
        if (ovf)         begin $display("[tb] ERROR: overflow 置位"); errs = errs + 1; end
        if (n_cap != len)begin $display("[tb] ERROR: 输出 %0d，应为 %0d", n_cap, len);
                            errs = errs + 1; end
        if (n_tlast != 1)begin $display("[tb] ERROR: TLAST 出现 %0d 次", n_tlast);
                            errs = errs + 1; end
        if (n_signerr)   begin $display("[tb] ERROR: %0d 个输出高 16 位不是符号扩展", n_signerr);
                            errs = errs + 1; end

        begin : cmp_golden
            integer i, bad;
            bad = 0;
            for (i = 0; i < len && i < glen; i = i + 1) begin
                if (cap_a[i] !== gold[i]) begin
                    if (bad < 5)
                        $display("[tb] ERROR: 第 %0d 点 dut=%0d golden=%0d",
                                 i, cap_a[i], gold[i]);
                    bad = bad + 1;
                end
            end
            $display("[tb] 和黄金向量逐位: %0d / %0d 不同", bad, len);
            if (bad != 0) errs = errs + 1;
        end

        /* 落盘，交给 Python 再看一遍（和 block 版脚本同一套） */
        f_out = $fopen("stream_out.txt", "w");
        begin : dump
            integer i;
            for (i = 0; i < len; i = i + 1) $fwrite(f_out, "%0d\n", cap_a[i]);
        end
        $fclose(f_out);

        /* ---------- 第二遍：随机背压，同一批输入 ---------- */
        run_block(len, 1'b1, cyc_b);
        $display("[tb] 第二遍(背压): n_out=%0d overflow=%0d cycles=%0d 输出停顿=%0d 输入停顿=%0d",
                 n_cap, ovf, cyc_b, n_stall, n_istall);
        if (ovf)          begin $display("[tb] ERROR: 背压下 overflow"); errs = errs + 1; end
        if (n_cap != len) begin $display("[tb] ERROR: 背压输出 %0d，应为 %0d", n_cap, len);
                             errs = errs + 1; end
        if (n_tlast != 1) begin $display("[tb] ERROR: 背压下 TLAST 出现 %0d 次", n_tlast);
                             errs = errs + 1; end
        if (cyc_b < cyc_a) begin
            $display("[tb] ERROR: 背压反而更快 %0d < %0d", cyc_b, cyc_a);
            errs = errs + 1;
        end
        begin : cmp_bp
            integer i, bad;
            bad = 0;
            for (i = 0; i < len; i = i + 1) begin
                if (cap_b[i] !== cap_a[i]) begin
                    if (bad < 5)
                        $display("[tb] ERROR: 背压第 %0d 点 %0d，常 ready 是 %0d",
                                 i, cap_b[i], cap_a[i]);
                    bad = bad + 1;
                end
            end
            $display("[tb] 常 ready vs 背压: %0d / %0d 不同", bad, len);
            if (bad != 0) errs = errs + 1;
        end

        if (errs == 0) $display("[tb] STREAM PASS");
        else           $display("[tb] STREAM FAIL (%0d)", errs);
        $display("[tb] DONE");
        $finish;
    end

endmodule
