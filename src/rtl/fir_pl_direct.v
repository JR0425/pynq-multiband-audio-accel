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
 * 参数：四段增益和 DRC 的阈值/压缩比/起停时间全部是运行期寄存器
 * （2026-10-28 批 2 把后四个也从 parameter 改成端口），网页拖滑杆即可。
 */

`timescale 1ns / 1ps

module fir_pl_direct #(
    parameter integer TX_TARGET = 128,   // 播放 FIFO 水位目标（字），直接进延迟
    parameter integer OUT_AW    = 5,     // 输出 FIFO 深度 = 2^OUT_AW 字
    parameter integer RST_CYC   = 8,     // flush 松开后再给前端几拍复位
    parameter integer OUT_GAIN  = 1      // 主增益（1 = 0 dB）。分频段增益已运行期可调，
                                         // 这一级留作整机总音量，默认不再额外放大
) (
    input  wire        clk,
    input  wire        rst_n,

    /* ctrl[0] run     =1 才往外放（i2s_duplex 的语义）
     * ctrl[1] drc_en  =1 走压缩通路，0 只过前端（直通/只做四段增益）
     * ctrl[2] flush   =1 两侧 FIFO 和前端一起清空
     * 其余位留给 i2s_duplex（[8] 清计数） */
    input  wire [31:0] ctrl,
    /* 四段运行期验配增益目标值（Q7.8，1.0 = 256），打包成 64 位：
     *   [63:48] band3  [47:32] band2  [31:16] band1  [15:0] band0
     * 打包而不是四个独立端口，是为了 BD 里只需要一个 xlslice 就能从
     * fir_pl_regs 的 128 位参数组里取出来（少三个单元、少三根线）。 */
    input  wire [63:0] gain_pack,
    /* 2026-10-28 批 2：DRC 四参数也是运行期，格式同 gain_pack 但每段 24 位
     * （Q2.22 有符号），段 i 在 [24i+23 : 24i]：
     *   thr_pack / ratio_pack / atk_pack / rel_pack
     * 每个都是 fir_pl_regs 的 128 位参数组低 96 位的 xlslice。 */
    input  wire [95:0] thr_pack,
    input  wire [95:0] ratio_pack,
    input  wire [95:0] atk_pack,
    input  wire [95:0] rel_pack,
    /* 2026-10-29：bank1（右耳）那套参数。双耳 = 两套处方，单麦分左右。
     * 两个 bank 用同一个模块例化两次，靠"拆乘法"把 DSP 从 20/套降到 12/套
     * （见 fir_drc4.v 文件头），否则 209 + 20 会超过 220。 */
    input  wire [63:0] gain_pack_b1,
    input  wire [95:0] thr_pack_b1,
    input  wire [95:0] ratio_pack_b1,
    input  wire [95:0] atk_pack_b1,
    input  wire [95:0] rel_pack_b1,
    input  wire [15:0] ramp_div,          // 斜坡移位量，6 ≈ 1.3 ms 收敛
    output wire        ramp_busy,         // 还有段没爬到目标（1 = 正在爬）
    output wire        ramp_busy_b1,
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

    /* ---- 2026-10-28 批 1：观察量 + 板上 WAV 音源 ----
     * env_b0/gr_b0：四段包络/衰减量（Q2.22，24 位符号扩展到 32），段0 在低位。
     *   env_b0/gr_b0 是左耳（bank0），env_b1/gr_b1 是右耳（bank1），两套 DRC 各一份。
     * peak_in/peak_out：每 768 采样（16 ms @48 kHz）窗内 |max|，窗末锁存。
     * ply_*：波表 BRAM 的 B 口（只读）。PS 经 axi_bram_ctrl 把 WAV 写进去
     *   （16 位样本放 [31:16]，与发送字同格式），ply_ctl[0]=en、[1]=loop，
     *   ctrl[5]=1 时它是音源（替代麦克风），地址按采样节拍递增。 */
    output wire [127:0] env_b0,
    output wire [127:0] gr_b0,
    output wire [127:0] env_b1,
    output wire [127:0] gr_b1,
    output wire [15:0]  peak_in,
    output wire [15:0]  peak_out,
    output wire [13:0]  ply_addr,
    output wire         ply_en,
    input  wire [31:0]  ply_rdata,
    input  wire [31:0]  ply_ctl,
    input  wire [31:0]  ply_len,

    /* ---- 2026-10-30：C3 抓取缓冲（波形/频谱）----
     * BD 里一块 4096×32 的 BRAM 挂在 axi_bram_ctrl 上（0x43C60000），
     * 这里只出写口：低 16 位 = 进核样本，高 16 位 = 限幅后左耳。
     * scope_snap=1 时停止采集（只停抓取，不影响音频），写指针经 0x6C 读回。
     * PS 侧从 ptr 往回读 4096 字就是快照前约 84 ms 的连续历史。 */
    output wire [11:0]  cap_addr,
    output wire [31:0]  cap_din,
    output wire         cap_we,
    input  wire         scope_snap,
    output wire [12:0]  scope_wr,

    /* ---- 2026-10-29 夜：测试音（B4 听力图闭环）+ MPO 限幅（B6） ----
     * tone_sel 选四段中心频率（0=250 1=750 2=1500 3=3000 Hz），tone_amp 是
     * Q1.15 幅度（0 = 静音）。原先这里写死 440 Hz / ±8000，只能证明"链路通"，
     * 没法按频段做测听。
     * mpo_th 是输出幅度上限（线性，0x7FFF = 满量程 = 不动作）；超过的部分
     * 按 1/4 斜率压（膝部软限）。mpo_cnt 是被限幅的采样数，mpo_peak 是见过的
     * 最大 |输出| —— 两个都是给网页读的。 */
    input  wire [1:0]   tone_sel,
    input  wire [15:0]  tone_amp,
    input  wire [15:0]  mpo_th,
    input  wire [31:0]  mpo_rel,
    output wire [31:0]  mpo_cnt,
    output wire [15:0]  mpo_peak,

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
     * 取高 16 位就是去低 8 位的那一刀。
     * 2026-10-28 批 1：ctrl[5]=1 且 ply_ctl[0]=1 时音源切波表 BRAM。
     * cap_tvalid 的节拍恒定（codec 一直收，静音时也是 48.8 k 字/s），
     * 所以 vld 不用换源，只换"值"。 */
    wire signed [15:0] mic_sample = cap_tdata[31:16];
    wire signed [15:0] ply_sample = ply_rdata[31:16];
    wire               ply_sel   = ctrl[5] && ply_ctl[0];
    assign fe_din = ply_sel ? ply_sample : mic_sample;

    /* ---- WAV 播放引擎：16384 字**环形缓冲**（寄存器表口径），PS 持续填充 ----
     * 地址 14 位自然环回（16384 = 2^14），不用 ply_len——PS 侧追着写，
     * RTL 只管按采样节拍读。BRAM 读 1 拍延迟，但地址每 3 拍才变一次，
     * 读数稳定对应当前地址。!en / flush / !rst_n 时地址归零。
     * 曲子有多长取决于 PS 填得多快（Python bulk 写 32 KB/0.3 s，余量足）。 */
    reg  [13:0] ply_cnt;
    wire        ply_adv = fe_din_vld && ply_sel;
    always @(posedge clk) begin
        if (!rst_n || flush || !ply_ctl[0])
            ply_cnt <= 14'd0;
        else if (ply_adv)
            ply_cnt <= ply_cnt + 14'd1;    /* 2^14 自然环回 */
    end
    assign ply_en   = ply_sel;
    assign ply_addr = ply_cnt;

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

    /* ================= 四段增益斜坡（2026-10-28 加） =================
     *
     * 拖滑杆一次能从 −24 dB 跳到 +24 dB，直接塞进乘法器就是一声"咔"。
     * 斜坡本身在 fir_gain_ramp 里（独立模块，好单独仿真）：
     *   step = (tgt − cur) >>> ramp_div，除不尽时至少走 1 个 LSB，
     *   div=6 → 时间常数 64 个采样 ≈ 1.3 ms @48 kHz。
     *
     * tick 用 fe_dout_vld：每个采样走一步，四段共用一个节拍。
     * clr 用 fe_rst：flush 之后直接对齐目标，不该带着旧斜坡跑。
     */
    wire signed [15:0] g0_cur, g1_cur, g2_cur, g3_cur;

    wire signed [15:0] g0_tgt = gain_pack[15:0];
    wire signed [15:0] g1_tgt = gain_pack[31:16];
    wire signed [15:0] g2_tgt = gain_pack[47:32];
    wire signed [15:0] g3_tgt = gain_pack[63:48];

    fir_gain_ramp u_ramp (
        .clk    (clk),
        .rst_n  (rst_n),
        .clr    (fe_rst),
        .tick   (fe_dout_vld),
        .tgt0   (g0_tgt),
        .tgt1   (g1_tgt),
        .tgt2   (g2_tgt),
        .tgt3   (g3_tgt),
        .div    (ramp_div),
        .cur0   (g0_cur),
        .cur1   (g1_cur),
        .cur2   (g2_cur),
        .cur3   (g3_cur),
        .busy   (ramp_busy)
    );

    /* bank1（右耳）的斜坡：同一套模块，目标值来自 gain_pack_b1。
     * 两个斜坡各自爬各自的，一双耳朵拖滑杆时不会互相拖。 */
    wire signed [15:0] h0_cur, h1_cur, h2_cur, h3_cur;

    fir_gain_ramp u_ramp_b (
        .clk    (clk),
        .rst_n  (rst_n),
        .clr    (fe_rst),
        .tick   (fe_dout_vld),
        .tgt0   (gain_pack_b1[15:0]),
        .tgt1   (gain_pack_b1[31:16]),
        .tgt2   (gain_pack_b1[47:32]),
        .tgt3   (gain_pack_b1[63:48]),
        .div    (ramp_div),
        .cur0   (h0_cur),
        .cur1   (h1_cur),
        .cur2   (h2_cur),
        .cur3   (h3_cur),
        .busy   (ramp_busy_b1)
    );

    wire [95:0] drc_env, drc_gr;   /* bank0（左）四段包络/衰减观察（4×24） */
    wire [95:0] drc_env_r, drc_gr_r; /* bank1（右） */

    /* ================= 双耳：两套处方，两套 DRC =================
     *
     * 单麦 → 同一个四段信号 → 左右各一套增益/阈值/压缩比/time constant。
     * 两套参数都由 fir_pl_regs 给（bank0 是 *_pack，bank1 是 *_pack_b1）。
     *
     * 为什么能放两份：fir_drc4 里两个 25×24 乘法拆成「18 位走 DSP + 6 位走 LUT」，
     * 一套从 20 个 DSP 降到 12 个，两份 24 个，整机 213/220。
     * 拆分是整数分解（B = Bhi·64 + Blo），逐位精确，tb_drc_gain/tb_drc_chan 全过。
     */
    wire signed [15:0] drc_y_l, drc_y_r;
    wire               drc_vld_l, drc_vld_r;

    fir_drc4 u_drc_l (
        .clk    (clk),
        .rst    (fe_rst),
        .in_vld (fe_dout_vld),
        .band0  (b0),
        .band1  (b1),
        .band2  (b2),
        .band3  (b3),
        .gain0  (g0_cur),
        .gain1  (g1_cur),
        .gain2  (g2_cur),
        .gain3  (g3_cur),
        .thr0   (thr_pack[23:0]),
        .thr1   (thr_pack[47:24]),
        .thr2   (thr_pack[71:48]),
        .thr3   (thr_pack[95:72]),
        .ratio0 (ratio_pack[23:0]),
        .ratio1 (ratio_pack[47:24]),
        .ratio2 (ratio_pack[71:48]),
        .ratio3 (ratio_pack[95:72]),
        .atk0   (atk_pack[23:0]),
        .atk1   (atk_pack[47:24]),
        .atk2   (atk_pack[71:48]),
        .atk3   (atk_pack[95:72]),
        .rel0   (rel_pack[23:0]),
        .rel1   (rel_pack[47:24]),
        .rel2   (rel_pack[71:48]),
        .rel3   (rel_pack[95:72]),
        .y      (drc_y_l),
        .y_vld  (drc_vld_l),
        .env_o  (drc_env),
        .gr_o   (drc_gr)
    );

    fir_drc4 u_drc_r (
        .clk    (clk),
        .rst    (fe_rst),
        .in_vld (fe_dout_vld),
        .band0  (b0),
        .band1  (b1),
        .band2  (b2),
        .band3  (b3),
        .gain0  (h0_cur),
        .gain1  (h1_cur),
        .gain2  (h2_cur),
        .gain3  (h3_cur),
        .thr0   (thr_pack_b1[23:0]),
        .thr1   (thr_pack_b1[47:24]),
        .thr2   (thr_pack_b1[71:48]),
        .thr3   (thr_pack_b1[95:72]),
        .ratio0 (ratio_pack_b1[23:0]),
        .ratio1 (ratio_pack_b1[47:24]),
        .ratio2 (ratio_pack_b1[71:48]),
        .ratio3 (ratio_pack_b1[95:72]),
        .atk0   (atk_pack_b1[23:0]),
        .atk1   (atk_pack_b1[47:24]),
        .atk2   (atk_pack_b1[71:48]),
        .atk3   (atk_pack_b1[95:72]),
        .rel0   (rel_pack_b1[23:0]),
        .rel1   (rel_pack_b1[47:24]),
        .rel2   (rel_pack_b1[71:48]),
        .rel3   (rel_pack_b1[95:72]),
        .y      (drc_y_r),
        .y_vld  (drc_vld_r),
        .env_o  (drc_env_r),
        .gr_o   (drc_gr_r)
    );

    /* env/gr 打包：四段 Q2.22（24 位）符号扩展到 32，段0 在最低 32 位。 */
    wire signed [23:0] e0 = drc_env[23:0];
    wire signed [23:0] e1 = drc_env[47:24];
    wire signed [23:0] e2 = drc_env[71:48];
    wire signed [23:0] e3 = drc_env[95:72];
    wire signed [23:0] w0 = drc_gr[23:0];
    wire signed [23:0] w1 = drc_gr[47:24];
    wire signed [23:0] w2 = drc_gr[71:48];
    wire signed [23:0] w3 = drc_gr[95:72];
    assign env_b0 = {{8{e3[23]}}, e3, {8{e2[23]}}, e2, {8{e1[23]}}, e1, {8{e0[23]}}, e0};
    assign gr_b0  = {{8{w3[23]}}, w3, {8{w2[23]}}, w2, {8{w1[23]}}, w1, {8{w0[23]}}, w0};

    wire signed [23:0] f0 = drc_env_r[23:0];
    wire signed [23:0] f1 = drc_env_r[47:24];
    wire signed [23:0] f2 = drc_env_r[71:48];
    wire signed [23:0] f3 = drc_env_r[95:72];
    wire signed [23:0] v0 = drc_gr_r[23:0];
    wire signed [23:0] v1 = drc_gr_r[47:24];
    wire signed [23:0] v2 = drc_gr_r[71:48];
    wire signed [23:0] v3 = drc_gr_r[95:72];
    assign env_b1 = {{8{f3[23]}}, f3, {8{f2[23]}}, f2, {8{f1[23]}}, f1, {8{f0[23]}}, f0};
    assign gr_b1  = {{8{v3[23]}}, v3, {8{v2[23]}}, v2, {8{v1[23]}}, v1, {8{v0[23]}}, v0};

    /* ---- 峰值表：每 768 采样（16 ms）窗内 |max|，窗末锁存 ----
     * 进侧看 fe_dout（处理前），出侧看 out_q（处理后）——评委一眼看出
     * "增益把小声抬上来了、压缩把大声按住了"。 */
    wire signed [15:0] out_q;   /* 前向声明，定义在下面（drc/直通 mux） */
    localparam integer PK_WIN = 768;
    reg  [9:0]  pk_cnt;
    reg  [15:0] pk_in_acc, pk_out_acc;
    reg  [15:0] peak_in_r, peak_out_r;
    wire [15:0] din_abs  = fe_dout[15] ? (~fe_dout + 16'sd1) : fe_dout;
    wire [15:0] dout_abs = out_q[15]   ? (~out_q   + 16'sd1) : out_q;
    wire        pk_tick  = (pk_cnt == PK_WIN[9:0] - 10'd1);
    always @(posedge clk) begin
        if (!rst_n || flush) begin
            pk_cnt     <= 10'd0;
            pk_in_acc  <= 16'd0;
            pk_out_acc <= 16'd0;
            peak_in_r  <= 16'd0;
            peak_out_r <= 16'd0;
        end else if (fe_din_vld) begin
            if (pk_tick) begin
                peak_in_r  <= (din_abs  > pk_in_acc)  ? din_abs  : pk_in_acc;
                peak_out_r <= (dout_abs > pk_out_acc) ? dout_abs : pk_out_acc;
                pk_cnt     <= 10'd0;
                pk_in_acc  <= 16'd0;
                pk_out_acc <= 16'd0;
            end else begin
                pk_cnt     <= pk_cnt + 10'd1;
                pk_in_acc  <= (din_abs  > pk_in_acc)  ? din_abs  : pk_in_acc;
                pk_out_acc <= (dout_abs > pk_out_acc) ? dout_abs : pk_out_acc;
            end
        end
    end
    assign peak_in  = peak_in_r;
    assign peak_out = peak_out_r;

    /* 直取 ctrl[1]，不锁存：压缩器一直在跟踪包络（in_vld 不看 drc_en），
     * 所以中途切只是"过压缩"和"不过压缩"两个值换一下，会有一声轻微的跳变，
     * 不会把状态搞乱。锁存反而更糟 —— 复位期间 ctrl[1] 通常是 0，
     * 之后想改 drc_en 就再也改不了了。 */
    assign        out_q   = ctrl[1] ? drc_y_l  : fe_dout;
    wire signed [15:0] out_q_r = ctrl[1] ? drc_y_r : fe_dout;   /* 旁路时左右相同 */
    wire               out_vld = ctrl[1] ? drc_vld_l : fe_dout_vld;

    /* ================= 内部测试音（ctrl[3]，2026-10-28 加） =================
     *
     * 为什么要它：话筒那一路实测只有 −75 dBFS（ADC 本身就没信号），
     * 于是"前端 → 压缩 → 输出"这一段在板子上永远听不出对错 —— 输入是静音，
     * 输出当然是静音，健康计数器也全都正常，没法区分"链路通"和"数据是 0"。
     *
     * ctrl[3]=1 时用 PL 内部产生的方波（频率由 tone_sel 选四段之一，幅度由 tone_amp 定）顶替前端输出，
     * 一个字节都不经过 ADC。听到声音就说明：播放 FIFO、i2s_duplex 发送、
     * codec 播放混音器、耳机功放、引脚这一整条在**这颗位流上**是通的。
     *
     * 相位累加器挂在 out_vld 上（每帧一次），所以频率严格按帧率算，
     * 和 ADC 那条路无关；切换瞬间可能有一下咔，测试用不介意。
     */
    reg  [16:0] tone_acc;
    reg         tone_sq;
    localparam integer FS_NOM    = 48828;      // 48.828 kHz（100 MHz / 2048）
    /* 四段中心频率的相位增量。
     *
     * **注意这个累加器不是 2^N 取模，是在 FS_NOM 处回绕，而且每回绕一次就翻转
     * 一次方波** —— 所以方波周期 = 2 次回绕，回绕率 = 增量（Hz）/ 1 秒，
     * 最终频率 = 增量 / 2。
     * 2026-10-30 板上量出来的：tone_sel=2 写 4027，抓取缓冲做 FFT 得到 2014.6 Hz
     * （正是 4027/2），说明原来按 "f·2^17/fs" 算的表整体偏高 2 倍。
     * 正确值是 2×目标频率：250→500  750→1500  1500→3000  3000→6000。
     * （旧表 440 那一版实际放的是 220 Hz，一直没人量过。） */
    wire [16:0] tone_inc = (tone_sel == 2'd0) ? 17'd500  :
                           (tone_sel == 2'd1) ? 17'd1500 :
                           (tone_sel == 2'd2) ? 17'd3000 : 17'd6000;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            tone_acc <= 17'd0;
            tone_sq  <= 1'b0;
        end else if (out_vld) begin
            if (tone_acc + tone_inc >= FS_NOM[16:0]) begin
                tone_acc <= tone_acc + tone_inc - FS_NOM[16:0];
                tone_sq  <= ~tone_sq;
            end else begin
                tone_acc <= tone_acc + tone_inc;
            end
        end
    end

    wire signed [15:0] tone_amp_s = {1'b0, tone_amp[14:0]};

    /* ================= 输出固定增益（2026-10-28 加） =================
     *
     * 板上实测：四段都是 0 dB（前端单位增益），对着话筒说话只有 −25 dBFS 上下，
     * 送进耳机几乎听不见（吹气这种宽带大声能听见、说话听不见），
     * 听上去就像"根本没接话筒"。
     *
     * 2026-10-28 晚：四段 GAIN 已经端口化（来自 fir_pl_regs 的 GAIN[0..3]），
     * 所以这里的 +18 dB 临时档撤掉，OUT_GAIN 回归 1（0 dB），留给整机总音量。
     * **默认增益是 0 dB**（寄存器出厂值 256），说话要靠软件写增益才听得见 ——
     * 启动脚本 pl_demo_check.py / pl_direct_probe.py 都会先写一遍 +18 dB。
     *
     * 注意旁路（ctrl[1]=0）走 fe_dout，不经过四段增益，所以 A/B 对比时
     * 两边的响度不一样，这是对的：A/B 就是要"不补偿"和"按处方补偿"。
     */
    wire signed [31:0] g_mul_l = out_q   * OUT_GAIN;
    wire signed [31:0] g_mul_r = out_q_r * OUT_GAIN;
    wire signed [15:0] g_sat_l = (g_mul_l >  32'sd32767) ?  16'sd32767 :
                                 (g_mul_l < -32'sd32768) ? -16'sd32768 :
                                 g_mul_l[15:0];
    wire signed [15:0] g_sat_r = (g_mul_r >  32'sd32767) ?  16'sd32767 :
                                 (g_mul_r < -32'sd32768) ? -16'sd32768 :
                                 g_mul_r[15:0];

    wire signed [15:0] tone_out = ctrl[3] ? (tone_sq ? tone_amp_s : -tone_amp_s) : 16'sd0;

    wire signed [15:0] out_l = ctrl[3] ? tone_out : g_sat_l;
    wire signed [15:0] out_r = ctrl[3] ? tone_out : g_sat_r;

    /* ================= MPO 限幅（B6，2026-10-29 夜） =================
     *
     * 听力保护的输出上限：|x| 超过 mpo_th 的部分按 1/4 斜率继续涨（膝部软限），
     * 不硬削 —— 硬削出方波，谐波一片，听感是"破音"而不是"被限制"。
     *
     *   |x| <= th :  y = x
     *   |x| >  th :  y = sign(x)·( th + (|x|-th)/4 )
     *
     * mpo_th 出厂 0x7FFF（满量程）→ 阈值等于最大值，等于不动作。
     * 用户在网页上把 MPO 拉到 -10 dBFS 之类的值，超出的动态范围就被压住，
     * 保护耳朵（也是验配软件里的标准做法）。
     */
    /* ---- 一级流水：out_l/out_r 先打一拍再进限幅 ----
     * 2026-10-29 时序：tone_amp → 测试音 mux → MPO（取绝对值、比门限、减、右移 2、
     * 加、钳位、恢复符号）→ pr_l 串了 18 级逻辑 / 11.9 ns，WNS −2.25 ns。
     * 在 ctrl[3] 与门限之后切一刀，两段各 ~6 ns。
     * out_vld 每帧才来一次，多一拍只是端到端多 10 ns（噪声级）。 */
    reg signed [15:0] xl_q, xr_q;
    reg               xvld_q;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || flush) begin
            xl_q   <= 16'sd0;
            xr_q   <= 16'sd0;
            xvld_q <= 1'b0;
        end else begin
            xl_q   <= out_l;
            xr_q   <= out_r;
            xvld_q <= out_vld;
        end
    end

    wire signed [16:0] th17  = {1'b0, mpo_th[14:0]};
    wire signed [16:0] x17_l = {xl_q[15], xl_q};
    wire signed [16:0] x17_r = {xr_q[15], xr_q};
    wire signed [16:0] a17_l = x17_l[16] ? -x17_l : x17_l;
    wire signed [16:0] a17_r = x17_r[16] ? -x17_r : x17_r;
    wire signed [16:0] d17_l = a17_l - th17;
    wire signed [16:0] d17_r = a17_r - th17;

    /* ---- 第二级流水：膝部运算再切一刀 ----
     * 第一拍做 |x|、比门限、算差值；第二拍做 >>>2、加 th、钳位、恢复符号。
     * 只切一刀时最差路径变成 xl_q→MPO→pr_l，WNS 还剩 −0.19 ns。 */
    reg signed [16:0] aq_l, aq_r, dq_l, dq_r;
    reg               gtq_l, gtq_r, sq_l, sq_r;
    reg               vld2_q;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || flush) begin
            aq_l  <= 17'sd0;  aq_r  <= 17'sd0;
            dq_l  <= 17'sd0;  dq_r  <= 17'sd0;
            gtq_l <= 1'b0;    gtq_r <= 1'b0;
            sq_l  <= 1'b0;    sq_r  <= 1'b0;
            vld2_q <= 1'b0;
        end else begin
            aq_l  <= a17_l;
            aq_r  <= a17_r;
            dq_l  <= d17_l;
            dq_r  <= d17_r;
            gtq_l <= (a17_l > th17);
            gtq_r <= (a17_r > th17);
            sq_l  <= xl_q[15];
            sq_r  <= xr_q[15];
            vld2_q <= xvld_q;
        end
    end

    wire signed [16:0] y17_l = gtq_l ? (th17 + (dq_l >>> 2)) : aq_l;
    wire signed [16:0] y17_r = gtq_r ? (th17 + (dq_r >>> 2)) : aq_r;
    wire signed [16:0] yc_l  = (y17_l > 17'sd32767) ? 17'sd32767 : y17_l;
    wire signed [16:0] yc_r  = (y17_r > 17'sd32767) ? 17'sd32767 : y17_r;
    wire signed [15:0] mpo_l = sq_l ? -yc_l[15:0] : yc_l[15:0];
    wire signed [15:0] mpo_r = sq_r ? -yc_r[15:0] : yc_r[15:0];

    wire mpo_hit = gtq_l || gtq_r;

    reg  [31:0] mpo_cnt_r;
    reg  [16:0] mpo_peak_r;

    /* 峰值量的是**限幅之后**的输出（寄存器表 0x054「限幅后输出峰值」），
     * 而且要取两只耳朵里大的那个 —— 2026-10-29 板上探针发现原来量的是
     * aq_l（限幅前的左耳），于是"限幅明明在动、峰值却不变"。
     *
     * 直接拿 pr_l/pr_r（真正写进输出 FIFO 的那两个采样）算：既符合"限幅后"，
     * 又不会让 mpo_th 穿过整条限幅器再进新触发器 —— 接在组合的 mpo_l/mpo_r
     * 上试过，路径 mpo_th_reg→abs_q_reg 串太长，WNS 掉到 −1.574 ns。 */
    reg [15:0] pr_l, pr_r;

    /* 取负前必须先**符号扩展**到 17 位：{1'b0, pr_l} 是零扩展，-8000 会变成
     * 57536，取负再得 73536，于是任何负采样都被钳成 32767（2026-10-29 板上
     * 探针读到的 PEAK 恒等于 32767 就是这个）。 */
    wire [16:0] abs_l = pr_l[15] ? (17'd0 - {pr_l[15], pr_l}) : {1'b0, pr_l};
    wire [16:0] abs_r = pr_r[15] ? (17'd0 - {pr_r[15], pr_r}) : {1'b0, pr_r};
    wire [16:0] abs_m = (abs_r > abs_l) ? abs_r : abs_l;

    reg [16:0] abs_q;
    reg        vld3_q, vld4_q;

    /* pr_* 比 vld2_q 晚一拍、abs_q 再晚一拍，所以取峰值看的是 vld4_q。 */
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || flush) begin
            abs_q      <= 17'd0;
            vld3_q     <= 1'b0;
            vld4_q     <= 1'b0;
            mpo_cnt_r  <= 32'd0;
            mpo_peak_r <= 17'd0;
        end else begin
            abs_q  <= abs_m;
            vld3_q <= vld2_q;
            vld4_q <= vld3_q;
            if (vld2_q && mpo_hit)            mpo_cnt_r  <= mpo_cnt_r + 32'd1;
            if (vld4_q && abs_q > mpo_peak_r) mpo_peak_r <= abs_q;
        end
    end

    assign mpo_cnt  = mpo_cnt_r;
    assign mpo_peak = (mpo_peak_r > 17'd32767) ? 16'h7FFF : mpo_peak_r[15:0];

    /* ================= 抓取缓冲写口（C3，2026-10-30） ================= */
    /* 每采样写一个字：低 16 位进核、高 16 位出核（限幅后左耳）。
     * 先打一拍再写 BRAM —— mpo_l 和 fe_din 都是组合来的，直接进 BRAM 端口会
     * 多一条"组合逻辑 → BRAM 建立时间"的路径，而当前 WNS 只有 +0.1 ns。
     * scope_snap=1 冻结写指针：页面读 16 KB 要几毫秒，不冻的话读到的一半已经
     * 被新样本覆盖，波形会断成几截。只停抓取，音频通路照跑。 */
    reg signed [15:0] fe_din_q;
    reg [31:0] cap_q;
    reg        cap_qv;
    reg [11:0] cap_wr;
    reg [11:0] cap_ptr;

    always @(posedge clk) begin
        if (fe_din_vld) fe_din_q <= fe_din;
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || flush) begin
            cap_q   <= 32'd0;
            cap_qv  <= 1'b0;
            cap_wr  <= 12'd0;
            cap_ptr <= 12'd0;
        end else begin
            cap_qv <= 1'b0;
            if (vld2_q && !scope_snap) begin
                cap_q   <= {mpo_l, fe_din_q};
                cap_qv  <= 1'b1;
                cap_wr  <= cap_wr + 12'd1;
            end
            if (scope_snap) cap_ptr <= cap_wr;
        end
    end

    assign cap_we   = cap_qv;
    assign cap_din  = cap_q;
    assign cap_addr = cap_wr;
    assign scope_wr = {1'b0, cap_ptr};

    /* ================= 输出 FIFO ================= */
    /* 前端输出没有 ready，而 i2s_duplex 的播放入口可能有一拍不 ready
     * （TX FIFO 满）。中间垫一级小的，顺便把 valid 从一个脉冲变成可保持的。 */

    reg  [15:0]        ofifo [0:OUT_DEPTH-1];
    reg  [OUT_AW:0]    owptr, orptr;

    assign ofifo_cnt = owptr - orptr;

    wire ofifo_full = (ofifo_cnt == OUT_DEPTH);
    wire ofifo_rd   = ply_tvalid && ply_tready;

    wire [15:0] ofifo_q = ofifo[orptr[OUT_AW-1:0]];

    /* ---- 一帧写两个声道（2026-10-29 双耳改造） ----
     * i2s_duplex 的发送侧每帧从 FIFO 取两个字（hold0 / hold1）。收侧每帧只产
     * 一个采样，所以必须往 FIFO 里塞两个。以前是"同一个采样写两遍"，
     * 现在左边写 out_l、右边写 out_r —— 双耳时两个值不同，单耳/旁路时相同。
     *
     * 用 2 位计数而不是"写一次立刻再写一次"：写使能是寄存的，同一个 always
     * 块里连写两拍会让第二拍的数据来源在计数翻转后变掉。
     */
    reg [1:0]  dup_left;      // 还剩几个字没写（2 = 左、右都还没写）
    reg        dup_wr;
    reg        dup_drop;      // 一对没写出去（正常恒 0）
    wire [15:0] ofifo_din;

    assign ofifo_wr  = dup_wr;
    /* 写的时候 dup_left 已经是"还剩几个没写"：先写的那拍是 1、后写的是 0。
     * 2026-10-29 板上测出两只耳朵听到的是同一路 —— dup_wr 比 dup_left 的递减
     * 晚一拍，原来按 (dup_left==2) 选左，实际写的时候早就是 1/0 了，两次都取了
     * pr_r。改成按 0/非 0 选，才是"先左后右"。 */
    assign ofifo_din = (dup_left == 2'd0) ? pr_r : pr_l;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || flush) begin
            pr_l     <= 16'd0;
            pr_r     <= 16'd0;
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
            if (vld2_q) begin
                if (dup_left == 2'd0) begin
                    pr_l     <= mpo_l;
                    pr_r     <= mpo_r;
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
