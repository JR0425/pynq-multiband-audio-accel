/* fir_pl_direct —— 把麦克风到耳机整条链放在 PL 里，PS 只在一开始配一次。
 *
 *   I2S 收 → 录音 FIFO → fir_mb_fe → fir_drc4 → 输出 FIFO → 播放 FIFO → I2S 发
 *
 * 和块模式（fir_dma_core.bit）的区别只有一个：**中间没有 DDR、没有 DMA、
 * 没有 PS 参与每一块**。所以端到端延迟里不再有"一块的缓冲"和"PS 每块 2.6 ms"
 * 这两项，只剩 FIFO 水位 + 核的组延迟 + 声学。
 *
 * 延迟怎么算（TX_TARGET=128 时）：
 *     播放 FIFO 水位  128 字 / (2·48.83 kHz) ≈ 1.31 ms
 *     输出 FIFO       最多 32 字            ≈ 0.33 ms
 *     核的组延迟      96 字 = 48 帧         ≈ 0.98 ms
 *     声学（实测）                           1.23 ms
 *     ------------------------------------------------
 *     合计                                   ≈ 3.9 ms
 *
 * 流控只有一处：**播放入口按播放 FIFO 的水位节流**。
 * 前端的输出是推式的（没有 ready），所以背压只能从输入侧给 —— 和
 * fir_stream_shell 里那条注释是同一个道理。这里把"输入侧"落在 i2s_duplex 的
 * 录音 M_AXIS 上：tx_lvl + 输出 FIFO 占用 到 TX_TARGET 就不再收新字。
 * 录音 FIFO 因此一直只有一两个字，不产生积压（块模式那个"中性平衡"的坑
 * 在这里不存在）。
 *
 * 和 fir_stream_shell 的另一个区别：**没有块边界，所以前端复位只在上电/flush
 * 时给一次**。延迟线跨采样一直保持，等于 v25 那种 static 延迟线的语义。
 *
 * 参数：fir_drc4 的阈值/压缩比/时间常数是综合时的 parameter（和 v25 的默认值
 * 同一套数值），四段增益同样是 parameter。要在网页上拖增益得再加寄存器，
 * 这一版没做 —— 先把延迟和稳定性验出来。
 */

`timescale 1ns / 1ps

module fir_pl_direct #(
    parameter integer TX_TARGET = 128,   // 播放 FIFO 水位目标（字），直接进延迟
    parameter integer OUT_AW    = 5,     // 输出 FIFO 深度 = 2^OUT_AW 字
    parameter integer RST_CYC   = 8      // flush 松开后再给前端几拍复位
) (
    input  wire        clk,
    input  wire        rst_n,

    /* ctrl[0] run     =1 才往外放（i2s_duplex 的语义）
     * ctrl[1] drc_en  =1 走压缩通路，0 只过前端（直通/只做四段增益）
     * ctrl[2] flush   =1 两侧 FIFO 和前端一起清空
     * 其余位留给 i2s_duplex（[8] 清计数） */
    input  wire [31:0] ctrl,
    output wire [31:0] stat0,            // i2s_duplex：ovf / und 计数
    output wire [31:0] stat1,            // i2s_duplex：FIFO 占用
    output wire [31:0] stat2,            // 这里自己的：输出 FIFO 溢出 / 见过的最大 TX 水位

    /* 调试用（2026-10-25 加的，定位"flush 后放行不出数"）：
     *   dbg0 = {out_vld 次数[15:0], din_vld 次数[15:0]}，flush 清零。
     *          两个都在涨 = 前端在出数；只有 din 在涨 = 前端吃了不吐；
     *          都不涨 = 前端根本没被喂到。
     *   dbg1 = 状态快照，按拍看没有意义，看的是"卡在哪个相位/是不是一直被复位"。 */
    output wire [31:0] dbg0,
    output wire [31:0] dbg1,

    /* I2S pads */
    output wire        bclk,
    output wire        lrclk,
    output wire        sdata_o,
    input  wire        sdata_i
);

    localparam integer OUT_DEPTH = 1 << OUT_AW;

    wire run   = ctrl[0];
    wire flush = ctrl[2];

    /* ctrl[1] 在 i2s_duplex 里是"PL 内部把收到的流直接灌回去"的回环开关，
     * 在这里是 drc_en。同一位两个意思，一置 1 就把整条 FIR 旁路掉了 ——
     * 所以在进 i2s_duplex 之前把这一位清掉。 */
    wire [31:0] i2s_ctrl = ctrl & ~32'h0000_0002;

    /* ================= i2s_duplex ================= */

    wire [31:0] cap_tdata;
    wire        cap_tvalid, cap_tready;
    wire [31:0] ply_tdata;
    wire        ply_tvalid, ply_tready;

    i2s_duplex u_i2s (
        .clk            (clk),
        .rst_n          (rst_n),
        .ctrl           (i2s_ctrl),
        .stat0          (stat0),
        .stat1          (stat1),
        .bclk           (bclk),
        .lrclk          (lrclk),
        .sdata_o        (sdata_o),
        .sdata_i        (sdata_i),
        .s_axis_tdata   (ply_tdata),
        .s_axis_tvalid  (ply_tvalid),
        .s_axis_tready  (ply_tready),
        .s_axis_tlast   (1'b0),
        .m_axis_tdata   (cap_tdata),
        .m_axis_tvalid  (cap_tvalid),
        .m_axis_tready  (cap_tready),
        .m_axis_tlast   ()
    );

    wire [15:0] tx_lvl  = stat1[15:0];
    wire [15:0] cap_lvl = stat1[31:16];

    /* ================= 前端复位 ================= */

    reg  [3:0] rst_cnt;
    reg        fe_rst;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)      rst_cnt <= 4'd0;
        else if (flush)  rst_cnt <= RST_CYC;
        else if (rst_cnt != 4'd0) rst_cnt <= rst_cnt - 4'd1;
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) fe_rst <= 1'b1;
        else        fe_rst <= flush || (rst_cnt != 4'd0);
    end

    /* ================= 前端 + 压缩 ================= */

    wire signed [15:0] fe_din;
    wire               fe_din_vld, fe_din_ready;
    wire signed [15:0] fe_dout;
    wire               fe_dout_vld;

    wire signed [39:0] b0, b1, b2, b3;
    wire signed [39:0] ylp0, ylp1, ylp2, acc_raw;

    /* 录音字是 24 位补码（[23:0]，[31:24] 是符号扩展），核要 int16，
     * 取高 16 位就是去低 8 位的那一刀。 */
    assign fe_din = cap_tdata[31:16];

    /* ================= 每帧只喂一个采样（2026-10-27 修） =================
     *
     * i2s_duplex 的接收侧**每帧写两个字**（先 L 后 R，见 i2s_duplex.v 的
     * cap_st 1/2）。原来的写法把每一个字都当采样喂进前端，于是：
     *
     *   ① 前端实际跑在 97.7 kHz，四个分频点在物理上全部翻倍
     *      （500/1000/2000 → 1k/2k/4k）；1 kHz 的单音会落到"2 kHz 以上"
     *      那一段里去；
     *   ② 左右声道被交错塞进同一条 193 抽头延迟线。
     *
     * 10-25 那条验收记录（`und` 60 秒不涨 = 播放侧按 97.7 k 字/秒供数）
     * 就是 ① 的直接证据。板子一直哑着，所以耳朵没机会发现。
     *
     * 现在：L 那一个字进前端，R 那个字**照弹不喂**（不弹会堵住 FIFO）。
     * 帧率 48.83 kHz ⇒ 前端 3 拍/采样只占 2048 拍里的 3 拍，余量不变。
     *
     * 为什么用"翻转"而不是让 i2s_duplex 多给一个声道标志：这个模块的
     * 上游只有一个源，且 flush 之后第一个字必然是 L；万一 FIFO 溢出错开
     * 一个，L/R 内容本来就相同，翻转错位无副作用。加端口要动 fir_dma
     * 那条已经在跑的链路，不值。
     */
    reg rx_ch;                   // 0 = 下一个字是 L（喂前端），1 = R（丢掉）

    /* 输入节流：播放 FIFO 水位 + 输出 FIFO 占用 到目标值就停手。
     * run=0 时也不收 —— 那时候 i2s_duplex 不往外放，收了只会把播放 FIFO 灌满。 */
    wire [OUT_AW:0] ofifo_cnt;
    wire            room = run && ((tx_lvl + ofifo_cnt) < TX_TARGET);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || flush) rx_ch <= 1'b0;
        else if (cap_tvalid && cap_tready) rx_ch <= ~rx_ch;
    end

    assign cap_tready = rx_ch ? 1'b1 : (room && fe_din_ready);
    assign fe_din_vld = cap_tvalid && cap_tready && !rx_ch;

    fir_mb_fe u_fe (
        .clk       (clk),
        .rst       (fe_rst),
        .din       (fe_din),
        .din_vld   (fe_din_vld),
        .din_ready (fe_din_ready),
        .ylp0      (ylp0),
        .ylp1      (ylp1),
        .ylp2      (ylp2),
        .band0     (b0),
        .band1     (b1),
        .band2     (b2),
        .band3     (b3),
        .acc_raw   (acc_raw),
        .dout      (fe_dout),
        .dout_vld  (fe_dout_vld)
    );

    wire signed [15:0] drc_y;
    wire               drc_y_vld;

    fir_drc4 u_drc (
        .clk    (clk),
        .rst    (fe_rst),
        .in_vld (fe_dout_vld),
        .band0  (b0),
        .band1  (b1),
        .band2  (b2),
        .band3  (b3),
        .y      (drc_y),
        .y_vld  (drc_y_vld)
    );

    /* 直取 ctrl[1]，不锁存：压缩器一直在跟踪包络（in_vld 不看 drc_en），
     * 所以中途切只是"过压缩"和"不过压缩"两个值换一下，会有一声轻微的跳变，
     * 不会把状态搞乱。锁存反而更糟 —— 复位期间 ctrl[1] 通常是 0，
     * 之后想改 drc_en 就再也改不了了。 */
    wire signed [15:0] out_q   = ctrl[1] ? drc_y    : fe_dout;
    wire               out_vld = ctrl[1] ? drc_y_vld : fe_dout_vld;

    /* ================= 输出 FIFO ================= */
    /* 前端输出没有 ready，而 i2s_duplex 的播放入口可能有一拍不 ready
     * （TX FIFO 满）。中间垫一级小的，顺便把 valid 从一个脉冲变成可保持的。 */

    reg  [15:0]        ofifo [0:OUT_DEPTH-1];
    reg  [OUT_AW:0]    owptr, orptr;

    assign ofifo_cnt = owptr - orptr;

    wire ofifo_full = (ofifo_cnt == OUT_DEPTH);
    wire ofifo_rd   = ply_tvalid && ply_tready;

    wire [15:0] ofifo_q = ofifo[orptr[OUT_AW-1:0]];

    /* ---- 一个采样写两份（2026-10-27 与上面的取一修配套） ----
     * i2s_duplex 的发送侧每帧从 FIFO 取两个字（hold0 / hold1）。收侧现在每帧
     * 只产一个采样，所以这里必须把一个采样写两遍；否则每帧少一个字，`und`
     * 会一直涨，声音是断续的。
     *
     * 用 2 位计数而不是"写一次立刻再写一次"：写使能是寄存的，同一个 always
     * 块里连写两拍会让第二拍的数据来源在 `dup_half` 翻转后变成新采样。
     *
     * 以后做双耳（B5）时，两份分别写 drc_l / drc_r 即可，结构不用改。 */
    reg [15:0] dup_q;
    reg [1:0]  dup_left;      // 还剩几份没写
    reg        dup_wr;
    reg        dup_drop;      // 一对没写出去（正常恒 0）
    wire [15:0] ofifo_din;

    assign ofifo_wr  = dup_wr;
    assign ofifo_din = dup_q;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || flush) begin
            dup_q    <= 16'd0;
            dup_left <= 2'd0;
            dup_wr   <= 1'b0;
            dup_drop <= 1'b0;
        end else begin
            dup_wr   <= 1'b0;
            dup_drop <= 1'b0;
            if (dup_left != 2'd0) begin
                if (!ofifo_full) begin
                    dup_wr   <= 1'b1;
                    dup_left <= dup_left - 2'd1;
                end
            end
            if (out_vld) begin
                if (dup_left == 2'd0) begin
                    dup_q    <= out_q;
                    dup_left <= 2'd2;
                end else begin
                    dup_drop <= 1'b1;     // 上一对还没写完又来一个（不该发生）
                end
            end
        end
    end

    always @(posedge clk) begin
        if (ofifo_wr) ofifo[owptr[OUT_AW-1:0]] <= ofifo_din;
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || flush) begin
            owptr <= {(OUT_AW+1){1'b0}};
            orptr <= {(OUT_AW+1){1'b0}};
        end else begin
            if (ofifo_wr) owptr <= owptr + 1'b1;
            if (ofifo_rd) orptr <= orptr + 1'b1;
        end
    end

    /* 播放字：i2s_duplex 取 hold[23:0] 当 24 位采样，MSB 先发。
     * int16 → 24 位必须**左移 8 位**（低位补 0），不能符号扩展：
     *   {16{s}, s} 取 [23:0] = {s[15], s[15:0]}，高 8 位变成符号位，
     *   样本实际被右移 8 位 —— 音量小 48 dB（2026-10-27 查出的真 bug，
     *   仿真里 TX 时序与官方逐拍一致，但幅度是 1/256）。
     * 正确的 24 位字 = {s, 8'h00}，所以 32 位字写成 {8'h00, s, 8'h00}。 */
    assign ply_tvalid = (ofifo_cnt != 0);
    assign ply_tdata  = {8'h00, ofifo_q, 8'h00};

    /* ================= 统计 ================= */
    /* stat2[15:0]  输出 FIFO 溢出次数（正常必须一直是 0）
     * stat2[31:16] 跑起来以后见过的最大播放 FIFO 水位（调 TX_TARGET 用） */

    reg [15:0] oovf_cnt;
    reg [15:0] tx_peak;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || flush) begin
            tx_peak <= 16'd0;
        end else if (tx_lvl > tx_peak) begin
            tx_peak <= tx_lvl;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            oovf_cnt <= 16'd0;
        end else begin
            if (dup_drop) oovf_cnt <= oovf_cnt + 16'd1;
            if (ctrl[8])  oovf_cnt <= 16'd0;
        end
    end

    assign stat2 = {tx_peak, oovf_cnt};

    /* ================= 调试计数 ================= */

    reg [15:0] din_cnt, dout_cnt;
    reg        rst_at_din;      // 喂数的那一刻前端是不是被复位着（粘住）
    reg        out_ever;        // flush 之后有没有出过数

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || flush) begin
            din_cnt    <= 16'd0;
            dout_cnt   <= 16'd0;
            rst_at_din <= 1'b0;
            out_ever   <= 1'b0;
        end else begin
            if (fe_din_vld) din_cnt <= din_cnt + 16'd1;
            if (out_vld)    dout_cnt <= dout_cnt + 16'd1;
            if (fe_din_vld && fe_rst) rst_at_din <= 1'b1;
            if (out_vld)              out_ever   <= 1'b1;
        end
    end

    /* dbg0[15:0]  喂进前端的字数   dbg0[31:16] 前端吐出的字数
     * dbg1[1:0]   前端当前相位 ph（0=PRIME 1=P0 2=P1 3=P2；停在 0/3 都是"能收不吐"）
     * dbg1[2]     fe_rst 当前值
     * dbg1[3]     rst_at_din —— 出现过"复位期间还在喂数"就置 1
     * dbg1[4]     out_ever
     * dbg1[5]     room
     * dbg1[6]     cap_tready
     * dbg1[7]     cap_tvalid
     * dbg1[13:8]  ofifo_cnt */
    assign dbg0 = {dout_cnt, din_cnt};
    assign dbg1 = {18'b0, u_fe.ph, ofifo_cnt, cap_tvalid, cap_tready, room,
                   out_ever, rst_at_din, fe_rst};

endmodule
