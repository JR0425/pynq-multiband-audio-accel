/* fir_rtl_shell 的测试台 —— 在综合前把外壳的状态机、地址换算、块间复位验掉
 *
 * 板上的东西在这里全都能验，只有"PS 的 MMIO 写"换成了 TB 里的 $readmemh/循环：
 *   · LOAD 段的读延迟是否等够（BRAM_PORTB 是 1 拍，这里照 1 拍建模）
 *   · 字节地址换算（字下标 k → 地址 k<<2）对不对
 *   · RUN 段是不是 3 拍/采样（total_cycles / (n-1)）
 *   · DRAIN 段是不是把最后一个采样也吐出来了（n_out == n）
 *   · **连跑两块，第二块输出必须和第一块逐位相同** —— 这一条专门验 S_RST，
 *     没有它的话延迟线里还留着上一块最后 195 个采样，输出会整体偏移。
 *
 * 用法：run_xsim_shell.sh 负责把 in_q15.mem / coeffs_*.mem 摆进工作目录。
 */

`timescale 1ns / 1ps

/* ---------------- 行为级 BRAM 端口（照 blk_mem_gen 的 B 口） ----------------
 * 同步读、1 拍延迟、读优先于写（本设计两者不会同拍）。
 * ADDR 是**字**地址（板上直接连 addrb）。 */
module bram_port_model #(
    parameter integer AW    = 13,
    parameter integer DEPTH = 8192
) (
    input  wire          clk,
    input  wire          en,
    input  wire [3:0]    we,
    input  wire [AW-1:0] addr,
    input  wire [31:0]   din,
    output reg  [31:0]   dout
);

    reg [31:0] mem [0:DEPTH-1];
    wire [AW-1:0] widx = addr;

    always @(posedge clk) begin
        if (en) begin
            if (we != 4'b0000) begin
                if (we[0]) mem[widx][7:0]   <= din[7:0];
                if (we[1]) mem[widx][15:8]  <= din[15:8];
                if (we[2]) mem[widx][23:16] <= din[23:16];
                if (we[3]) mem[widx][31:24] <= din[31:24];
            end else begin
                dout <= mem[widx];
            end
        end
    end

endmodule


module tb_fir_rtl_shell;

    localparam integer AW   = 13;
    localparam integer MAXN = 8192;   // 块长按输入文件行数，上限 MAXN

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    always #5 clk = ~clk;      // 100 MHz

    reg  [31:0] ctrl;
    wire [31:0] stat_lo, stat_hi;

    /* 输入 BRAM 端口 */
    wire          in_en;
    wire [3:0]    in_we;
    wire [AW-1:0] in_addr;
    wire [31:0]   in_din, in_dout;
    /* 输出 BRAM 端口 */
    wire          out_en;
    wire [3:0]    out_we;
    wire [AW-1:0] out_addr;
    wire [31:0]   out_din, out_dout;

    bram_port_model #(.AW(AW), .DEPTH(8192)) u_in (
        .clk(clk), .en(in_en), .we(in_we), .addr(in_addr),
        .din(in_din), .dout(in_dout)
    );
    bram_port_model #(.AW(AW), .DEPTH(8192)) u_out (
        .clk(clk), .en(out_en), .we(out_we), .addr(out_addr),
        .din(out_din), .dout(out_dout)
    );

    fir_rtl_shell #(.DW(32), .AW(AW), .MAXN(8192), .RD_WAIT(2), .RST_CYC(8)) dut (
        .clk(clk), .rst_n(rst_n),
        .ctrl(ctrl),
        .stat_lo(stat_lo), .stat_hi(stat_hi),
        .bram_in_clk(clk), .bram_in_rst(1'b0),
        .bram_in_en(in_en), .bram_in_we(in_we), .bram_in_addr(in_addr),
        .bram_in_din(in_din), .bram_in_dout(in_dout),
        .bram_out_clk(clk), .bram_out_rst(1'b0),
        .bram_out_en(out_en), .bram_out_we(out_we), .bram_out_addr(out_addr),
        .bram_out_din(out_din), .bram_out_dout(out_dout)
    );

    wire        done     = stat_hi[16];
    wire        busy     = stat_hi[17];
    wire        ovf      = stat_hi[18];
    wire [15:0] n_out    = stat_hi[15:0];

    integer i, pass, errs;
    integer f_out, f_rd;
    reg [8*1024-1:0] line;
    integer len;

    /* +DRC 时 ctrl[17]=1，走「四段增益 + WDRC」那条路；否则只过前端 */
    reg drc_sel;

    /* 第一块的结果，用来和第二块逐位比 */
    reg signed [15:0] blk1 [0:MAXN-1];
    reg [31:0] cyc1, cyc2;

    /* 跑一块：拉 start、等 done、把 cycles 拿回来 */
    task run_block;
        input  integer    n;
        output reg [31:0] cycles_out;
        begin
            @(posedge clk);
            ctrl = {14'd0, drc_sel, 1'b0, n[15:0]};   // 先清 start
            repeat (4) @(posedge clk);
            ctrl = {14'd0, drc_sel, 1'b1, n[15:0]};   // start 上升沿
            repeat (4) @(posedge clk);
            ctrl = {14'd0, drc_sel, 1'b0, n[15:0]};   // 立刻放下，等下一次上升沿
            pass = 0;
            while (!done && pass < 2000000) begin
                @(posedge clk);
                pass = pass + 1;
            end
            if (!done) begin
                $display("[tb] ERROR: timeout waiting done (n=%0d)", n);
                errs = errs + 1;
            end
            @(posedge clk);
            cycles_out = stat_lo;
        end
    endtask

    initial begin
        errs = 0;
        ctrl = 32'd0;
        drc_sel = $test$plusargs("DRC") ? 1'b1 : 1'b0;
        $display("[tb] mode: %s", drc_sel ? "fe + gain + WDRC" : "fe only (bypass)");

        /* 数行数，别靠"从尾巴数非零" */
        f_rd = $fopen("in_q15.mem", "r");
        len = 0;
        while ($fgets(line, f_rd) != 0) len = len + 1;
        $fclose(f_rd);
        $display("[tb] input samples: %0d", len);
        if (len <= 0 || len > MAXN) begin
            $display("[tb] ERROR: 输入 %0d 行，超出 1..%0d", len, MAXN);
            errs = errs + 1;
        end

        for (i = 0; i < 8192; i = i + 1) begin
            u_in.mem[i]  = 32'd0;
            u_out.mem[i] = 32'd0;
        end
        $readmemh("in_q15.mem", u_in.mem);

        repeat (20) @(posedge clk);
        rst_n = 1'b1;
        repeat (10) @(posedge clk);

        /* ---------- 第一块 ---------- */
        run_block(len, cyc1);
        $display("[tb] block1: n_out=%0d overflow=%0d cycles=%0d", n_out, ovf, cyc1);
        $display("[tb] block1: cycles/(n-1) = %0.3f", cyc1 * 1.0 / (len - 1));
        if (ovf)  begin $display("[tb] ERROR: overflow"); errs = errs + 1; end
        if (n_out != len) begin $display("[tb] ERROR: n_out=%0d want %0d", n_out, len); errs = errs + 1; end

        for (i = 0; i < len; i = i + 1) blk1[i] = u_out.mem[i][15:0];

        /* 落盘，交给 Python 和 rtl_fe_out.txt 逐位比 */
        f_out = $fopen("shell_out.txt", "w");
        for (i = 0; i < len; i = i + 1) $fwrite(f_out, "%0d\n", $signed(u_out.mem[i][15:0]));
        $fclose(f_out);

        /* ---------- 第二块：输出缓冲区先涂成哨兵，再跑同一批输入 ---------- */
        for (i = 0; i < 8192; i = i + 1) u_out.mem[i] = 32'hDEADBEEF;

        run_block(len, cyc2);
        $display("[tb] block2: n_out=%0d overflow=%0d cycles=%0d", n_out, ovf, cyc2);
        if (n_out != len) begin $display("[tb] ERROR: block2 n_out=%0d", n_out); errs = errs + 1; end
        if (cyc1 != cyc2) begin
            $display("[tb] ERROR: 两块拍数不一致 %0d vs %0d", cyc1, cyc2);
            errs = errs + 1;
        end

        pass = 0;
        for (i = 0; i < len; i = i + 1) begin
            if (blk1[i] !== u_out.mem[i][15:0]) begin
                if (pass < 5)
                    $display("[tb] ERROR: block2 out[%0d]=%0d  block1=%0d",
                             i, $signed(u_out.mem[i][15:0]), blk1[i]);
                pass = pass + 1;
            end
        end
        $display("[tb] block1 vs block2 mismatch: %0d / %0d", pass, len);
        if (pass != 0) errs = errs + 1;

        if (errs == 0) $display("[tb] SHELL PASS");
        else           $display("[tb] SHELL FAIL (%0d)", errs);
        $display("[tb] DONE");
        $finish;
    end

endmodule
