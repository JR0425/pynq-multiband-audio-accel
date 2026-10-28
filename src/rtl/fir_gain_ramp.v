/* fir_gain_ramp —— 四段增益斜坡（2026-10-28）
 *
 * ================= 为什么需要它 =================
 *
 * 网页上拖增益滑杆，一次能从 −24 dB 跳到 +24 dB（Q7.8 里是 16 → 4063）。
 * 如果把目标值直接塞进乘法器，输出就是一次阶跃 —— 耳机里"咔"一声。
 * 分几档台阶也没用：那只是把一声咔变成几声咔。唯一正确的做法是
 * **逐采样**往目标逼近，让每次的变化量小到听不出来。
 *
 * ================= 算法 =================
 *
 *   step = (tgt − cur) >>> div        （算术右移，负数也往目标走）
 *   除不尽（step 变成 0）时至少走 1 个 LSB
 *   cur += step
 *
 * `div = 6` 时时间常数是 64 个采样，48 kHz 下 ≈ 1.3 ms。
 * 这个"至少 1 个 LSB"很关键：没有它，差值小于 2^div 时永远停在半路，
 * busy 一直挂着，网页的"参数已生效"指示灯永远不灭。
 *
 * ================= 什么时候不动 =================
 *
 * 只在 `tick` 为高时走一步（fir_pl_direct 里接 fe_dout_vld，即每个采样一次），
 * 这样 4 段共用一个时钟节拍，不会因为前端 3 拍一个采样而走得过快。
 * `clr` 为高（flush / 复位）时直接对齐目标：重新开始不该带着旧斜坡跑。
 */
`timescale 1ns / 1ps

module fir_gain_ramp (
    input  wire               clk,
    input  wire               rst_n,
    input  wire               clr,        // 复位/flush：直接对齐目标
    input  wire               tick,       // 每个采样一个脉冲
    input  wire signed [15:0] tgt0,
    input  wire signed [15:0] tgt1,
    input  wire signed [15:0] tgt2,
    input  wire signed [15:0] tgt3,
    input  wire [15:0]        div,        // 移位量，>15 按 15 算
    output wire signed [15:0] cur0,
    output wire signed [15:0] cur1,
    output wire signed [15:0] cur2,
    output wire signed [15:0] cur3,
    output wire               busy        // 还有段没爬到目标
);

    wire [4:0] sh = (div > 16'd15) ? 5'd15 : div[4:0];

    reg signed [15:0] c0, c1, c2, c3;

    /* 上电 = 0（静音），由第一个 clr / 写寄存器 / tick 爬到目标。
     * 不能再用复位装载 tgt：异步复位装非零值会被综合器拉进数据链，
     * 正是那颗 -3.5 ns 长链的起点。Xilinx 上电按 initial 装载，同 fir_pl_regs。 */
    initial begin
        c0 = 16'sd0; c1 = 16'sd0; c2 = 16'sd0; c3 = 16'sd0;
    end

    /* 17 位够用：两个 16 位有符号相减，范围 ±65535 */
    wire signed [16:0] d0 = {tgt0[15], tgt0} - {c0[15], c0};
    wire signed [16:0] d1 = {tgt1[15], tgt1} - {c1[15], c1};
    wire signed [16:0] d2 = {tgt2[15], tgt2} - {c2[15], c2};
    wire signed [16:0] d3 = {tgt3[15], tgt3} - {c3[15], c3};

    function automatic signed [16:0] step_of;
        input signed [16:0] d;
        input [4:0]         s;
        reg   signed [16:0] r;
        begin
            r = d >>> s;
            if (r == 17'sd0 && d != 17'sd0)
                step_of = (d > 17'sd0) ? 17'sd1 : -17'sd1;
            else
                step_of = r;
        end
    endfunction

    function automatic signed [15:0] clamp16;
        input signed [16:0] v;
        begin
            if (v > 17'sd32767)       clamp16 = 16'sd32767;
            else if (v < -17'sd32768) clamp16 = -16'sd32768;
            else                      clamp16 = v[15:0];
        end
    endfunction

    /* ================= 两级流水（2026-10-28 时序修复） =================
     *
     * 最初一拍串完「减法 → 17 位桶形移位 → 补 1 LSB → 加法 → clamp」，
     * 19 级逻辑 13.3 ns，100 MHz 域 WNS 打到 -3.5 ns。
     * 现在拆成两拍：拍 1 只算 step，拍 2 只做加法 + clamp，每段各留 1 级
     * 寄存器，两级都 ≤10 ns。
     *
     * 行为差异只有一个：step 用的是「上一拍的 c」算的。但 tick 是每个采样
     * 一个脉冲（前端 3 拍一个采样），两次 tick 之间 c 不变，所以 step 永远
     * 是对的 —— 斜坡的数学一个字没变，只是晚了一拍生效（20 ns，听不见）。
     * 这也是为什么 d/clr 语义不用动：clr 那拍 go_q 被压掉，下一拍 c 已经
     * 对齐目标，不会出现「清零拍还往前爬一步」。
     *
     * 复位分支从「装载 tgt」改成「装 0」：异步复位若要装非零值，综合器只能
     * 把 rst 拉进数据路径（每个位一个 LUT），那条 19 级长链的起点就是它；
     * 装 0 可以直接用 FF 的 CLR，复位彻底退出数据链。上电 c=0 意味着上电
     * 头 1.3 ms 是静音的（从 0 爬到默认 256），顺便把上电爆音也免了。
     */
    reg go_q;
    reg signed [16:0] s0_q, s1_q, s2_q, s3_q;

    always @(posedge clk) begin
        go_q <= rst_n && tick && !clr;
        s0_q <= step_of(d0, sh);
        s1_q <= step_of(d1, sh);
        s2_q <= step_of(d2, sh);
        s3_q <= step_of(d3, sh);
    end

    always @(posedge clk) begin
        if (clr) begin
            c0 <= tgt0;
            c1 <= tgt1;
            c2 <= tgt2;
            c3 <= tgt3;
        end else if (go_q) begin
            c0 <= clamp16({c0[15], c0} + s0_q);
            c1 <= clamp16({c1[15], c1} + s1_q);
            c2 <= clamp16({c2[15], c2} + s2_q);
            c3 <= clamp16({c3[15], c3} + s3_q);
        end
    end

    assign cur0 = c0;
    assign cur1 = c1;
    assign cur2 = c2;
    assign cur3 = c3;

    assign busy = (c0 != tgt0) | (c1 != tgt1) | (c2 != tgt2) | (c3 != tgt3);

endmodule
