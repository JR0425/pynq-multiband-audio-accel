/* fir_lp 的测试台 —— 把 RTL 的输出原样吐成文件，交给 Python 逐位比对
 *
 * 文件名全部写死（in_q15.mem / rtl_acc.mem / rtl_out.txt），**不用 plusarg**。
 * 理由见 src/rtl/fir_lp.v 里系数表那一段：Windows 上 plusarg 这条路
 * 要么被 cmd 吃掉引号、要么缺 DLL，两堵墙夹下来最省事的是不上命令行传路径。
 * 运行脚本（run_xsim.sh）负责把要用的文件摆成这几个名字。
 *
 * 为什么全部走文件、而不是在 testbench 里直接判对错：
 *     参考模型是 Python 写的（tools/ref_model_int.py），对拍也在 Python 里做。
 *     testbench 只干两件事 —— 按拍喂数据、按拍收数据。
 *     判据一旦写进 testbench，改判据就要重编 RTL，容易把"平台"和"被测物"搅在一起。
 *
 * 输入格式：每行一个 16 位十六进制（in_q15.mem，由 ref_model_int.py 生成）。
 * 注意输入是 int16 的**原始比特**，测试台不做任何数值换算 ——
 * "浮点 -> Q1.15"那一步已经在 Python 侧做完了，两边用的是同一串比特。
 */

`timescale 1ns / 1ps

module tb_fir_lp;

    localparam integer NALLOC = 195;
    localparam integer ACCW   = 40;
    localparam integer MAXLEN = 4096;

    reg clk = 1'b0;
    reg rst = 1'b1;

    always #5 clk = ~clk;      // 100 MHz

    reg signed [15:0] x [0:MAXLEN-1];
    reg signed [15:0] din;
    reg               din_vld;
    wire signed [ACCW-1:0] acc_raw;
    wire signed [15:0]     dout;
    wire                   dout_vld;

    fir_lp #(
        .NTAP(193), .NALLOC(NALLOC), .CW(18), .ACCW(ACCW), .SHIFT(17)
    ) dut (
        .clk(clk), .rst(rst), .din(din), .din_vld(din_vld),
        .acc_raw(acc_raw), .dout(dout), .dout_vld(dout_vld)
    );

    integer fa, fo, fi;
    integer n, got, len;
    reg [8*1024-1:0] line;

    initial begin
        /* ---- 先数行数，再读数据 ----
         * 长度**不能**用"从尾巴上数非零"来推：音频信号结尾恰好是 0
         * 一点都不稀奇，那样会静默少跑几个采样，而且对拍时表现为
         * "最后几个点不见了"，很容易误判成流水线排空没排干净。 */
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

        fa = $fopen("rtl_acc.mem", "w");
        fo = $fopen("rtl_out.txt", "w");
        if (fa == 0 || fo == 0) begin
            $display("[tb] ERROR: cannot open output files");
            $finish;
        end

        // ---- 复位 ----
        din     = 16'sd0;
        din_vld = 1'b0;
        repeat (4) @(posedge clk);
        rst = 1'b0;
        @(posedge clk);

        // ---- 喂数据：每拍一个采样，永不间断（II=1 的意思）----
        /* got 必须在这里清零，**不能**在喂完之后清。
         * dout_vld 是 din_vld 延两拍，喂数循环刚开始两拍就开始计数了；
         * 在循环之后才清零的话，会把已经数到的 996 个冲掉，只留下排空那几拍，
         * 打印出 "collected 4 (expected 1000)" —— 而输出文件本身是好的。
         * 一个会说谎的计数器比没有计数器更费时间。 */
        got = 0;
        for (n = 0; n < len; n = n + 1) begin
            din     <= x[n];
            din_vld <= 1'b1;
            @(posedge clk);
        end
        din_vld <= 1'b0;
        din     <= 16'sd0;

        // ---- 收数据：再等几拍把流水线排空 ----
        repeat (8) @(posedge clk);

        $fclose(fa);
        $fclose(fo);
        $display("[tb] collected %0d outputs (expected %0d)", got, len);
        if (got != len) $display("[tb] ERROR: count mismatch");
        $display("[tb] DONE");
        $finish;
    end

    /* 收数只认 dout_vld —— 它由流水线里的 valid 延迟两级得到，
     * 和 acc_raw/dout 严格对齐，所以不会多收也不会少收。 */
    always @(posedge clk) begin
        if (!rst && dout_vld) begin
            $fwrite(fa, "%010x\n", acc_raw);
            /* 一定要 $signed() —— %d 默认按无符号打印，
             * 负数会变成 65535 这种数，对拍时会显示成"到处都不对" */
            $fwrite(fo, "%0d\n", $signed(dout));
            got = got + 1;
        end
    end

endmodule
