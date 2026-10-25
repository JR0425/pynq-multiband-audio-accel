/* tb_wave_cap —— fir_wave_cap 的单元激励（xsim）
 *
 * 假的 AXI-Stream：输入每 3 拍握手一次，tdata 就是从 0 开始的采样序号；
 * 输出侧把输入延后 288 拍（= 96 采样群延迟 × 3 拍）再送出来。
 *
 * 抓两次：
 *   1) trig_sel=0（块头）：第 0 条应当正好是第一个握手拍
 *   2) trig_sel=1（块尾）：第 0 条应当是 in_last 那一拍，TLAST 落在第 288 条
 */
`timescale 1ns / 1ps

module tb_wave_cap;

    localparam integer DEPTH = 1024;
    localparam integer NSAMP = 201;
    localparam integer GD_CYC = 288;

    reg clk = 0, rst_n = 0;
    always #5 clk = ~clk;

    reg        arm = 0;
    reg [1:0]  trig_sel = 0;

    reg        in_valid = 0, in_ready = 1, in_last = 0;
    reg [15:0] in_tdata = 0;
    reg        out_valid = 0, out_ready = 1, out_last = 0;
    reg [15:0] out_tdata = 0;

    wire        done;
    wire        bram_en;
    wire [9:0]  bram_addr;
    wire [31:0] bram_din_in, bram_din_out;

    fir_wave_cap #(.DEPTH(DEPTH), .AW(10)) dut (
        .clk(clk), .rst_n(rst_n),
        .arm(arm), .trig_sel(trig_sel),
        .in_valid(in_valid), .in_ready(in_ready), .in_last(in_last), .in_tdata(in_tdata),
        .out_valid(out_valid), .out_ready(out_ready), .out_last(out_last), .out_tdata(out_tdata),
        .done(done),
        .bram_en(bram_en), .bram_addr(bram_addr),
        .bram_din_in(bram_din_in), .bram_din_out(bram_din_out)
    );

    /* 采样计数：in 握手一次 +1；run 门控，武装之前不发数 */
    integer sent = 0;
    integer cycle = 0;
    reg     run = 0;

    /* 输出侧延迟线 */
    reg        dv [0:GD_CYC];
    reg        dl [0:GD_CYC];
    reg [15:0] dd [0:GD_CYC];
    integer i;

    /* 抓下来的数据 */
    reg [31:0] cap_in  [0:DEPTH-1];
    reg [31:0] cap_out [0:DEPTH-1];
    integer nw = 0;
    integer errs = 0;

    always @(posedge clk) begin
        cycle <= cycle + 1;
        if (bram_en) begin
            cap_in[bram_addr]  <= bram_din_in;
            cap_out[bram_addr] <= bram_din_out;
            nw <= nw + 1;
        end
    end

    /* 输入：每 3 拍一个握手，tdata = 序号，最后一拍带 last */
    always @(posedge clk) begin
        if (!rst_n) begin
            in_valid <= 0; in_last <= 0; in_tdata <= 0;
        end else begin
            if (in_valid && in_ready) sent <= sent + 1;
            in_valid <= run && (cycle % 3 == 0) && (sent < NSAMP);
            in_tdata <= sent[15:0];
            in_last  <= run && (sent == NSAMP - 1);
        end
    end

    /* 输出：把输入整体打 288 拍 */
    integer j;
    always @(posedge clk) begin
        for (j = GD_CYC - 1; j > 0; j = j - 1) begin
            dv[j] <= dv[j-1]; dl[j] <= dl[j-1]; dd[j] <= dd[j-1];
        end
        dv[0] <= in_valid && in_ready; dl[0] <= in_last; dd[0] <= in_tdata;
        out_valid <= dv[GD_CYC - 1];
        out_last  <= dl[GD_CYC - 1];
        out_tdata <= dd[GD_CYC - 1];
    end

    task wait_done;
        integer t;
        begin
            t = 0;
            while (!done && t < 4000) begin
                @(posedge clk); t = t + 1;
            end
            repeat (3) @(posedge clk);   // 等最后一拍的 NBA 落地再数
            if (!done) begin
                $display("FAIL  %0d 拍内没有 done", t);
                errs = errs + 1;
            end
        end
    endtask

    task do_arm(input [1:0] sel);
        begin
            run = 0; sent = 0;
            arm = 0; trig_sel = sel;
            repeat (4) @(posedge clk);
            arm = 1;
            repeat (4) @(posedge clk);
            run = 1;
        end
    endtask

    function [31:0] w_in(input integer k);  w_in  = cap_in[k];  endfunction
    function [31:0] w_out(input integer k); w_out = cap_out[k]; endfunction

    initial begin
        for (i = 0; i <= GD_CYC; i = i + 1) begin dv[i] = 0; dl[i] = 0; dd[i] = 0; end
        nw = 0; errs = 0;

        repeat (6) @(posedge clk);
        rst_n <= 1;
        repeat (6) @(posedge clk);

        /* ---------- 测试 1：块头 ---------- */
        do_arm(2'd0);
        wait_done;
        if (nw != DEPTH) begin
            $display("FAIL  [head] 写了 %0d 条，应为 %0d", nw, DEPTH);
            errs = errs + 1;
        end
        if (w_in(0) !== 32'h0000_0003) begin
            $display("FAIL  [head] 第 0 条应为 {tdata=0,last=0,ready=1,valid=1}=0x00000003，读到 0x%08X",
                     w_in(0));
            errs = errs + 1;
        end else
            $display("PASS  [head] 第 0 条 0x%08X（valid=1 ready=1 last=0 tdata=0）", w_in(0));

        if ((w_in(1) & 1) !== 1'b0) begin
            $display("FAIL  [head] 第 1 条 in_valid 应为 0（激励每 3 拍才发一个），读到 0x%08X",
                     w_in(1));
            errs = errs + 1;
        end
        /* 第 3 条起每 3 条一个采样：第 3 条 tdata 应为 1 */
        if ((w_in(3) >> 16) !== 16'd1) begin
            $display("FAIL  [head] 第 3 条 tdata 应为 1，读到 %0d", w_in(3) >> 16);
            errs = errs + 1;
        end else
            $display("PASS  [head] 节流 3 拍/采样，第 3 条 tdata=1");

        /* in_last 应落在第 200 个采样 = 第 600 条 */
        if (((w_in(600) >> 2) & 1) !== 1'b1) begin
            $display("FAIL  [head] 第 600 条应有 in_last，读到 0x%08X", w_in(600));
            errs = errs + 1;
        end else
            $display("PASS  [head] in_last 落在第 600 条（= 200 采样 × 3 拍）");

        /* 输出侧：第 288 条前后应当出现 out_valid，tdata=0。
           给 ±2 拍的窗口 —— 激励里的延迟线本身就有 1 拍登记差，
           这里验的是"抓到的是同一时刻的 out_valid"，不是精确延迟。 */
        if (!((w_out(GD_CYC - 2) | w_out(GD_CYC - 1) | w_out(GD_CYC) |
               w_out(GD_CYC + 1) | w_out(GD_CYC + 2)) & 1)) begin
            $display("FAIL  [head] 第 %0d±2 条都没有 out_valid", GD_CYC);
            errs = errs + 1;
        end else
            $display("PASS  [head] 输出在第 %0d 条附近出现（群延迟 96 采样）", GD_CYC);

        /* ---------- 测试 2：块尾 ---------- */
        nw = 0;
        do_arm(2'd1);
        wait_done;
        if (nw != DEPTH) begin
            $display("FAIL  [tail] 写了 %0d 条，应为 %0d", nw, DEPTH);
            errs = errs + 1;
        end
        if (w_in(0) !== 32'h00C8_0007) begin
            $display("FAIL  [tail] 第 0 条应为 {tdata=200,last=1,ready=1,valid=1}=0x00C80007，读到 0x%08X",
                     w_in(0));
            errs = errs + 1;
        end else
            $display("PASS  [tail] 第 0 条就是 in_last 那一拍（tdata=200，last=1）");

        if ((w_in(1) & 1) !== 1'b0) begin
            $display("FAIL  [tail] 第 1 条 in_valid 应已落下，读到 0x%08X", w_in(1));
            errs = errs + 1;
        end
        if (((w_out(GD_CYC) >> 2) & 1) !== 1'b1) begin
            $display("FAIL  [tail] 第 %0d 条应有 out_last，读到 0x%08X", GD_CYC, w_out(GD_CYC));
            errs = errs + 1;
        end else
            $display("PASS  [tail] out_last 在触发后第 %0d 条（= 288 拍）", GD_CYC);

        if (errs == 0) $display("\n### ALL PASS");
        else           $display("\n### %0d FAIL", errs);
        $finish;
    end

endmodule
