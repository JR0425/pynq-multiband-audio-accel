/* fir_drc4 的单元测试台 —— 用 RTL 前端真实算出来的四段当输入
 *
 * 输入：rtl_fe_band0..3.mem（tb_fir_mb_fe 吐的，40 位有符号十六进制）
 * 输出：drc_out.txt（每行一个十进制 int16，和 y_vld 对齐）
 *
 * 喂数节奏照抄前端：每 3 拍给一个采样（in_vld 单拍脉冲）。
 * 这样测的就是「接在 fir_mb_fe 后面」的真实时序，不是理想化的每拍一个。
 *
 * 用法：run_xsim_drc4.sh 负责摆文件、跑仿真、和 Python 黄金模型逐位比。
 */

`timescale 1ns / 1ps

module tb_fir_drc4;

    localparam integer AW   = 40;
    localparam integer MAXL = 8192;

    reg clk = 1'b0;
    always #5 clk = ~clk;               // 100 MHz

    reg                 rst = 1'b1;
    reg                 in_vld = 1'b0;
    reg  signed [AW-1:0] band0, band1, band2, band3;

    wire signed [15:0] y;
    wire               y_vld;

    fir_drc4 #(.BW(AW), .DW(24)) dut (
        .clk(clk), .rst(rst),
        .in_vld(in_vld),
        .band0(band0), .band1(band1), .band2(band2), .band3(band3),
        .gain0(16'sd256), .gain1(16'sd256),
        .gain2(16'sd256), .gain3(16'sd256),
        /* 2026-10-30 修：2026-10-28 批 2 把 DRC 四参数改成端口了，这个 tb 一直没接，
         * 四个输入悬空（z）→ use_wdrc/omr 全是 X → 输出整片 x。现在绑 v25 出厂值，
         * 和 Python 黄金模型（run_xsim_drc4.sh 里那一段）对得上。 */
        .thr0(24'sd419456), .thr1(24'sd419456), .thr2(24'sd419456), .thr3(24'sd419456),
        .ratio0(24'sd2936064), .ratio1(24'sd2936064),
        .ratio2(24'sd2936064), .ratio3(24'sd2936064),
        .atk0(24'sd17408), .atk1(24'sd17408), .atk2(24'sd17408), .atk3(24'sd17408),
        .rel0(24'sd896), .rel1(24'sd896), .rel2(24'sd896), .rel3(24'sd896),
        .y(y), .y_vld(y_vld)
    );

    reg signed [AW-1:0] bm0 [0:MAXL-1];
    reg signed [AW-1:0] bm1 [0:MAXL-1];
    reg signed [AW-1:0] bm2 [0:MAXL-1];
    reg signed [AW-1:0] bm3 [0:MAXL-1];

    integer len, i, nout, errs, fo;
    reg [8*256-1:0] line;
    integer fr;

    initial begin
        errs = 0;
        band0 = 0; band1 = 0; band2 = 0; band3 = 0;

        fr = $fopen("rtl_fe_band0.mem", "r");
        if (fr == 0) begin
            $display("[tb] ERROR: 缺 rtl_fe_band0.mem");
            $finish;
        end
        len = 0;
        while ($fgets(line, fr) != 0) len = len + 1;
        $fclose(fr);
        if (len <= 0 || len > MAXL) begin
            $display("[tb] ERROR: 长度 %0d 越界", len);
            $finish;
        end
        $display("[tb] band samples: %0d", len);

        $readmemh("rtl_fe_band0.mem", bm0);
        $readmemh("rtl_fe_band1.mem", bm1);
        $readmemh("rtl_fe_band2.mem", bm2);
        $readmemh("rtl_fe_band3.mem", bm3);

        fo = $fopen("drc_out.txt", "w");

        repeat (5) @(posedge clk);
        rst = 1'b0;
        repeat (2) @(posedge clk);

        /* 每 3 拍一个采样，和前端 din_ready 的节奏一致 */
        for (i = 0; i < len; i = i + 1) begin
            @(posedge clk);
            band0  <= bm0[i];
            band1  <= bm1[i];
            band2  <= bm2[i];
            band3  <= bm3[i];
            in_vld <= 1'b1;
            @(posedge clk);
            in_vld <= 1'b0;
            @(posedge clk);
        end

        /* 排空流水：延迟 9 拍 + 1 拍写回，给到 16 拍余量 */
        repeat (16) @(posedge clk);

        $fclose(fo);
        $display("[tb] outputs collected: %0d (expected %0d)", nout, len);
        if (nout != len) begin
            $display("[tb] ERROR: 输出个数不对");
            errs = errs + 1;
        end
        if (errs == 0) $display("[tb] DRC4 PASS");
        else           $display("[tb] DRC4 FAIL");
        $display("[tb] DONE");
        $finish;
    end

    initial nout = 0;

    /* 写文件的时刻要**比 y_vld 晚一拍**：本模块的 y_vld 比 y 早一拍
     * （2026-10-28 批 2 加数据流水时 vpipe 没跟着加，遗留至今，未改 RTL）。
     * 直接按 y_vld 写会把上一个采样写进去，整份文件错一位。 */
    reg vld_d;
    always @(posedge clk) vld_d <= y_vld;

    always @(posedge clk) begin
        if (!rst && vld_d) begin
            $fwrite(fo, "%0d\n", $signed(y));
            nout = nout + 1;
        end
    end

endmodule
