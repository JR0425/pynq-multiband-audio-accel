/* tb_fir_pl_regs —— AXI-Lite 寄存器文件的单元测试
 *
 *   bash sim/rtl_sim/run_xsim_regs.sh
 *
 * 验四件事:
 *   ① 出厂默认与 v25 逐位一致(寄存器表 §5);
 *   ② 写进去能读回来(参数组两组银行 + 控制位);
 *   ③ 只读寄存器真的是直通输入(不是本地寄存器);
 *   ④ CTRL[2] 自清、CLR_STAT 出单拍脉冲。
 */

`timescale 1ns / 1ps

module tb_fir_pl_regs;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    always #5 clk = ~clk;

    reg  [11:0] awaddr = 12'd0;
    reg         awvalid = 1'b0;
    wire        awready;
    reg  [31:0] wdata = 32'd0;
    reg  [3:0]  wstrb = 4'hF;
    reg         wvalid = 1'b0;
    wire        wready;
    wire [1:0]  bresp;
    wire        bvalid;
    reg         bready = 1'b1;
    reg  [11:0] araddr = 12'd0;
    reg         arvalid = 1'b0;
    wire        arready;
    wire [31:0] rdata;
    wire [1:0]  rresp;
    wire        rvalid;
    reg         rready = 1'b1;

    wire [31:0] ctrl;
    wire        clr_pulse;
    wire [63:0]  gain_b0, gain_b1;
    wire [95:0]  thr_b0, ratio_b0, atk_b0, rel_b0;
    wire [95:0]  thr_b1, ratio_b1, atk_b1, rel_b1;
    wire [15:0] ramp_div_b0, ramp_div_b1;
    wire [2:0]  ear_ctl;
    wire [1:0]  tone_sel;
    wire [15:0] tone_amp;
    wire [15:0] mpo_th;
    wire [31:0] mpo_rel;
    wire [15:0] win_len;
    wire [31:0] ply_ctl, ply_len, scope_ctl;

    reg  [31:0] stat0 = 32'hDEAD_BEEF;
    reg  [127:0] env_b0 = 128'h0000_0004_0000_0003_0000_0002_0000_0001;
    reg  [127:0] gr_b0  = 128'h0;

    fir_pl_regs dut (
        .clk(clk), .rst_n(rst_n),
        .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready),
        .ctrl(ctrl), .clr_pulse(clr_pulse),
        .gain_b0(gain_b0), .thr_b0(thr_b0), .ratio_b0(ratio_b0), .atk_b0(atk_b0), .rel_b0(rel_b0),
        .gain_b1(gain_b1), .thr_b1(thr_b1), .ratio_b1(ratio_b1), .atk_b1(atk_b1), .rel_b1(rel_b1),
        .ramp_div_b0(ramp_div_b0), .ramp_div_b1(ramp_div_b1),
        .ear_ctl(ear_ctl), .tone_sel(tone_sel), .tone_amp(tone_amp),
        .mpo_th(mpo_th), .mpo_rel(mpo_rel), .win_len(win_len),
        .ply_ctl(ply_ctl), .ply_len(ply_len), .scope_ctl(scope_ctl),
        .stat0(stat0), .stat1(32'h0), .stat2(32'h0),
        .smp_cnt(32'd0), .frm_cnt(32'd0), .ply_rd(32'd0), .ply_und(32'd0),
        .env_b0(env_b0), .gr_b0(gr_b0), .env_b1(128'd0), .gr_b1(128'd0),
        .lvl_in_lo(32'd0), .lvl_in_hi(32'd0), .lvl_out_lo(32'd0), .lvl_out_hi(32'd0),
        .peak_in(16'd0), .peak_out(16'd0),
        .mpo_cnt(32'd0), .mpo_peak(16'd0),
        .scope_seq(32'd0), .scope_wr(13'd0),
        .ramp_busy_b0(1'b0), .ramp_busy_b1(1'b0)
    );

    /* ---- AXI-Lite 主机任务 ---- */
    task axi_write(input [11:0] a, input [31:0] d);
        begin
            @(posedge clk);
            awaddr <= a; awvalid <= 1'b1;
            wdata  <= d; wvalid  <= 1'b1;
            while (!(awready && wready)) @(posedge clk);
            @(posedge clk);
            awvalid <= 1'b0; wvalid <= 1'b0;
            while (!bvalid) @(posedge clk);
            @(posedge clk);
        end
    endtask

    task axi_read(input [11:0] a, output [31:0] d);
        begin
            @(posedge clk);
            araddr <= a; arvalid <= 1'b1;
            while (!arready) @(posedge clk);
            @(posedge clk);
            arvalid <= 1'b0;
            while (!rvalid) @(posedge clk);
            d = rdata;
            @(posedge clk);
        end
    endtask

    /* ---- 检查 ---- */
    integer fail = 0;
    reg [31:0] v;

    task chk(input [255:0] what, input [31:0] got, input [31:0] exp);
        begin
            if (got !== exp) begin
                $display("  FAIL %0s: got=0x%08x exp=0x%08x", what, got, exp);
                fail = fail + 1;
            end else begin
                $display("  ok   %0s = 0x%08x", what, got);
            end
        end
    endtask

    /* ---- 脉冲监测 ---- */
    integer clr_cnt = 0;
    always @(posedge clk) if (clr_pulse) clr_cnt <= clr_cnt + 1;

    integer n;

    initial begin
        $display("=== tb_fir_pl_regs ===");
        repeat (4) @(posedge clk);
        rst_n = 1'b1;
        repeat (4) @(posedge clk);

        $display("-- ① 出厂默认 --");
        axi_read(12'h000, v); chk("ID", v, 32'h504C0002);
        axi_read(12'h100, v); chk("GAIN[0] b0", v, 32'd256);
        axi_read(12'h10C, v); chk("GAIN[3] b0", v, 32'd256);
        axi_read(12'h200, v); chk("GAIN[0] b1", v, 32'd256);
        axi_read(12'h110, v); chk("THR[0]  b0", v, 32'd419456);
        axi_read(12'h120, v); chk("RATIO[0] b0", v, 32'd2936064);
        axi_read(12'h130, v); chk("ATK[0]  b0", v, 32'd17408);
        axi_read(12'h140, v); chk("REL[0]  b0", v, 32'd896);
        axi_read(12'h170, v); chk("RAMP_DIV b0", v, 32'd0);
        axi_read(12'h048, v); chk("MPO_TH", v, 32'd32767);
        axi_read(12'h04C, v); chk("MPO_REL", v, 32'd896);
        axi_read(12'h03C, v); chk("WIN_LEN", v, 32'd1024);
        axi_read(12'h004, v); chk("CTRL", v, 32'd0);

        $display("-- ② 写读回 --");
        axi_write(12'h104, 32'd271);
        axi_read (12'h104, v); chk("GAIN[1] b0 rd", v, 32'd271);
        chk("gain_b0[1] out", {16'd0, gain_b0[31:16]}, 32'd271);
        axi_write(12'h124, 32'd2097152);
        axi_read (12'h124, v); chk("RATIO[1] b0 rd", v, 32'd2097152);
        axi_write(12'h234, 32'd17408);
        axi_read (12'h234, v); chk("ATK[1]  b1 rd", v, 32'd17408);
        axi_write(12'h170, 32'h0000_0007);
        axi_read (12'h170, v); chk("RAMP_DIV b0 rd", v, 32'd7);
        axi_write(12'h060, 32'h0000_0005);
        axi_read (12'h060, v); chk("EAR_CTL rd", v, 32'd5);
        chk("ear_ctl out", {29'd0, ear_ctl}, 32'd5);

        $display("-- ③ 只读直通 --");
        axi_read(12'h008, v); chk("STAT0 passthru", v, 32'hDEADBEEF);
        axi_read(12'h150, v); chk("ENV[0] b0 passthru", v, 32'd1);
        axi_read(12'h15C, v); chk("ENV[3] b0 passthru", v, 32'd4);
        axi_write(12'h008, 32'h1234);            // 写只读应当无效
        axi_read (12'h008, v); chk("STAT0 ro", v, 32'hDEADBEEF);

        $display("-- ④ CTRL[2] 自清 / CLR_STAT 脉冲 --");
        axi_write(12'h004, 32'h0000_0025);       // run + src + flush
        axi_read (12'h004, v); chk("CTRL rd(no flush)", v & 32'hFFFF_FFFB, 32'h21);
        if (ctrl[0] !== 1'b1) begin $display("  FAIL ctrl[0] 没置起来"); fail = fail + 1; end
        else $display("  ok   ctrl[0] = 1");
        n = 40;
        while (ctrl[2] && n > 0) begin
            @(posedge clk);
            n = n - 1;
        end
        if (ctrl[2] !== 1'b0) begin $display("  FAIL CTRL[2] 没在 40 拍内自清"); fail = fail + 1; end
        else $display("  ok   CTRL[2] 自清（用了 %0d 拍）", 40 - n);

        clr_cnt = 0;
        axi_write(12'h070, 32'h1);
        repeat (4) @(posedge clk);
        if (clr_cnt != 1) begin
            $display("  FAIL CLR_STAT 脉冲数 = %0d（要 1）", clr_cnt);
            fail = fail + 1;
        end else $display("  ok   CLR_STAT 一个单拍脉冲");

        axi_write(12'h004, 32'h0000_0101);       // ctrl[8] 也该出脉冲
        repeat (4) @(posedge clk);
        if (clr_cnt != 2) begin
            $display("  FAIL ctrl[8] 没出脉冲（累计 %0d）", clr_cnt);
            fail = fail + 1;
        end else $display("  ok   ctrl[8] 也能出脉冲");

        $display("-- ⑤ 打包端口：四段要各自落到自己的槽位 --");
        /* 这一段是补的判据。之前只对过 gain_b0[1]，四种参数都写同一个值的时候，
         * 32 位打包 + 取低 64 位的接法会把 b1/b3 段喂成 0、b2 段喂成 b1 的值，
         * 单值测试完全看不出来。 */
        axi_write(12'h100, 32'h0000_0100);       // 256
        axi_write(12'h104, 32'h0000_0200);       // 512
        axi_write(12'h108, 32'h0000_0300);       // 768
        axi_write(12'h10C, 32'h0000_0400);       // 1024
        chk("gain_b0 段0", {16'd0, gain_b0[15:0]},  32'd256);
        chk("gain_b0 段1", {16'd0, gain_b0[31:16]}, 32'd512);
        chk("gain_b0 段2", {16'd0, gain_b0[47:32]}, 32'd768);
        chk("gain_b0 段3", {16'd0, gain_b0[63:48]}, 32'd1024);
        axi_write(12'h110, 32'h000A_0001);
        axi_write(12'h114, 32'h000A_0002);
        axi_write(12'h118, 32'h000A_0003);
        axi_write(12'h11C, 32'h000A_0004);
        chk("thr_b0 段0", {8'd0, thr_b0[23:0]},  32'h000A_0001);
        chk("thr_b0 段1", {8'd0, thr_b0[47:24]}, 32'h000A_0002);
        chk("thr_b0 段2", {8'd0, thr_b0[71:48]}, 32'h000A_0003);
        chk("thr_b0 段3", {8'd0, thr_b0[95:72]}, 32'h000A_0004);

        $display("");
        if (fail == 0) $display("PASS 寄存器文件全部通过");
        else           $display("FAIL 共 %0d 项不符", fail);
        $display("[done]");
        $finish;
    end

    initial begin
        #500000;
        $display("FAIL 超时");
        $finish;
    end

endmodule
