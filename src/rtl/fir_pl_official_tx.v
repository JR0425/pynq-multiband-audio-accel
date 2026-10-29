/* fir_pl_official_tx —— 用**官方 iis_ser.vhd** 当发送器的对照位流（2026-10-27）
 *
 * 为什么做这一颗：
 *   我们的 `i2s_duplex` 发送逻辑与官方 `iis_ser.vhd` 在同一个仿真里、同一根
 *   bclk/lrclk、同一份数据下 **逐拍 100000 拍 0 差异**；引脚网表里 `sdata_o`
 *   也是真实 FDCE 驱动；codec 的 ADC 在我们的时钟下还能正常录音。
 *   可 DAC 就是不出声。软件侧能查的已经查完，只剩"我们的时钟是不是真到了
 *   codec"这一条没法用软件证明。
 *
 *   这颗位流把"发送器"换成官方的：时钟照旧由本模块自己的计数器产生
 *   （和官方 user_logic.vhd 一样：bclk = cntr[4]，lrclk = cntr[10]）。
 *
 *     · 官方发送器 + 我们的时钟 → 有声  ⇒ 我们 `i2s_duplex` 的发送段有问题
 *     · 官方发送器 + 我们的时钟 → 无声  ⇒ 时钟/板上那一侧的问题，软件到头了
 *
 * 端口和 `fir_pl_direct` 完全一致，所以 `build_audio_pl.tcl` 只改两行就能编。
 *
 * ctrl[0] = 1 出声（440 Hz 方波，−12 dBFS）
 */

`timescale 1ns / 1ps

module fir_pl_official_tx (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [31:0] ctrl,
    output wire [31:0] stat0,
    output wire [31:0] stat1,
    output wire [31:0] stat2,
    output wire [31:0] dbg0,
    output wire [31:0] dbg1,

    output wire        bclk,
    output wire        lrclk,
    output wire        sdata_o,
    input  wire        sdata_i
);

    /* ---- 时钟：和官方 user_logic.vhd 第 201-203 行一模一样 ---- */
    /* 2026-10-27 深夜：**不再带复位**，和官方 IP 完全一致。
     * 官方 user_logic.vhd 的 clk_cntr 是纯自由计数：
     *     process(Bus2IP_Clk) if rising_edge then clk_cntr <= clk_cntr + 1;
     * 没有 rst。我们原来写成 `if (!rst_n) cntr <= 0`，一旦
     * peripheral_aresetn 在运行中抖动，LRCLK 就会出现残脉冲，
     * codec 的帧同步可能永久错位 —— ADC 能自己恢复，DAC 不一定。
     * 这是"官方发送器 + 我们的时钟也不响"里唯一还没排除的时钟差异。 */
    reg [10:0] cntr = 11'd0;

    always @(posedge clk) begin
        cntr <= cntr + 11'd1;
    end

    wire sclk_i  = cntr[4];      // 100 MHz / 32  = 3.125 MHz
    wire lrclk_i = cntr[10];     // 100 MHz / 2048 = 48.83 kHz

    /* ---- 音源：440 Hz 方波，每帧走一步 ---- */
    reg [15:0] ph;
    reg        lr_d;
    wire       sample_tick = lrclk_i & ~lr_d;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ph   <= 16'd0;
            lr_d <= 1'b0;
        end else begin
            lr_d <= lrclk_i;
            if (sample_tick && ctrl[0]) ph <= ph + 16'd590;   // 440 Hz @ 48.83 kHz
        end
    end

    /* 24 位、MSB 先发；±0x200000 = −12 dBFS，正负各占一半 */
    wire [23:0] tone24 = ph[15] ? 24'h200000 : 24'hE00000;

    /* ---- 官方发送器（VHDL） ---- */
    iis_ser u_tx (
        .CLK_100MHZ (clk),
        .SCLK       (sclk_i),
        .LRCLK      (lrclk_i),
        .SDATA      (sdata_o),
        .EN         (ctrl[0]),
        .LDATA      (tone24),
        .RDATA      (tone24)
    );

    assign bclk  = sclk_i;
    assign lrclk = lrclk_i;

    /* ---- 状态量（这一颗只验出声，全部给 0） ---- */
    assign stat0 = 32'd0;
    assign stat1 = 32'd0;
    assign stat2 = 32'd0;
    assign dbg0  = {16'd0, ph};
    assign dbg1  = {31'd0, sdata_o};

endmodule
