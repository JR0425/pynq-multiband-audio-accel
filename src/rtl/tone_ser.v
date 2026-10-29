/* tone_ser —— 只吃外部 BCLK/LRCLK 的方波发送器（2026-10-28 凌晨）

为什么单独做这一个：
  我们已经测出「官方 iis_ser + 我们自己的计数器时钟」= 无声。
  那么剩下的可能只有两半：发送器、时钟。要把它们分开，就需要一颗
  **时钟来自别处、发送器仍是我们这套** 的位流 —— 那就是本模块：
  它自己不产生 BCLK/LRCLK，而是**接收官方音频 IP 输出的那两根**，
  再配一个 iis_ser 把方波送出去。

  接进 `build_fir.tcl` / `build_audio.tcl` 那类**含官方音频 IP** 的设计：
      ip.bclk  ──┬─> 外部端口 bclk
                 └─> tone_ser.bclk
      ip.lrclk ──┬─> 外部端口 lrclk
                 └─> tone_ser.lrclk
      sdata_o  = sel ? tone_ser.sdata : ip.sdata_o

  sel=0 -> 出货那条路（对照，应该响）
  sel=1 -> 官方时钟 + **我们的**发送器
         响了 => 我们的发送器没问题，问题在时钟产生方式
         不响 => 发送器有问题

用 440 Hz 方波（相位每帧 +590），因为它不需要 PS 参与，排除数据源这个变量。
*/

`timescale 1ns / 1ps

module tone_ser (
    input  wire clk,        /* 系统时钟，只用来跑相位累加器 */
    input  wire bclk,       /* 来自官方音频 IP 的 BCLK */
    input  wire lrclk,      /* 来自官方音频 IP 的 LRCLK */
    input  wire en,         /* 1 = 出声 */
    output wire sdata
);

    /* ---- 音源：440 Hz 方波，每帧走一步 ---- */
    reg [15:0] ph;
    reg        lr_d;
    wire       sample_tick = lrclk & ~lr_d;

    always @(posedge clk) begin
        lr_d <= lrclk;
        if (sample_tick && en) ph <= ph + 16'd590;   /* 440 Hz @ 48.83 kHz */
    end

    /* 24 位、MSB 先发；±0x200000 = −12 dBFS */
    wire [23:0] tone24 = ph[15] ? 24'h200000 : 24'hE00000;

    /* ---- 官方发送器，用外部给进来的时钟 ---- */
    iis_ser u_ser (
        .CLK_100MHZ (clk),
        .SCLK       (bclk),
        .LRCLK      (lrclk),
        .SDATA      (sdata),
        .EN         (en),
        .LDATA      (tone24),
        .RDATA      (tone24)
    );

endmodule
