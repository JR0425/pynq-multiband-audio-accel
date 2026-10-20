/* fir_mb_fe 的测试台 —— 按 3 拍/采样喂数，把每一级的原值吐成文件交给 Python 对拍
 *
 * 吐出来的东西（全部和 dout_vld 对齐）：
 *     rtl_fe_ylp0/1/2.mem   三个低通累加器（40 位十六进制）
 *     rtl_fe_band0..3.mem   四段（40 位十六进制）
 *     rtl_fe_sum.mem        四段之和（40 位十六进制）
 *     rtl_fe_out.txt        最终输出（十进制 int16）
 *
 * 为什么每一级都吐：
 *     只看输出，出错时无法区分"乘法/加法树/抽头下标"和"相减/移位/饱和"。
 *     2026-10-07 第一版就是靠"先比累加器、再比输出"把问题范围砍掉的，
 *     这次把粒度再往下切一层 —— 四段各自也能单独看。
 *
 * 喂数规则：只在 din_ready 为高的那一拍换下一个采样。
 * 模块每 3 拍给一次 ready，所以这里天然就是 II=3。
 * ⚠️ din 在两次 ready 之间保持不动（生产者本来就该这样），
 *    模块只在 PRIME/P2 两个相位看 din_vld。
 */

`timescale 1ns / 1ps

module tb_fir_mb_fe;

    localparam integer ACCW   = 40;
    localparam integer MAXLEN = 4096;

    reg clk = 1'b0;
    reg rst = 1'b1;

    always #5 clk = ~clk;      // 100 MHz

    reg signed [15:0] x [0:MAXLEN-1];
    reg signed [15:0] din;
    reg               din_vld;
    wire              din_ready;

    wire signed [ACCW-1:0] ylp0, ylp1, ylp2;
    wire signed [ACCW-1:0] band0, band1, band2, band3;
    wire signed [ACCW-1:0] acc_raw;
    wire signed [15:0]     dout;
    wire                   dout_vld;

    fir_mb_fe #(.NTAP(193), .NALLOC(195), .CW(18), .ACCW(ACCW),
                .SHIFT(17), .GD(96)) dut (
        .clk(clk), .rst(rst),
        .din(din), .din_vld(din_vld), .din_ready(din_ready),
        .ylp0(ylp0), .ylp1(ylp1), .ylp2(ylp2),
        .band0(band0), .band1(band1), .band2(band2), .band3(band3),
        .acc_raw(acc_raw), .dout(dout), .dout_vld(dout_vld)
    );

    integer fi, fy0, fy1, fy2, fb0, fb1, fb2, fb3, fs, fo;
    integer n, got, len;
    integer cyc, cyc_first, cyc_last;
    reg [8*1024-1:0] line;

    initial begin
        /* 先数行数再读 —— 长度不能靠"从尾巴数非零"推（理由见 tb_fir_lp.v） */
        fi = $fopen("in_q15.mem", "r");
        if (fi == 0) begin
            $display("[tb] ERROR: cannot open in_q15.mem");
            $finish;
        end
        len = 0;
        while ($fgets(line, fi) != 0) len = len + 1;
        $fclose(fi);
        if (len <= 0 || len > MAXLEN) begin
            $display("[tb] ERROR: LEN=%0d out of range (MAXLEN=%0d)", len, MAXLEN);
            $finish;
        end
        for (n = 0; n < MAXLEN; n = n + 1) x[n] = 16'sd0;
        $readmemh("in_q15.mem", x);
        $display("[tb] samples: %0d", len);

        fy0 = $fopen("rtl_fe_ylp0.mem", "w");
        fy1 = $fopen("rtl_fe_ylp1.mem", "w");
        fy2 = $fopen("rtl_fe_ylp2.mem", "w");
        fb0 = $fopen("rtl_fe_band0.mem", "w");
        fb1 = $fopen("rtl_fe_band1.mem", "w");
        fb2 = $fopen("rtl_fe_band2.mem", "w");
        fb3 = $fopen("rtl_fe_band3.mem", "w");
        fs  = $fopen("rtl_fe_sum.mem",   "w");
        fo  = $fopen("rtl_fe_out.txt",   "w");
        if (fy0 == 0 || fo == 0) begin
            $display("[tb] ERROR: cannot open output files");
            $finish;
        end

        din     = 16'sd0;
        din_vld = 1'b0;
        cyc = 0; cyc_first = 0; cyc_last = 0;
        repeat (4) @(posedge clk);
        rst = 1'b0;
        @(posedge clk);
        /* 第一个采样先摆上（PRIME 相位 ready 就是高的） */
        din     = x[0];
        din_vld = 1'b1;

        n   = 0;
        got = 0;
        while (n < len) begin
            @(posedge clk);
            /* 这一拍之前 ready 为高 = 这个采样被接收了 */
            if (din_ready) begin
                n = n + 1;
                if (n < len) begin
                    din     = x[n];
                    din_vld = 1'b1;
                end else begin
                    din     = 16'sd0;
                    din_vld = 1'b0;
                end
            end
        end

        /* 排空流水线：最后一个采样之后还有 7 拍左右才出结果 */
        repeat (12) @(posedge clk);

        $fclose(fy0); $fclose(fy1); $fclose(fy2);
        $fclose(fb0); $fclose(fb1); $fclose(fb2); $fclose(fb3);
        $fclose(fs);  $fclose(fo);
        $display("[tb] collected %0d outputs (expected %0d)", got, len);
        if (got != len) $display("[tb] ERROR: count mismatch");
        /* 拍数：第一次出数和最后一次出数之间隔了多少拍。
         * 报告里的"3 拍/采样"要用这个数，不要凭结构推。 */
        if (len > 1)
            $display("[tb] cycles: first=%0d last=%0d span=%0d over %0d samples -> %0d/1000 per sample",
                     cyc_first, cyc_last, cyc_last - cyc_first, len - 1,
                     (cyc_last - cyc_first) * 1000 / (len - 1));
        $display("[tb] DONE");
        $finish;
    end

    /* 收数只认 dout_vld */
    always @(posedge clk) begin
        if (!rst) cyc = cyc + 1;
        if (!rst && dout_vld) begin
            if (got == 0) cyc_first = cyc;
            cyc_last = cyc;
            $fwrite(fy0, "%010x\n", ylp0);
            $fwrite(fy1, "%010x\n", ylp1);
            $fwrite(fy2, "%010x\n", ylp2);
            $fwrite(fb0, "%010x\n", band0);
            $fwrite(fb1, "%010x\n", band1);
            $fwrite(fb2, "%010x\n", band2);
            $fwrite(fb3, "%010x\n", band3);
            $fwrite(fs,  "%010x\n", acc_raw);
            /* %d 默认按无符号打印，负数必须 $signed */
            $fwrite(fo,  "%0d\n", $signed(dout));
            got = got + 1;
        end
    end

endmodule
