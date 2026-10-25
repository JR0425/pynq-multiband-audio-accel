/* fir_drc4 —— 四段「验配增益 + 压缩」，和 HLS v25 的压缩路径逐位对齐
 *
 * ================= 这是什么 =================
 *
 * fir_mb_fe 只做到「3 个低通 → 相减 → 4 段 → 相加」。出货的 v25 在四段相加之前
 * 还有两级：每段先乘验配增益（band_gain），再过压缩器（drc / wdrc），最后才相加。
 * 没有这一段，RTL 和 v25 就不是「同功能」，上板对拍也对不上。
 *
 * 输入是 fir_mb_fe 已经算好的四个 band（acc_t，40 位 Q8.32），
 * 输出是压缩后的 int16 采样，配一个 y_vld —— 比 in_vld 晚 9 拍。
 *
 * ================= 定点规则（照抄 src/hls/fir_types.h） =================
 *
 *   acc_t   ap_fixed<40,8,AP_TRN,AP_SAT>    Q8.32，小数位 32
 *   drc_t   ap_fixed<24,2,AP_TRN,AP_SAT>    Q2.22，小数位 22
 *   gain_t  ap_fixed<16,8,AP_TRN,AP_SAT>    Q7.8，1.0 = 256
 *
 * 每一次「赋给窄类型」都是 **向负无穷取整 + 饱和**：
 *   · Verilog 的 >>> 在有符号数上就是向负无穷取整，和 AP_TRN 一致
 *   · 先取整**再**判饱和 —— 顺序反了会在负数上差 1 个 LSB
 *
 * 出厂参数（v25，见 src/hls/fir_tb.cpp）：
 *   thr=3277(Q1.15) ratio=22938(Q1.15) gain=256(Q7.8) attack=136(5ms) release=7(100ms)
 *   核里是 Q2.22 原始整数，所以要 <<7：thr=419456 ratio=2936064 atk=17408 rel=896
 *
 * ================= 为什么是九级流水（2026-10-25 改）=================
 *
 * 第一版写成四级，综合不报错、xsim 1000/1000 全对，但实现后 **WNS = −11.397 ns**：
 * 最差路径 40 级逻辑、20.7 ns，从 `r_gb` 穿过「绝对值 → 减包络 → 乘步进 →
 * 64 位加 → 移位 → 饱和」到 `env_r`。两个原因叠在一起：
 *
 *   ① 全程用 64 位有符号在算。其实 env/diff 都在 24 位内，乘完也只有 40 位，
 *      64 位只是让每条进位链长了一倍；
 *   ② 三件事（乘、加、饱和）挤在同一拍。
 *
 * 现在按九级拆开，宽度也只留到够用：
 *
 *   级 0  band(Q8.32) → drc_t：右移 10、饱和
 *   级 1  × 增益（v25 的 256/2^8 正好抵消，综合会折成一根线）
 *   级 2  |x|、符号
 *   级 3  (a − env) × 步进 —— 步进只有两个常数，比较选一个
 *   级 4  env += 增量>>22，饱和、钳零
 *   级 5  env − thr（只在 env > thr 时有用）
 *   级 6  衰减量 gr 的乘法 + 右移 22
 *   级 7  gr 饱和到 drc_t
 *   级 8  a2 = a − gr，钳零，带符号；四段相加在 fir_drc4 里
 *
 * 级 6/7 是 2026-10-25 从一级拆成两级的：八级那版整机 WNS −0.140 ns，44 个失败
 * 端点**全部**是 `p_gr_reg[*]/D`，路径就是「移位 → 48 位饱和比较」串在一拍里。
 *
 * `env` 每个采样更新一次，四段各一份状态。前端每 3 拍吐一个采样，流水有 9 级
 * 但吞吐不受影响（同时在飞的采样最多 3 个）。
 *
 * 有一条等价值改写要记住：原式是 `((env<<22) + diff*step) >> 22`，
 * 因为 `env<<22` 是 2^22 的整数倍，可以拆成 `env + ((diff*step) >> 22)`。
 * 少一次 46 位的加法，结果逐位相同。
 *
 * ================= 改流水时踩的两个坑 =================
 *
 * ① **拼接结果是无符号的，`>>>` 会变成逻辑右移。**
 *    `{{8{band[39]}}, band} >>> 10` 对 −532 算出 +274877906943，
 *    整条链后面全饱和。必须先落到 `wire signed` 再移位。
 *    这类错不报错、不警告，只有对拍能抓到。
 *
 * ② **ATK 不能截成 15 位。** 为了省位宽写成 `localparam signed [14:0]
 *    ATK15 = ATK[14:0]`，而 15 位有符号的上限是 16383，ATK=17408 一截
 *    就变成 −15360 —— 包络每一步都在减，永远停在 0。现象是"压缩器一点都
 *    没动"，和阈值设太高长得一模一样。
 *    **而且当时 1000 点那份单元 tb 是 1000/1000 全对的**：那份音源包络本来
 *    就没超过门限，压缩器不工作，包络算错也看不出来。换响的音源才暴露。
 *    → 现在步进用全宽 DW 位，乘积 25×24 用 49 位装。
 *
 * ================= 和 drc()（无记忆）的关系 =================
 *
 * HLS 里 attack/release 都是 0 时走 drc()，不看包络、每个采样独立压。
 * 本模块检测 ATK==0 && REL==0 时自动切到那条路（不动 env 状态），
 * 和 HLS 的选择完全一样。v25 用的是 wdrc()，这条路留着给 v20_gain 回归。
 */

`timescale 1ns / 1ps

/* ------------------------------------------------------------------ 单段 */
module fir_drc4_chan #(
    parameter integer BW        = 40,
    parameter integer DW        = 24,
    parameter integer FRAC_ACC  = 32,
    parameter integer FRAC_DRC  = 22,
    parameter signed [DW-1:0] THR   = 24'sd419456,
    parameter signed [DW-1:0] RATIO = 24'sd2936064,
    parameter signed [DW-1:0] ATK   = 24'sd17408,
    parameter signed [DW-1:0] REL   = 24'sd896,
    parameter signed [15:0]   GAIN  = 16'sd256
) (
    input  wire                  clk,
    input  wire                  rst,      // 清包络（和 HLS 的 reset 输入同义）
    input  wire                  in_vld,
    input  wire signed [BW-1:0]  band,
    output reg  signed [DW-1:0]  yv,
    output reg                   yv_vld
);

    localparam integer SH_ACC = FRAC_ACC - FRAC_DRC;   // Q8.32 → Q2.22 是右移 10
    /* drc_t(Q2.22) × gain_t(Q7.8) = Q9.30；收回 Q2.22 就是右移 30-22 = 8 位。
     * 不是 FRAC_DRC-8（那是把小数点搞反了），写成 14 会让输出整体小 64 倍。 */
    localparam integer SH_GAIN = 8;
    localparam USE_WDRC = ((ATK != 0) || (REL != 0)) ? 1'b1 : 1'b0;

    localparam signed [DW-1:0]   THR24 = THR;
    localparam signed [DW-1:0]   OMR24 = ({{(DW-1){1'b0}}, 1'b1} <<< FRAC_DRC) - RATIO;

    /* 饱和到 drc_t。输入位宽 48 够装最大的那笔乘积（25×24）。
     * 用窄函数而不是 64 位比较 —— 64 位比较器单独就要两级 CARRY4。 */
    function automatic signed [DW-1:0] satd;
        input signed [47:0] v;
        begin
            if (v > 48'sd8388607)       satd = 24'sh7FFFFF;
            else if (v < -48'sd8388608) satd = 24'sh800000;
            else                        satd = v[DW-1:0];
        end
    endfunction

    /* ---------------- 级 0：band → drc_t ---------------- */
    /* 拼接是**无符号**的：直接写 `{{..}, band} >>> n`，>>> 会变成逻辑右移，
     * 负数被移成一堆 1 的正数（实测 −532 变成 +274877906943）。必须先落到
     * signed 线再做移位。 */
    wire signed [47:0]   bse  = {{(48-BW){band[BW-1]}}, band};
    wire signed [47:0]   bsh  = bse >>> SH_ACC;
    wire signed [DW-1:0] gb0  = satd(bsh);

    reg signed [DW-1:0] p_band;

    /* ---------------- 级 1：× 增益 ---------------- */
    /* 乘积 24×16 正好 40 位，别先扩到 48 再乘 —— 那会变成 48×48。 */
    wire signed [39:0]   gmul = p_band * GAIN;
    wire signed [47:0]   gse  = {{8{gmul[39]}}, gmul};
    wire signed [47:0]   gshr = gse >>> SH_GAIN;
    wire signed [DW-1:0] gb1  = satd(gshr);

    reg signed [DW-1:0] p_gb;

    /* ---------------- 级 2：绝对值 + 符号 ---------------- */
    wire signed [47:0]   nabs = -{{24{p_gb[23]}}, p_gb};
    wire signed [DW-1:0] aabs = (p_gb[DW-1]) ? satd(nabs) : p_gb;

    reg signed [DW-1:0] p_a;
    reg signed [DW-1:0] p_gain;
    reg                 p_neg;

    /* 包络状态：每段一份，跨块保持，rst 清 */
    reg signed [DW-1:0] env_r;

    /* ---------------- 级 3：包络增量 (a − env) × 步进 ---------------- */
    /* env 是 24 位有符号、a 也是，差最多 25 位；步进用**全宽** DW 位。
     * 别为了省位宽把步进截成 15 位 —— ATK=17408 超过 15 位有符号的上限
     * 16383，截完变成 −15360，包络一步都涨不上去，输出却只是"压得不够"，
     * 看起来像参数没调好。25 × 24 = 49，用 49 位装。 */
    wire signed [24:0]   diff = {{1{p_a[23]}}, p_a} - {{1{env_r[23]}}, env_r};
    wire signed [DW-1:0] step = (diff > 0) ? ATK : REL;
    wire signed [48:0]   prod = diff * step;

    reg signed [48:0] p_prod;
    reg signed [DW-1:0] p_a2;
    reg signed [DW-1:0] p_gain2;
    reg                 p_neg2;

    /* ---------------- 级 4：env += 增量 ---------------- */
    wire signed [47:0]   inc  = p_prod >>> FRAC_DRC;
    wire signed [47:0]   esum = {{24{env_r[23]}}, env_r} + inc;
    wire signed [DW-1:0] en   = satd(esum);
    wire signed [DW-1:0] enp  = (en < 0) ? {DW{1'b0}} : en;

    reg signed [DW-1:0] p_a3;
    reg signed [DW-1:0] p_gain3;
    reg                 p_neg3;

    /* ---------------- 级 5：env − thr ---------------- */
    wire signed [24:0]   ex  = {{1{env_r[23]}}, env_r} - {{1{THR24[23]}}, THR24};
    wire                 epos = (env_r > THR24);

    reg signed [24:0]   p_ex;
    reg                 p_epos;
    reg signed [DW-1:0] p_a4;
    reg signed [DW-1:0] p_gain4;
    reg                 p_neg4;

    /* ---------------- 级 6：衰减量 gr 的乘法 + 右移 ---------------- */
    /* 25 × 24 = 49 位，用 49 位装，别扩到 64。
     *
     * `>>>22` 和 48 位饱和比较**必须分两拍**。挤在一拍里是整机时序的唯一失败点：
     * 2026-10-25 实测 WNS −0.140 ns、44 个失败端点**全部**是
     * `u_b{0,2,3}/p_gr_reg[*]/D`，路径是 DSP 的 gr_p → 右移 → 48 位比较 → 寄存器，
     * 12 级 CARRY4、9.921 ns。拆开之后这条降到 6 ns 量级，流水 8 → 9 级。 */
    wire signed [48:0]   gr_p = p_ex * OMR24;
    wire signed [47:0]   gr_s = gr_p >>> FRAC_DRC;

    reg signed [47:0]    p_grs;      // 移位结果，下一拍再判饱和
    reg                  p_epos6;

    /* 数据侧多出来的那一级（原来 p_a5 和 p_gr 对齐，现在 p_gr 晚一拍，靠 p_a6 追） */
    reg signed [DW-1:0]  p_a5;
    reg signed [DW-1:0]  p_gain5;
    reg                  p_neg5;

    wire signed [DW-1:0] gr   = p_epos6 ? satd(p_grs) : {DW{1'b0}};

    reg signed [DW-1:0] p_gr;
    reg signed [DW-1:0] p_a6;
    reg signed [DW-1:0] p_gain6;
    reg                 p_neg6;

    /* ---------------- 级 8：a2 = a − gr，带符号 ----------------
     * p_gr 比原来晚一拍（移位和饱和拆开了），所以数据侧也跟着多用一级 p_a6。 */
    wire signed [24:0]   a2w  = {{1{p_a6[23]}}, p_a6} - {{1{p_gr[23]}}, p_gr};
    wire signed [24:0]   a2wc = (a2w < 0) ? 25'sd0 : a2w;
    wire signed [47:0]   yv_w = p_neg6 ? -{{23{a2wc[24]}}, a2wc}
                                      : {{23{a2wc[24]}}, a2wc};

    /* legacy：a <= thr 原样放过；否则 sign*(thr + (a-thr)*ratio) */
    wire signed [48:0] leg_p = {{24{p_a6[23]}}, p_a6} - {{24{THR24[23]}}, THR24};
    wire signed [48:0] leg_m = leg_p * RATIO;
    wire signed [48:0] leg_n = ({{25{THR24[23]}}, THR24} <<< FRAC_DRC) + leg_m;
    wire signed [47:0] leg_y = leg_n >>> FRAC_DRC;
    wire signed [47:0] yv_l  = (p_a6 <= THR24) ? {{24{p_gain6[23]}}, p_gain6}
                                               : (p_neg6 ? -leg_y : leg_y);
    wire signed [47:0] yv_sel = USE_WDRC ? yv_w : yv_l;

    /* ---------------- 寄存器 ---------------- */
    /* 8 位：in_vld 延迟 1..8 拍，yv 在 t+9 有效，所以用 vpipe[7] 对齐 */
    reg [7:0] vpipe;

    always @(posedge clk) begin
        if (rst) begin
            env_r   <= {DW{1'b0}};
            p_band  <= {DW{1'b0}};
            p_gb    <= {DW{1'b0}};
            p_a     <= {DW{1'b0}};
            p_gain  <= {DW{1'b0}};
            p_neg   <= 1'b0;
            p_prod  <= 49'sd0;
            p_a2    <= {DW{1'b0}};
            p_gain2 <= {DW{1'b0}};
            p_neg2  <= 1'b0;
            p_a3    <= {DW{1'b0}};
            p_gain3 <= {DW{1'b0}};
            p_neg3  <= 1'b0;
            p_ex    <= 25'sd0;
            p_epos  <= 1'b0;
            p_a4    <= {DW{1'b0}};
            p_gain4 <= {DW{1'b0}};
            p_neg4  <= 1'b0;
            p_grs   <= 48'sd0;
            p_epos6 <= 1'b0;
            p_gr    <= {DW{1'b0}};
            p_a6    <= {DW{1'b0}};
            p_gain6 <= {DW{1'b0}};
            p_neg6  <= 1'b0;
            p_a5    <= {DW{1'b0}};
            p_gain5 <= {DW{1'b0}};
            p_neg5  <= 1'b0;
            vpipe   <= 8'd0;
            yv      <= {DW{1'b0}};
            yv_vld  <= 1'b0;
        end else begin
            /* 级 0..2 */
            p_band <= gb0;
            p_gb   <= gb1;
            p_a    <= aabs;
            p_gain <= p_gb;
            p_neg  <= p_gb[DW-1];

            /* 级 3 */
            p_prod  <= prod;
            p_a2    <= p_a;
            p_gain2 <= p_gain;
            p_neg2  <= p_neg;

            /* 级 4：只在真采样进来时更新包络。
             * p_prod 在 t+4 有效，那时 vpipe[3] 正好是 1（in_vld 延迟 3 拍）。
             * 写成 vpipe[1] 会把上一拍残留的 p_prod 算进去 —— 包络整个错位。 */
            if (vpipe[3]) env_r <= enp;
            p_a3    <= p_a2;
            p_gain3 <= p_gain2;
            p_neg3  <= p_neg2;

            /* 级 5 */
            p_ex    <= ex;
            p_epos  <= epos;
            p_a4    <= p_a3;
            p_gain4 <= p_gain3;
            p_neg4  <= p_neg3;

            /* 级 6：乘法 + 右移，先存下来 */
            p_grs   <= gr_s;
            p_epos6 <= p_epos;
            p_a5    <= p_a4;
            p_gain5 <= p_gain4;
            p_neg5  <= p_neg4;

            /* 级 7：饱和到 drc_t */
            p_gr    <= gr;
            p_a6    <= p_a5;
            p_gain6 <= p_gain5;
            p_neg6  <= p_neg5;

            /* 级 8 */
            yv     <= satd(yv_sel);
            yv_vld <= vpipe[7];

            vpipe <= {vpipe[6:0], in_vld};
        end
    end

endmodule


/* ------------------------------------------------------------------ 四段 */
module fir_drc4 #(
    parameter integer BW        = 40,
    parameter integer DW        = 24,
    parameter integer FRAC_ACC  = 32,
    parameter integer FRAC_DRC  = 22,
    parameter integer OUT_SHIFT = 17,
    parameter signed [DW-1:0] THR0   = 24'sd419456,
    parameter signed [DW-1:0] RATIO0 = 24'sd2936064,
    parameter signed [DW-1:0] ATK0   = 24'sd17408,
    parameter signed [DW-1:0] REL0   = 24'sd896,
    parameter signed [15:0]   GAIN0  = 16'sd256,
    parameter signed [DW-1:0] THR1   = 24'sd419456,
    parameter signed [DW-1:0] RATIO1 = 24'sd2936064,
    parameter signed [DW-1:0] ATK1   = 24'sd17408,
    parameter signed [DW-1:0] REL1   = 24'sd896,
    parameter signed [15:0]   GAIN1  = 16'sd256,
    parameter signed [DW-1:0] THR2   = 24'sd419456,
    parameter signed [DW-1:0] RATIO2 = 24'sd2936064,
    parameter signed [DW-1:0] ATK2   = 24'sd17408,
    parameter signed [DW-1:0] REL2   = 24'sd896,
    parameter signed [15:0]   GAIN2  = 16'sd256,
    parameter signed [DW-1:0] THR3   = 24'sd419456,
    parameter signed [DW-1:0] RATIO3 = 24'sd2936064,
    parameter signed [DW-1:0] ATK3   = 24'sd17408,
    parameter signed [DW-1:0] REL3   = 24'sd896,
    parameter signed [15:0]   GAIN3  = 16'sd256
) (
    input  wire                  clk,
    input  wire                  rst,
    input  wire                  in_vld,
    input  wire signed [BW-1:0]  band0,
    input  wire signed [BW-1:0]  band1,
    input  wire signed [BW-1:0]  band2,
    input  wire signed [BW-1:0]  band3,
    output reg  signed [15:0]    y,
    output reg                   y_vld
);

    function automatic signed [15:0] sat16;
        input signed [BW-1:0] v;
        reg   signed [BW-1:0] s;
        begin
            s = v >>> OUT_SHIFT;
            if (s[BW-1:15] == {(BW-15){1'b0}})      sat16 = s[15:0];
            else if (s[BW-1:15] == {(BW-15){1'b1}}) sat16 = s[15:0];
            else if (s[BW-1])                       sat16 = 16'sh8000;
            else                                    sat16 = 16'sh7FFF;
        end
    endfunction

    wire signed [DW-1:0] yv0, yv1, yv2, yv3;
    wire                 v0,  v1,  v2,  v3;

    fir_drc4_chan #(.BW(BW), .DW(DW), .FRAC_ACC(FRAC_ACC), .FRAC_DRC(FRAC_DRC),
                    .THR(THR0), .RATIO(RATIO0), .ATK(ATK0), .REL(REL0), .GAIN(GAIN0))
    u_b0 (.clk(clk), .rst(rst), .in_vld(in_vld), .band(band0), .yv(yv0), .yv_vld(v0));

    fir_drc4_chan #(.BW(BW), .DW(DW), .FRAC_ACC(FRAC_ACC), .FRAC_DRC(FRAC_DRC),
                    .THR(THR1), .RATIO(RATIO1), .ATK(ATK1), .REL(REL1), .GAIN(GAIN1))
    u_b1 (.clk(clk), .rst(rst), .in_vld(in_vld), .band(band1), .yv(yv1), .yv_vld(v1));

    fir_drc4_chan #(.BW(BW), .DW(DW), .FRAC_ACC(FRAC_ACC), .FRAC_DRC(FRAC_DRC),
                    .THR(THR2), .RATIO(RATIO2), .ATK(ATK2), .REL(REL2), .GAIN(GAIN2))
    u_b2 (.clk(clk), .rst(rst), .in_vld(in_vld), .band(band2), .yv(yv2), .yv_vld(v2));

    fir_drc4_chan #(.BW(BW), .DW(DW), .FRAC_ACC(FRAC_ACC), .FRAC_DRC(FRAC_DRC),
                    .THR(THR3), .RATIO(RATIO3), .ATK(ATK3), .REL(REL3), .GAIN(GAIN3))
    u_b3 (.clk(clk), .rst(rst), .in_vld(in_vld), .band(band3), .yv(yv3), .yv_vld(v3));

    /* drc_t(Q2.22) 加到 acc_t(Q8.32)：小数位差 10，左移 10。
     * 四段都 ≤ 2^23，<<10 之后 ≤ 2^33，四项相加 ≤ 2^35，40 位装得下。 */
    localparam integer SH = FRAC_ACC - FRAC_DRC;

    wire signed [BW-1:0] yvx0 = {{(BW-DW){yv0[DW-1]}}, yv0};
    wire signed [BW-1:0] yvx1 = {{(BW-DW){yv1[DW-1]}}, yv1};
    wire signed [BW-1:0] yvx2 = {{(BW-DW){yv2[DW-1]}}, yv2};
    wire signed [BW-1:0] yvx3 = {{(BW-DW){yv3[DW-1]}}, yv3};
    wire signed [BW-1:0] asum = (yvx0 <<< SH) + (yvx1 <<< SH)
                              + (yvx2 <<< SH) + (yvx3 <<< SH);

    always @(posedge clk) begin
        if (rst) begin
            y     <= 16'sd0;
            y_vld <= 1'b0;
        end else begin
            y     <= sat16(asum);
            y_vld <= v0 & v1 & v2 & v3;   // 四段同步，用一个就够，四个一起与更保险
        end
    end

endmodule
