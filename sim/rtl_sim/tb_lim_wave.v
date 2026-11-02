/* tb_lim_wave —— 只给报告出图用，不做判据（判据在 tb_drc_lim）
 *
 * 目的：把末级软膝限幅的输入和输出一起记下来，画成两张图
 *   ① lim_wave_inout.txt  一个过载正弦的 (u, y)：看波形是被"压扁"还是被"削方"
 *   ② lim_curve.txt       全范围单调扫 (u, y)：看六段折线的拐点在哪
 *
 * 和 tb_drc_lim 一样把 DRC 其余部分旁路掉（gain=256、thr 拉满、ratio=2^22 → omr=0、
 * atk=rel=0），只留末级限幅在动，这样 y 就是 lim16(u) 本身。
 *
 * 送数节奏沿用 tb_drc_lim：in_vld 一拍，再空 4 拍。取数也要晚两拍，理由见
 * tb_drc_lim 的注释（y_vld 比 y 早一拍，是既有实现，不是这次改的）。
 *
 * 用法：bash sim/rtl_sim/run_xsim_wave.sh
 */

`timescale 1ns / 1ps

module tb_lim_wave;

    localparam integer AW = 40;

    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg rst = 1'b1;
    reg in_vld = 1'b0;
    reg signed [AW-1:0] band0 = 0, band1 = 0, band2 = 0, band3 = 0;

    wire signed [15:0] y;
    wire               y_vld;

    fir_drc4 #(.BW(AW), .DW(24)) dut (
        .clk(clk), .rst(rst), .in_vld(in_vld),
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

    reg signed [23:0] q [0:4095];
    integer q_wr = 0, q_rd = 0;

    /* 四段平均分配：只喂 band0 的话，单段的 satd 把带宽钳在 ±2^23，
     * 相加后最多只能到 2 FS，看不到限幅的后几段。四段各出四分之一，
     * 合成回到 u，才能扫到 8 FS（tb_drc_lim 的 feed_sat 用的是同一招）。 */
    task feed(input integer u);
        reg signed [39:0] b;
        begin
            b     = (u / 4) <<< 17;
            band0 = b; band1 = b; band2 = b; band3 = b;
            q[q_wr] = u[23:0];
            q_wr = q_wr + 1;
            in_vld = 1'b1;
            @(posedge clk);
            in_vld = 1'b0;
            repeat (4) @(posedge clk);
        end
    endtask

    reg signed [15:0] ys1, ys2;
    reg vs1, vs2;
    always @(posedge clk) begin
        ys1 <= y;   vs1 <= y_vld;
        ys2 <= ys1; vs2 <= vs1;
    end

    integer fo_wave, fo_curve;
    integer i, uu;

    always @(posedge clk) begin
        if (!rst && vs1) begin
            /* 每路文件交错写：前 96 个是正弦段，之后是扫描段，由 initial 里切换 */
            if (fo_wave != 0 && q_rd < 96)
                $fwrite(fo_wave, "%0d %0d\n", $signed(q[q_rd]), $signed(y));
            else if (fo_curve != 0)
                $fwrite(fo_curve, "%0d %0d\n", $signed(q[q_rd]), $signed(y));
            q_rd = q_rd + 1;
        end
    end

    initial begin
        fo_wave = $fopen("lim_wave_inout.txt", "w");
        fo_curve = 0;
        repeat (6) @(posedge clk);
        rst = 1'b0;
        repeat (4) @(posedge clk);

        /* ① 幅度 2 FS 的正弦，一个周期 24 个采样，跑 4 个周期。
         *    门限 0.70 FS，所以每个峰都进压缩区，但不是很大 —— 正好看膝弯。 */
        for (i = 0; i < 96; i = i + 1) begin
            uu = $rtoi(65536.0 * $sin(6.2831853 * i / 24.0));
            feed(uu);
        end
        repeat (60) @(posedge clk);
        $fclose(fo_wave);
        fo_wave = 0;

        /* ② 全范围单调扫：-8 FS .. +8 FS，步长 512 */
        fo_curve = $fopen("lim_curve.txt", "w");
        for (i = -262144; i <= 262143; i = i + 512) feed(i);
        repeat (60) @(posedge clk);
        $fclose(fo_curve);

        $display("[tb] 正弦段 %0d 点，扫描段 %0d 点", 96, q_rd - 96);
        $display("[tb] DONE");
        $finish;
    end

    initial begin
        #20000000;
        $display("[tb] TIMEOUT");
        $finish;
    end

endmodule
