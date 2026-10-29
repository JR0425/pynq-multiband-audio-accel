/* 多频段前端（front end）—— 三区间转 + 相减 + 4 段综合
 *
 * ================== 范围（刻意写死） ==================
 *
 * 做：
 *     3 个 193 抽头低通，**共享一套 195 个乘法器**，按系数组分三拍轮转；
 *     用相减式把它们变成 4 个频段；
 *     4 段相加，输出。
 *
 * 不做（刻意）：
 *     DRC / 每段增益 / AXI 外壳 / 板级握手。
 *     这一块的作用是给「性能与资源优化」提供**同算法的第三种实现**，
 *     并且回答一个 HLS 回答不了的问题：同一件事自己写 RTL 能到几拍。
 *
 * ================== 它跟谁逐位对齐 ==================
 *
 * 对照物有两个，都必须过：
 *
 *   ① HLS 那版核（src/hls/fir_multiband.cpp）在 **bypass=1** 时的通路。
 *      bypass 时核输出 = 4 段之和 = 延迟 D 拍的输入，HLS csim 已经把这一串
 *      吐成了 data/results/hw_transparent_i16.txt。本模块的 dout 必须和它
 *      **逐位相同**（1000 个采样，0 处不符）。
 *   ② sim/model/ref_model_mb.py 的整数模型。它把三路低通累加器、四段、总和
 *      全写成纯整数，本模块逐项对拍 —— 输出只能证明"最后一步对"，
 *      累加器才能把"乘法/加法树/抽头下标"和"移位/饱和"分开。
 *
 * 数值契约和 firmware 版一模一样（来历见 src/rtl/fir_lp.v 文件头）：
 *
 *     din    int16，Q1.15，贴比特，不做数值换算
 *     coef   int18，Q1.17，三张表（LP500 / LP1000 / LP2000）
 *     乘积   16 × 18 = 34 位有符号，精确
 *     累加   40 位有符号 = Q8.32，峰值 1.7e9，上限 5.5e11，不溢出
 *            → 加法精确且**与顺序无关**，所以加法树怎么摆都行
 *     输出   sat16(acc >>> 17)，AP_TRN + AP_SAT
 *
 * ================== 相减式（和 HLS 那版同一套公式） ==================
 *
 *     y0 = LP500(x)          b0 = y0
 *     y1 = LP1000(x)         b1 = y1 - y0
 *     y2 = LP2000(x)         b2 = y2 - y1
 *                            b3 = x[D] - y2      D = (193-1)/2 = 96
 *
 *     Σb = x[D]（精确，定点下没有任何舍入）—— 这就是"不压缩时输出等于
 *     延迟 96 拍的输入"的来源，也是直通检验的判据。
 *
 * b3 里的 x[D] 是**第 D 拍前的输入**，就是抽头延迟线上的 sr[D]。
 * 它进累加器（Q8.32）要左移 17 位：Q1.15 的 1 个最低位 = Q8.32 的 2^17。
 *
 * ================== 为什么是 3 拍/采样 ==================
 *
 * HLS 那版：195 个乘法器被 5 路部分和分批喂，每采样 35~49 拍。
 * 这里：195 个乘法器**每拍都在干活**，三组系数共用它们。
 *
 *     拍 0   抽头延迟线不动，乘法器吃 LP500 的系数
 *     拍 1   乘法器吃 LP1000 的系数；加法树算上一拍那一组
 *     拍 2   乘法器吃 LP2000 的系数；加法树算上一拍那组；同时
 *            **把下一个采样移进延迟线**
 *     之后   加法树四段流水分完三组，再一拍相减、一拍合成 ——
 *            从移位算第 8 拍出数（valid 流水也是 8 位）
 *
 * 关键资源账：
 *     · 乘法器只有**一套** 195 个（三组系数轮着用），不是 585 个；
 *     · 加法树也只有**一棵** —— 每拍只归约一组乘积，同一棵树轮三遍。
 *     这是"3 拍换面积"的实质：HLS 那版是 3 倍时间换 3 倍乘法器，
 *     这里反过来，用 3 倍时间把乘法器省下来 —— 但板上 DSP 只有 220 个，
 *     585 个根本放不下，所以这一条不是优化，是**唯一能放下的做法**。
 *
 * 系数表按文件名读（coeffs_lp500.mem / lp1000 / lp2000），原因见
 * src/rtl/fir_lp.v：Windows 上 plusarg 传给 xsim 的两条路都被堵死了。
 *
 * ⚠️ 乘法器上显式挂 (* use_dsp = "yes" *)：常量系数 FIR 不挂这个属性时，
 *    Vivado 会把它综合成 LUT 里的 CSD 移位加法，一个 DSP 都不占。
 *    195 个乘法全落 LUT 会把面积撑爆，这条是必须的。
 */

`timescale 1ns / 1ps

module fir_mb_fe #(
    parameter integer NTAP   = 193,          // 实际抽头数
    parameter integer NALLOC = 195,          // 系数表长度（补齐后）
    parameter integer CW     = 18,           // 系数位宽，Q1.17
    parameter integer ACCW   = 40,           // 累加器位宽，Q8.32
    parameter integer SHIFT  = 17,           // 输出右移量
    parameter integer GD     = 96            // 群延迟 (NTAP-1)/2
) (
    input  wire                   clk,
    input  wire                   rst,
    input  wire signed [15:0]     din,
    input  wire                   din_vld,
    output wire                   din_ready,

    /* 三个低通累加器原值（Q8.32）—— 给逐位对拍用 */
    output reg  signed [ACCW-1:0] ylp0,
    output reg  signed [ACCW-1:0] ylp1,
    output reg  signed [ACCW-1:0] ylp2,

    /* 四段各自的值（Q8.32）—— 同上 */
    output reg  signed [ACCW-1:0] band0,
    output reg  signed [ACCW-1:0] band1,
    output reg  signed [ACCW-1:0] band2,
    output reg  signed [ACCW-1:0] band3,

    /* 四段之和（Q8.32）= 延迟 D 拍的输入 << 17 */
    output reg  signed [ACCW-1:0] acc_raw,

    output reg  signed [15:0]     dout,
    output reg                    dout_vld
);

    /* ---- 相位 ----
     * PRIME 只走一次（第一个采样进来之前），之后 P0/P1/P2 循环。
     * din_ready 在 PRIME 和 P2 为高：生产者每 3 拍给一个采样。 */
    localparam [1:0] P_PRIME = 2'd0,
                     P0      = 2'd1,
                     P1      = 2'd2,
                     P2      = 2'd3;

    reg [1:0] ph;
    assign din_ready = (ph == P_PRIME) || (ph == P2);

    /* ---- 加法树层数 ---- */
    function integer clog2f;
        input integer v;
        integer i;
        begin
            clog2f = 0;
            for (i = v - 1; i > 0; i = i >> 1) clog2f = clog2f + 1;
        end
    endfunction

    localparam integer LOGNP = clog2f(NALLOC);
    localparam integer NPAD  = 1 << LOGNP;   // 补到 2 的幂（256）

    /* ---- 三张系数表 ----
     * 文件固定名，运行脚本负责把它们摆到工作目录里。 */
    reg signed [CW-1:0] c0 [0:NALLOC-1];
    reg signed [CW-1:0] c1 [0:NALLOC-1];
    reg signed [CW-1:0] c2 [0:NALLOC-1];
    initial begin
        $readmemh("coeffs_lp500.mem",  c0);
        $readmemh("coeffs_lp1000.mem", c1);
        $readmemh("coeffs_lp2000.mem", c2);
    end

    /* ---- 抽头延迟线 ----
     * 整体移位在硬件里就是寄存器之间接几根线，零逻辑、零拍数。
     * 这正是 HLS 那版做不到的（它的写法有环依赖，只能一拍一格地搬）。 */
    reg signed [15:0] sr [0:NALLOC-1];

    /* ---- 乘积寄存器（只有一套，三组系数轮着写） ---- */
    reg signed [ACCW-1:0] p [0:NPAD-1];

    /* ---- 每组的累加结果 ---- */
    reg signed [ACCW-1:0] y0r, y1r;

    /* ---- valid 流水 ----
     * 移位 -> 出数是 **8 拍**（加法树四段各占 1 拍，相减 1 拍，综合 1 拍，
     * 再加乘法与相位对齐 1 拍）。这个数必须和状态机一起改：
     * 2026-10-20 改流水时忘了它，现象是 rtl[i] == ref[i-1] ——
     * 数据全对、只是整体晚一个采样，很容易误判成"少了一个采样"。 */
    reg [7:0] v_sr;

    /* ---- 系数选择：P0 -> LP500, P1 -> LP1000, P2 -> LP2000 ----
     *
     * 这 2 位要扇出到 195 个 DSP 的 B 端口，是布线后最紧的一条路径：
     * 直接从相位寄存器译码时，一个 LUT 带 195 个负载，单这条净线 5.0 ns
     * （2026-10-20 实测，占总延迟的 90%）。
     *
     * 所以这里**提前一拍**算好存进寄存器：本拍打进去的是**下一拍**相位
     * 该用的组号，下一拍读出来正好对齐，相位寄存器自己就不用带这个扇出了。
     *
     * 实测 max_fanout 16 和 32 都只能把布线从 5.03 压到 4.95 ns —— 189 个
     * DSP 铺满了整片器件，控制信号从任何一点走到最远的 DSP 都要这么长，
     * 这是"一套乘法器轮三组系数"的固有代价，不是复制能解决的。
     * 布线后 WNS +0.585 ns（10 ns 周期），够用，但别再加宽树了。
     *
     * 映射：PRIME -> 空转（乘法器那一拍不写，值无所谓，给 0）
     *       P0 -> P1 用 LP1000；P1 -> P2 用 LP2000；P2 -> 回 P0 用 LP500 */
    (* max_fanout = 32 *) reg [1:0] sel;
    always @(posedge clk) begin
        if (rst)         sel <= 2'd0;
        else if (ph == P0) sel <= 2'd1;
        else if (ph == P1) sel <= 2'd2;
        else               sel <= 2'd0;
    end

    /* ---- 乘法 + 打一拍 ----
     * 两个操作数在各自原始位宽上相乘（16 × 18 = 34 位，正好一片 DSP48），
     * 再符号扩展进 40 位累加器。**不能**先把两边都撑到 40 位再乘 ——
     * 那样 Vivado 会造 40×40，落进逻辑格子。 */
    genvar g;
    genvar L, k;
    integer i;      // 主状态机用（移位 / 复位循环）
    integer ia;     // 加法树段 A 的流水寄存器用
    integer ib;     // 加法树段 B 的流水寄存器用
    integer ic;     // 加法树段 C 的流水寄存器用
    generate
    for (g = 0; g < NPAD; g = g + 1) begin : GEN_MUL
        if (g < NALLOC) begin
            wire signed [CW-1:0] cs = (sel == 2'd0) ? c0[g] :
                                      (sel == 2'd1) ? c1[g] : c2[g];
            (* use_dsp = "yes" *)
            wire signed [ACCW-1:0] pmul = sr[g] * cs;
            always @(posedge clk) begin
                if (rst)          p[g] <= {ACCW{1'b0}};
                else if (ph == P0 || ph == P1 || ph == P2) p[g] <= pmul;
            end
        end else begin
            always @(posedge clk) p[g] <= {ACCW{1'b0}};
        end
    end
    endgenerate

    /* ---- 40 位加法树：四级流水，每级 2 层加法 ----
     *
     * 2026-10-20 三轮实测（10 ns / 100 MHz，同一份源码只动树和相位）：
     *
     *   ① 8 层全组合        WNS -4.926（综合），单条组合路 14.6 ns
     *   ② 3+3+2 三级        WNS -2.460（综合）—— 综合器把乘法器和加法树
     *                       拼成 DSP 级联，一拍串 5 个 DSP = 11.0 ns
     *   ③ 3+3+2 且禁止 DSP   +3.215（综合）/ +0.070（布线后）
     *                       布线后 6.0 ns 全是走线，两级 40 位加法的
     *                       扇出在 189 个 DSP 之间铺得太开
     *
     * 定稿：每级只留 2 层加法，中间多用一排寄存器换走线余量。
     *     段 A   256 -> 64   （2 层）
     *     段 B    64 -> 16   （2 层）
     *     段 C    16 -> 4    （2 层）
     *     段 D     4 -> 1    （2 层，组合，直接进 y 寄存器）
     *
     * 寄存器比 ②③ 多一排（sA 从 32 个变 64 个），代价约 +1900 个 FF，
     * 换来布线后 3 ns 以上的余量 —— 这块板子上 FF 才用了 2%。
     *
     * 三组系数本来就是每拍进一组，和流水级数无关：每拍树上躺着三个
     * 不同采样的部分和，互不打架，所以拍数不变，仍是 3 拍/采样。
     * 每多一级流水，结果晚 3 拍出来 —— 正好一个整相位，
     * 所以下面状态机里各相位**承担的角色**要重新对一遍（见注释）。
     * 从移位到出数是 8 拍。
     */
    localparam integer ST1 = 2;              // 段 A 的层数
    localparam integer ST2 = 4;              // 段 A+B 的总层数
    localparam integer ST3 = 6;              // 段 A+B+C 的总层数
    localparam integer NA  = NPAD >> ST1;    // 段 A 输出个数（64）
    localparam integer NB  = NPAD >> ST2;    // 段 B 输出个数（16）
    localparam integer NC  = NPAD >> ST3;    // 段 C 输出个数（4）

    wire signed [ACCW-1:0] tr [0:LOGNP][0:NPAD-1];

    generate
    for (g = 0; g < NPAD; g = g + 1) begin : GEN_T0
        assign tr[0][g] = p[g];
    end
    for (L = 1; L <= ST1; L = L + 1) begin : GEN_LA
        for (k = 0; k < (NPAD >> L); k = k + 1) begin : GEN_NA
            /* use_dsp="no"：不挂的话综合器会把乘法器和加法树拼成 DSP 级联，
             * 一拍里串 5 个 DSP（2.4 + 1.7×4 = 11.0 ns），比进位链还慢。 */
            (* use_dsp = "no" *) wire signed [ACCW-1:0] t;
            assign t = tr[L-1][2*k] + tr[L-1][2*k+1];
            assign tr[L][k] = t;
        end
    end
    endgenerate

    reg signed [ACCW-1:0] sA [0:NA-1];
    always @(posedge clk) begin
        for (ia = 0; ia < NA; ia = ia + 1) sA[ia] <= tr[ST1][ia];
    end

    wire signed [ACCW-1:0] trB [0:(ST2-ST1)][0:NA-1];

    generate
    for (g = 0; g < NA; g = g + 1) begin : GEN_B0
        assign trB[0][g] = sA[g];
    end
    for (L = 1; L <= (ST2-ST1); L = L + 1) begin : GEN_LB
        for (k = 0; k < (NA >> L); k = k + 1) begin : GEN_NB
            (* use_dsp = "no" *) wire signed [ACCW-1:0] t;
            assign t = trB[L-1][2*k] + trB[L-1][2*k+1];
            assign trB[L][k] = t;
        end
    end
    endgenerate

    reg signed [ACCW-1:0] sB [0:NB-1];
    always @(posedge clk) begin
        for (ib = 0; ib < NB; ib = ib + 1) sB[ib] <= trB[ST2-ST1][ib];
    end

    wire signed [ACCW-1:0] trC [0:(ST3-ST2)][0:NB-1];

    generate
    for (g = 0; g < NB; g = g + 1) begin : GEN_C0
        assign trC[0][g] = sB[g];
    end
    for (L = 1; L <= (ST3-ST2); L = L + 1) begin : GEN_LC
        for (k = 0; k < (NB >> L); k = k + 1) begin : GEN_NC
            (* use_dsp = "no" *) wire signed [ACCW-1:0] t;
            assign t = trC[L-1][2*k] + trC[L-1][2*k+1];
            assign trC[L][k] = t;
        end
    end
    endgenerate

    reg signed [ACCW-1:0] sC [0:NC-1];
    always @(posedge clk) begin
        for (ic = 0; ic < NC; ic = ic + 1) sC[ic] <= trC[ST3-ST2][ic];
    end

    wire signed [ACCW-1:0] trD [0:(LOGNP-ST3)][0:NC-1];

    generate
    for (g = 0; g < NC; g = g + 1) begin : GEN_D0
        assign trD[0][g] = sC[g];
    end
    for (L = 1; L <= (LOGNP-ST3); L = L + 1) begin : GEN_LD
        for (k = 0; k < (NC >> L); k = k + 1) begin : GEN_ND
            (* use_dsp = "no" *) wire signed [ACCW-1:0] t;
            assign t = trD[L-1][2*k] + trD[L-1][2*k+1];
            assign trD[L][k] = t;
        end
    end
    endgenerate

    /* 某一组的和（还没打拍）。它在哪一拍对应哪一组，见状态机注释。 */
    wire signed [ACCW-1:0] ysum = trD[LOGNP-ST3][0];

    /* ---- 输出饱和：acc >>> 17 再钳到 int16 ---- */
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

    /* ---- x[D]，符号扩展后左移 17 位变成 Q8.32 ----
     * 在 P2（移位那一拍）从 sr[GD] 抓：那一拍第 k 个采样正在 sr[0]，
     * sr[GD] 就是它的 x[k-D]。
     *
     * 为什么不用"到 P0 直接从 sr[GD+2] 读"：延迟线只在有 din_vld 时移位，
     * 最后一个采样之后不再移，sr[GD+2] 就永远到不了那个采样 ——
     * 2026-10-20 这么写，最后 2 个采样的第 4 段差 51（= x 的一个 LSB <<17）。
     * 抓下来再走 4 拍流水，排空阶段也能顺着流出来。 */
    wire signed [ACCW-1:0] xd_q32 =
        ({{(ACCW-16){sr[GD][15]}}, sr[GD]}) <<< SHIFT;

    /* x[k-D] 的 4 拍流水：P2 抓，P0 用（s+3 抓到，s+7 用） */
    reg signed [ACCW-1:0] sd0, sd1, sd2, sd3;

    /* ---- 相减 + 综合 ----
     * 拍点对齐（四级加法树，从移位算第 8 拍出数）：
     *
     *   P0   ysum = LP2000(k)，y0r/y1r 里正是 LP500(k)/LP1000(k)
     *        -> 三个减法在这拍做，四段打进暂存
     *   P1   从暂存出数：四段相加、饱和、拉 dout_vld
     *        同时 y0r 收下一组的 LP500
     *   P2   y1r 收下一组的 LP1000；把下一个采样移进延迟线
     *
     * 三个减法（P0）和四项相加（P1）分两拍，是为了不让任何一拍
     * 出现 4 层 40 位加法 —— 和加法树是同一个坑。
     *
     * 写成 Σ band 而不是直接给 sd，是为了让"4 段综合"这一级真的在电路里；
     * 数值上它恒等于 sd。 */
    reg signed [ACCW-1:0] br0, br1, br2, br3;      // 四段（P0 写，P1 出）
    reg signed [ACCW-1:0] yr2_s;                   // LP2000 暂存，给 ylp2 口

    wire signed [ACCW-1:0] nb0 = y0r;
    wire signed [ACCW-1:0] nb1 = y1r - y0r;
    wire signed [ACCW-1:0] nb2 = ysum - y1r;
    wire signed [ACCW-1:0] nb3 = sd3 - ysum;
    wire signed [ACCW-1:0] bsum = br0 + br1 + br2 + br3;

    /* ---- 主状态机 + 数据通路 ---- */
    always @(posedge clk) begin
        if (rst) begin
            ph       <= P_PRIME;
            v_sr     <= 8'd0;
            y0r      <= {ACCW{1'b0}};
            y1r      <= {ACCW{1'b0}};
            br0      <= {ACCW{1'b0}};
            br1      <= {ACCW{1'b0}};
            br2      <= {ACCW{1'b0}};
            br3      <= {ACCW{1'b0}};
            yr2_s    <= {ACCW{1'b0}};
            sd0      <= {ACCW{1'b0}};
            sd1      <= {ACCW{1'b0}};
            sd2      <= {ACCW{1'b0}};
            sd3      <= {ACCW{1'b0}};
            ylp0     <= {ACCW{1'b0}};
            ylp1     <= {ACCW{1'b0}};
            ylp2     <= {ACCW{1'b0}};
            band0    <= {ACCW{1'b0}};
            band1    <= {ACCW{1'b0}};
            band2    <= {ACCW{1'b0}};
            band3    <= {ACCW{1'b0}};
            acc_raw  <= {ACCW{1'b0}};
            dout     <= 16'sd0;
            dout_vld <= 1'b0;
            for (i = 0; i < NALLOC; i = i + 1) sr[i] <= 16'sd0;
        end else begin
            /* valid 流水：shift 那一拍写入 din_vld，之后每拍往后挪一格 */
            v_sr <= {v_sr[6:0], ((ph == P_PRIME || ph == P2) && din_vld) ? 1'b1 : 1'b0};
            dout_vld <= 1'b0;

            /* x[D] 流水：sd0 每拍都抓，排空时 sr[GD] 不动，流出来的还是对的 */
            sd0 <= xd_q32;
            sd1 <= sd0;
            sd2 <= sd1;
            sd3 <= sd2;

            case (ph)
            P_PRIME: begin
                if (din_vld) begin
                    sr[0] <= din;
                    for (i = 1; i < NALLOC; i = i + 1) sr[i] <= sr[i-1];
                    ph <= P0;
                end
            end

            P0: begin
                /* 乘法器吃 LP500(k+2)；加法树出 LP2000(k)。
                 * 三段低通在这拍齐了 -> 相减，四段进暂存。 */
                br0   <= nb0;
                br1   <= nb1;
                br2   <= nb2;
                br3   <= nb3;
                yr2_s <= ysum;
                ph    <= P1;
            end

            P1: begin
                /* 乘法器吃 LP1000(k+2)；加法树出 LP500(k+1)。
                 * 这一拍出第 k 组的结果。 */
                y0r      <= ysum;
                ylp0     <= y0r;
                ylp1     <= y1r;
                ylp2     <= yr2_s;
                band0    <= br0;
                band1    <= br1;
                band2    <= br2;
                band3    <= br3;
                acc_raw  <= bsum;
                dout     <= sat16(bsum);
                dout_vld <= v_sr[7];
                ph       <= P2;
            end

            P2: begin
                /* 乘法器吃 LP2000(k+2)；加法树出 LP1000(k+1)；
                 * 把下一个采样移进延迟线。 */
                y1r <= ysum;
                if (din_vld) begin
                    sr[0] <= din;
                    for (i = 1; i < NALLOC; i = i + 1) sr[i] <= sr[i-1];
                end
                /* 相位**总是**往前转，即使这一拍没有新采样。
                 * 写成"没数就回 PRIME"会少出最后一个采样：
                 * 第 k 个采样的四段是在**下一个采样组的 P1 拍**算的
                 * （那时 LP2000 的加法树才出得来），没有下一组就没有这一拍。
                 * 空转时乘法和加法树照算，只是 v_sr 全 0，不往外发。 */
                ph <= P0;
            end
            endcase
        end
    end

endmodule
