/* 单路低通 FIR —— 直接型（direct form），全并行，每拍吃一个采样
 *
 * ================== 它要跟什么逐位对上 ==================
 *
 * 对照物是 HLS 那版核（src/hls/fir_multiband.cpp）里同一个低通。
 * "逐位相同"不是目标本身，是**唯一能把"结构写错了"和"量化差一点"分开**的手段：
 * 差 1 个 LSB 时，输出文件会有几百个采样点对不上，看起来跟"移位下标写错"
 * 一模一样，靠耳朵或者看波形根本分不出来。
 *
 * 数值契约（来历见 tools/gen_rtl_coeffs.py 与 tools/ref_model_int.py 的文件头）：
 *
 *     din    int16，Q1.15。就是音频采样的那串比特，不是"数值换算"。
 *     coef   int18，Q1.17，有符号。系数表由 tools/gen_rtl_coeffs.py 生成，
 *            规则是 round_half_away(x * 2^17) —— 这条规则是拿 HLS 综合出来的
 *            系数 ROM 反推出来的，579 个系数逐个比，0 处不符。
 *     乘积   16 + 18 = 34 位有符号，**精确**，不丢位。
 *     累加   40 位有符号 = Q8.32。峰值只有 1.70e9，上限 5.50e11，**不会溢出**。
 *            → 定点加法精确，**而且与顺序无关**。
 *              所以下面这棵加法树想怎么摆就怎么摆，**不需要**去模仿 HLS 的
 *              5 路部分和（FIR_PARTIAL=5）。这条是整个"能逐位对拍"的支点。
 *     输出   out = sat16(acc >>> 17)
 *              17 = 32（累加器小数位）- 15（输出小数位）
 *              >>> 是算术右移 = 向下取整，对应 ap_fixed 的 AP_TRN
 *              最后饱和到 16 位有符号，对应 AP_SAT
 *
 * ================== 结构 ==================
 *
 *     拍 0   din_vld 拉高，抽头延迟线 sr 整体挪一格
 *     拍 0→1 195 个乘法各算各的（每个一个 DSP48），打一拍存下来
 *     拍 1→2 40 位平衡加法树（8 层），再打一拍 -> acc_raw / dout
 *
 *     延迟 2 拍，II = 1（源源不断时每拍出一个采样）。
 *     对照：HLS 那版是每采样 28 拍。**同样的 DSP 数，吞吐差 28 倍** ——
 *     因为 HLS 的 195 个乘法器是被 5 路部分和分批喂的，而这里每个乘法器
 *     每拍都在干活。
 *
 * ⚠️ 抽头延迟线在 RTL 里就是**寄存器之间接几根线**，零逻辑、零拍数。
 *    这件事在 HLS 那边是做不到的（那边的写法有环依赖，编译器只能拒绝展开，
 *    一拍一格地搬 194 次）—— 这是本文件存在的主要理由，见
 *    src/hls/fir_multiband.cpp 里 FIR_SHIFT_CHAIN 那段注释。
 *
 * ⚠️ 加法树按 2 的幂补零到 256 项，实际只用 195 项。
 *    多出来的 61 项是常数 0，被综合折掉一部分，但确实多花加法器
 *    （255 个 vs 理论最少 194 个）。第一版先求"对"，非 2 的幂的树后面再说。
 *
 * ⚠️ 系数是编译期常量，AMD 论坛上有实测：**常量系数 FIR 会被综合成
 *    LUT 里的 CSD 移位加法，一个 DSP 都不占**。板上只有 220 个 DSP，
 *    这条路要是跑偏了面积会很难看。所以乘法器上显式挂了 (* use_dsp = "yes" *)。
 */

`timescale 1ns / 1ps

module fir_lp #(
    parameter integer NTAP      = 193,          // 实际抽头数（只作记录）
    parameter integer NALLOC    = 195,          // 系数表长度（补齐后）
    parameter integer CW        = 18,           // 系数位宽，Q1.17
    parameter integer ACCW      = 40,           // 累加器位宽，Q8.32
    parameter integer SHIFT     = 17,           // 输出右移量
    parameter         COEF_FILE = "coeffs.mem"
) (
    input  wire                   clk,
    input  wire                   rst,
    input  wire signed [15:0]     din,
    input  wire                   din_vld,
    output reg  signed [ACCW-1:0] acc_raw,      // 累加器原值，给逐位对拍用
    output reg  signed [15:0]     dout,
    output reg                    dout_vld
);

    /* ---- 数一下加法树要几层 ---- */
    function integer clog2f;
        input integer v;
        integer i;
        begin
            clog2f = 0;
            for (i = v - 1; i > 0; i = i >> 1) clog2f = clog2f + 1;
        end
    endfunction

    localparam integer LOGNP = clog2f(NALLOC);
    localparam integer NPAD  = 1 << LOGNP;      // 补齐到 2 的幂

    /* ---- 系数表 ----
     * 固定从一个相对文件名读（默认 coeffs.mem），**不**从命令行传路径。
     * 上面这段注释值得留着，因为这是被两堵墙夹出来的选择：
     *   · 走 bin/xvlog.bat + `cmd /c`：cmd 会把整串参数**重新解析一遍引号**，
     *     `+COEF=D:\...\coeffs.mem` 到 xsim 手里变成裸的 `D:\...`，
     *     报错是 "Expected a switch but found D"，看着像参数名写错。
     *   · 直接起 bin/unwrapped/win64.o/xsim.exe：绕开了引号，
     *     但缺 libxsimverific.dll（那个 DLL 是靠 .bat 设 PATH 才找得到的），
     *     起不来。
     * → 结论：**命令行上不要传路径**。运行脚本把要用的那份 .mem 复制成
     *   coeffs.mem，模块只认这一个名字。三个滤波器就是三次复制。
     *   代价是模块不能同时例化三份（会抢同一个文件）—— 现在的用法是
     *   一次跑一个，正好不需要。 */
    reg signed [CW-1:0] coef [0:NALLOC-1];
    initial $readmemh(COEF_FILE, coef);

    /* ---- 抽头延迟线 ----
     * sr[0] 最新，sr[k] 是 k 拍前。
     * 非阻塞赋值让整条线在同一拍里一起挪 —— 硬件里这就是寄存器之间接几根线，
     * 不花任何逻辑。这是 RTL 相对 HLS 那版最大的结构性优势。 */
    reg signed [15:0] sr [0:NALLOC-1];

    integer i;
    always @(posedge clk) begin
        if (rst) begin
            for (i = 0; i < NALLOC; i = i + 1) sr[i] <= 16'sd0;
        end else if (din_vld) begin
            sr[0] <= din;
            for (i = 1; i < NALLOC; i = i + 1) sr[i] <= sr[i-1];
        end
    end

    /* ---- 乘法 + 打一拍 ----
     * 写成 ACCW 位接收：两个操作数都被符号扩展到 40 位再相乘，
     * 取低 40 位。因为真积只占 34 位，低 40 位就是它的符号扩展，**精确**。 */
    reg signed [ACCW-1:0] p [0:NPAD-1];

    genvar g;
    generate
    for (g = 0; g < NPAD; g = g + 1) begin : GEN_MUL
        if (g < NALLOC) begin
            (* use_dsp = "yes" *)
            wire signed [ACCW-1:0] pmul = sr[g] * coef[g];
            always @(posedge clk) p[g] <= pmul;
        end else begin
            always @(posedge clk) p[g] <= {ACCW{1'b0}};
        end
    end
    endgenerate

    /* ---- 40 位平衡加法树 ----
     * 逐层对半折。写成 2 维数组，层数由 LOGNP 推出来，抽头数改了不用改这里。 */
    wire signed [ACCW-1:0] tr [0:LOGNP][0:NPAD-1];

    generate
    for (g = 0; g < NPAD; g = g + 1) begin : GEN_T0
        assign tr[0][g] = p[g];
    end
    endgenerate

    genvar L, k;
    generate
    for (L = 1; L <= LOGNP; L = L + 1) begin : GEN_LVL
        for (k = 0; k < (NPAD >> L); k = k + 1) begin : GEN_ND
            assign tr[L][k] = tr[L-1][2*k] + tr[L-1][2*k+1];
        end
    end
    endgenerate

    /* ---- 输出：算术右移 + 饱和 ----
     * 饱和判据不写死位宽：右移后高 (ACCW-15) 位必须"全 0"（正）或"全 1"（负），
     * 否则就是超出 16 位有符号能表示的范围。 */
    function signed [15:0] sat16;
        input signed [ACCW-1:0] v;
        reg   signed [ACCW-1:0] s;
        begin
            s = v >>> SHIFT;
            if (s[ACCW-1:15] == {(ACCW-15){1'b0}})      sat16 = s[15:0];
            else if (s[ACCW-1:15] == {(ACCW-15){1'b1}}) sat16 = s[15:0];
            else if (s[ACCW-1])                         sat16 = 16'sh8000;
            else                                        sat16 = 16'sh7FFF;
        end
    endfunction

    /* valid 延迟两级，跟 acc_raw 对齐：
     * 第 n 个采样在第 (n+2) 拍出结果。 */
    reg v1, v2;
    always @(posedge clk) begin
        if (rst) begin
            v1 <= 1'b0;
            v2 <= 1'b0;
        end else begin
            v1 <= din_vld;
            v2 <= v1;
        end
    end

    always @(posedge clk) begin
        if (rst) begin
            acc_raw  <= {ACCW{1'b0}};
            dout     <= 16'sd0;
            dout_vld <= 1'b0;
        end else begin
            acc_raw  <= tr[LOGNP][0];
            dout     <= sat16(tr[LOGNP][0]);
            dout_vld <= v2;
        end
    end

endmodule
