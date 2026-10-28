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
 * 本模块检测 ATK==0 && REL==0 时把包络更新改成"写瞬时幅度"，
 * 等价于不看历史、每个采样独立压，和 HLS 的选择完全一样。
 * v25 用的是 wdrc()，这条路留着给 v20_gain 回归。
 */

`timescale 1ns / 1ps

/* ------------------------------------------------------------------ 单段 */
module fir_drc4_chan #(
    parameter integer BW        = 40,
    parameter integer DW        = 24,
    parameter integer FRAC_ACC  = 32,
    parameter integer FRAC_DRC  = 22
) (
    input  wire                  clk,
    input  wire                  rst,      // 清包络（和 HLS 的 reset 输入同义）
    input  wire                  in_vld,
    input  wire signed [BW-1:0]  band,
    /* 运行期验配增益（Q7.8，1.0 = 256）。
     * 2026-10-28：原来是参数，为了网页能拖滑杆改成端口。
     * 斜坡不在这一级做 —— 由 fir_pl_direct 把爬好坡的目标值喂进来，
     * 这样压缩器的包络状态不受增益变化影响（增益在包络检测之前，
     * 换增益等价于换输入电平，包络本来就该跟着变）。 */
    input  wire signed [15:0]    gain_i,
    /* 2026-10-28 批 2：DRC 四参数也是运行期端口（Q2.22，24 位有符号）。
     * thr/ratio 就是 HLS 的对应量；atk/rel 是每采样平滑步进。
     * **atk 与 rel 同时为 0 = 无记忆压缩**（每个采样独立压，不看包络），
     * 和 HLS `fir_drc4()` 里 ATK==0 && REL==0 那条分支同一个意思。
     * 实现上不是另一条数据路，而是把包络改成"瞬时 = 当前 |x|"：
     * 阈值以上的输出 = thr + (a−thr)·ratio，与 HLS 的 drc() 最多差 1 LSB
     * （WDRC 那边是 ceil、HLS 写的是 floor），听不出来，对拍要按 ±1 判。 */
    input  wire signed [DW-1:0]  thr_i,
    input  wire signed [DW-1:0]  ratio_i,
    input  wire signed [DW-1:0]  atk_i,
    input  wire signed [DW-1:0]  rel_i,
    output reg  signed [DW-1:0]  yv,
    output reg                   yv_vld,
    /* 2026-10-28 批 1 观察口：把包络和当前衰减量引出去给寄存器采样。
     * 纯 wire 转发，不动任何主通路逻辑/时序。 */
    output wire signed [DW-1:0]  env_o,
    output wire signed [DW-1:0]  gr_o
);

    localparam integer SH_ACC = FRAC_ACC - FRAC_DRC;   // Q8.32 → Q2.22 是右移 10
    /* drc_t(Q2.22) × gain_t(Q7.8) = Q9.30；收回 Q2.22 就是右移 30-22 = 8 位。
     * 不是 FRAC_DRC-8（那是把小数点搞反了），写成 14 会让输出整体小 64 倍。 */
    localparam integer SH_GAIN = 8;

    /* ---- 运行期参数（2026-10-28 批 2）----------------------------------
     * 原来 THR/RATIO/ATK/REL 是 localparam，编译期就把通路选死了。
     * 现在：
     *   · omr_r  = 2^FRAC_DRC − ratio_i（"剩余斜率"），**先打一拍**再用 ——
     *     ratio 很少变，寄存器化之后乘法器输入仍是寄存器，不吃时序；
     *   · use_wdrc_r = (atk != 0) || (rel != 0)，同样打一拍，用来选包络更新方式。
     * 两者都不跟 rst 走：它们只是参数的影子，复位期间保持上一次的值无害。 */
    wire use_wdrc = (atk_i != {DW{1'b0}}) || (rel_i != {DW{1'b0}});

    wire signed [DW:0] omr_w = ({{(DW-1){1'b0}}, 1'b1} <<< FRAC_DRC)
                             - {{1{ratio_i[DW-1]}}, ratio_i};

    reg  signed [DW-1:0] omr_r;
    reg                  use_wdrc_r;

    initial begin
        omr_r      = {DW{1'b0}};
        use_wdrc_r = 1'b0;
    end

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
    /* 乘积 24×16 正好 40 位，别先扩到 48 再乘 —— 那会变成 48×48。
     * gain_i 是运行期值，但要和 p_band 同一拍对齐：p_band 是打了一拍的
     * band，gain_i 由外面的斜坡寄存器直接给出（每个采样更新一次），
     * 所以在 p_band 有效的那一拍取 gain_i 就是对的。 */
    wire signed [39:0]   gmul = p_band * gain_i;
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

    assign env_o = env_r;

    /* ---------------- 级 3：包络增量 (a − env)，差先打一拍 ---------------- */
    /* env 是 24 位有符号、a 也是，差最多 25 位；步进用**全宽** DW 位。
     * 别为了省位宽把步进截成 15 位 —— ATK=17408 超过 15 位有符号的上限
     * 16383，截完变成 −15360，包络一步都涨不上去，输出却只是"压得不够"，
     * 看起来像参数没调好。25 × 24 = 49，用 49 位装。
     *
     * 2026-10-28 批 2 收尾：这四件事原来挤在一拍里（p_a → 25 位减法 → 步进
     * 选择 → DSP 乘法 → p_prod），参数变成寄存器之后综合出来 10.1 ns /
     * 8 级 CARRY4 + DSP，WNS −1.612 ns、529 个失败端点全是这一条。
     * 现在把差和步进先打一拍，乘法单独占一拍：流水 9 → 10 级。
     * 硬件里相邻两段采样隔 2083 拍（48 kHz @100 MHz），多一级不改变任何
     * 数值 —— 只是把「同一个采样的包络更新」往后挪一拍，数据侧同步挪，
     * 对拍逐位不变。 */
    wire signed [24:0]   diff = {{1{p_a[23]}}, p_a} - {{1{env_r[23]}}, env_r};
    wire signed [DW-1:0] step = (diff > 0) ? atk_i : rel_i;

    reg signed [24:0]   p_diff;
    reg signed [DW-1:0] p_step;

    /* 级 4：乘法（DSP + LUT 拆分，见文件头「为什么要拆乘法」）
     * p_step = step_hi·64 + step_lo，两段各乘一次再相加，整数上完全相等。 */
    wire signed [DW-7:0] step_hi = p_step >>> 6;      // 18 位，正好塞进 DSP48 的 B 口
    wire signed [6:0]    step_lo = {1'b0, p_step[5:0]};  // 低 6 位，补 0 变有符号
    (* use_dsp = "no" *) wire signed [31:0] prod_lo = p_diff * step_lo;
    wire signed [48:0]   prod = ((p_diff * step_hi) <<< 6) + prod_lo;

    reg signed [48:0] p_prod;

    /* ---------------- 级 5：env += 增量 ---------------- */
    wire signed [47:0]   inc  = p_prod >>> FRAC_DRC;
    wire signed [47:0]   esum = {{24{env_r[23]}}, env_r} + inc;
    wire signed [DW-1:0] en   = satd(esum);
    wire signed [DW-1:0] enp  = (en < 0) ? {DW{1'b0}} : en;

    /* ---------------- 级 6：env − thr ---------------- */
    wire signed [24:0]   ex  = {{1{env_r[23]}}, env_r} - {{1{thr_i[23]}}, thr_i};
    wire                 epos = (env_r > thr_i);

    reg signed [24:0]   p_ex;
    reg                 p_epos;

    /* ---------------- 级 7：衰减量 gr 的乘法 + 右移 ---------------- */
    /* 25 × 24 = 49 位，用 49 位装，别扩到 64。
     *
     * `>>>22` 和 48 位饱和比较**必须分两拍**。挤在一拍里是整机时序的唯一失败点：
     * 2026-10-25 实测 WNS −0.140 ns、44 个失败端点**全部**是
     * `u_b{0,2,3}/p_gr_reg[*]/D`，路径是 DSP 的 gr_p → 右移 → 48 位比较 → 寄存器，
     * 12 级 CARRY4、9.921 ns。拆开之后这条降到 6 ns 量级。 */
    wire signed [DW-7:0] omr_hi = omr_r >>> 6;
    wire signed [6:0]    omr_lo = {1'b0, omr_r[5:0]};
    (* use_dsp = "no" *) wire signed [31:0] gr_lo = p_ex * omr_lo;
    wire signed [48:0]   gr_p = ((p_ex * omr_hi) <<< 6) + gr_lo;
    wire signed [47:0]   gr_s = gr_p >>> FRAC_DRC;

    reg signed [47:0]    p_grs;      // 移位结果，下一拍再判饱和
    reg                  p_epos6;

    wire signed [DW-1:0] gr   = p_epos6 ? satd(p_grs) : {DW{1'b0}};

    reg signed [DW-1:0] p_gr;
    assign gr_o = p_gr;    /* 用寄存后那份，和参与减法的同拍 */

    /* ---------------- 数据侧延迟线 ----------------
     * p_a（t+3）之后一共 6 级：
     *   p_a4 和 p_a3 之后的一拍对齐、p_a5 和 p_ex 同拍、
     *   p_a6 和 p_grs 同拍、p_a7 和 p_gr 同拍。
     * 中间那几级没有任何运算，只是为了把 |a| 拖到减法那一拍。 */
    reg signed [DW-1:0] p_a2, p_a3, p_a4, p_a5, p_a6, p_a7;
    reg                 p_neg2, p_neg3, p_neg4, p_neg5, p_neg6, p_neg7;

    /* ---------------- 级 9：a2 = a − gr，带符号 ---------------- */
    wire signed [24:0]   a2w  = {{1{p_a7[23]}}, p_a7} - {{1{p_gr[23]}}, p_gr};
    wire signed [24:0]   a2wc = (a2w < 0) ? 25'sd0 : a2w;
    wire signed [47:0]   yv_w = p_neg7 ? -{{23{a2wc[24]}}, a2wc}
                                      : {{23{a2wc[24]}}, a2wc};

    /* legacy 那条"无记忆"通路已经删掉了（2026-10-28 批 2）：
     * ATK=REL=0 时把包络改成瞬时值（见下面 env_r 的更新），输出 = thr + (a−thr)·ratio，
     * 就是 HLS `drc()` 的式子。唯一差别是余数取整方向（这里是 ceil，HLS 写的是 floor），
     * 最多差 1 LSB —— 对拍按 ±1 判，听不出来。
     * 这样做的原因是资源与时序：保留两条真通路要多 4 个 24×24 乘法器，
     * 而且 legacy 那串「乘法 → 移位 → 加法」挤在最后一拍，WNS 只剩 +0.25 ns。 */

    /* ---------------- 寄存器 ---------------- */
    /* 9 位：in_vld 延迟 1..9 拍，yv 在 t+10 有效，所以用 vpipe[8] 对齐 */
    reg [8:0] vpipe;

    always @(posedge clk) begin
        if (rst) begin
            env_r   <= {DW{1'b0}};
            p_band  <= {DW{1'b0}};
            p_gb    <= {DW{1'b0}};
            p_a     <= {DW{1'b0}};
            p_neg   <= 1'b0;
            p_diff  <= 25'sd0;
            p_step  <= {DW{1'b0}};
            p_prod  <= 49'sd0;
            p_a2    <= {DW{1'b0}};
            p_neg2  <= 1'b0;
            p_a3    <= {DW{1'b0}};
            p_neg3  <= 1'b0;
            p_a4    <= {DW{1'b0}};
            p_neg4  <= 1'b0;
            p_a5    <= {DW{1'b0}};
            p_neg5  <= 1'b0;
            p_a6    <= {DW{1'b0}};
            p_neg6  <= 1'b0;
            p_a7    <= {DW{1'b0}};
            p_neg7  <= 1'b0;
            p_ex    <= 25'sd0;
            p_epos  <= 1'b0;
            p_grs   <= 48'sd0;
            p_epos6 <= 1'b0;
            p_gr    <= {DW{1'b0}};
            vpipe   <= 9'd0;
            yv      <= {DW{1'b0}};
            yv_vld  <= 1'b0;
        end else begin
            /* 参数影子：不跟 rst，复位期间保持上一次的值无害 */
            omr_r      <= omr_w[DW-1:0];
            use_wdrc_r <= use_wdrc;

            /* 级 0..2 */
            p_band <= gb0;
            p_gb   <= gb1;
            p_a    <= aabs;
            p_neg  <= p_gb[DW-1];

            /* 级 3：差与步进打一拍（乘法单独占下一拍） */
            p_diff <= diff;
            p_step <= step;
            p_a2   <= p_a;
            p_neg2 <= p_neg;

            /* 级 4：乘法 */
            p_prod <= prod;
            p_a3   <= p_a2;
            p_neg3 <= p_neg2;

            /* 级 5：只在真采样进来时更新包络。
             * p_prod 在 t+5 有效，那时 vpipe[4] 正好是 1（in_vld 延迟 4 拍）。
             * 写成别的下标会把上一拍残留的 p_prod 算进去 —— 包络整个错位。
             *
             * ATK=REL=0（无记忆压缩）时直接写 p_a3 —— 那一拍 p_a3 正好是同
             * 一个采样的 |a|（p_a 打两拍、p_prod 从 p_diff 出来也是两拍），于是
             * 包络 = 瞬时幅度，走出来的就是 thr + (a−thr)·ratio。 */
            if (vpipe[4]) env_r <= use_wdrc_r ? enp : p_a3;
            p_a4   <= p_a3;
            p_neg4 <= p_neg3;

            /* 级 6：env − thr（p_ex 比数据晚一拍，靠 p_a5 对齐） */
            p_ex   <= ex;
            p_epos <= epos;
            p_a5   <= p_a4;
            p_neg5 <= p_neg4;

            /* 级 7：乘法 + 右移，先存下来 */
            p_grs   <= gr_s;
            p_epos6 <= p_epos;
            p_a6    <= p_a5;
            p_neg6  <= p_neg5;

            /* 级 8：饱和到 drc_t */
            p_gr   <= gr;
            p_a7   <= p_a6;
            p_neg7 <= p_neg6;

            /* 级 9 */
            yv     <= satd(yv_w);
            yv_vld <= vpipe[8];

            vpipe <= {vpipe[7:0], in_vld};
        end
    end

endmodule


/* ------------------------------------------------------------------ 四段 */
module fir_drc4 #(
    parameter integer BW        = 40,
    parameter integer DW        = 24,
    parameter integer FRAC_ACC  = 32,
    parameter integer FRAC_DRC  = 22,
    parameter integer OUT_SHIFT = 17
) (
    input  wire                  clk,
    input  wire                  rst,
    input  wire                  in_vld,
    input  wire signed [BW-1:0]  band0,
    input  wire signed [BW-1:0]  band1,
    input  wire signed [BW-1:0]  band2,
    input  wire signed [BW-1:0]  band3,
    /* 四段运行期增益（Q7.8）。老实例（fir_rtl_shell / fir_stream_shell）
     * 直接绑 16'sd256，和端口化之前逐位相同。 */
    input  wire signed [15:0]    gain0,
    input  wire signed [15:0]    gain1,
    input  wire signed [15:0]    gain2,
    input  wire signed [15:0]    gain3,
    /* 2026-10-28 批 2：四段 DRC 参数（Q2.22，24 位有符号），全部运行期。
     * 出厂值 = v25：thr 419456 / ratio 2936064 / atk 17408 / rel 896。
     * atk 与 rel 同时为 0 时切"无记忆"，见 fir_drc4_chan 的注释。 */
    input  wire signed [DW-1:0]  thr0,
    input  wire signed [DW-1:0]  thr1,
    input  wire signed [DW-1:0]  thr2,
    input  wire signed [DW-1:0]  thr3,
    input  wire signed [DW-1:0]  ratio0,
    input  wire signed [DW-1:0]  ratio1,
    input  wire signed [DW-1:0]  ratio2,
    input  wire signed [DW-1:0]  ratio3,
    input  wire signed [DW-1:0]  atk0,
    input  wire signed [DW-1:0]  atk1,
    input  wire signed [DW-1:0]  atk2,
    input  wire signed [DW-1:0]  atk3,
    input  wire signed [DW-1:0]  rel0,
    input  wire signed [DW-1:0]  rel1,
    input  wire signed [DW-1:0]  rel2,
    input  wire signed [DW-1:0]  rel3,
    output reg  signed [15:0]    y,
    output reg                   y_vld,
    /* 2026-10-28 批 1：四段包络/衰减量观察口，4×DW 位打包（段0 在最低位） */
    output wire [DW*4-1:0]       env_o,
    output wire [DW*4-1:0]       gr_o
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

    fir_drc4_chan #(.BW(BW), .DW(DW), .FRAC_ACC(FRAC_ACC), .FRAC_DRC(FRAC_DRC))
    u_b0 (.clk(clk), .rst(rst), .in_vld(in_vld), .band(band0),
            .env_o(env_o[0*DW +: DW]), .gr_o(gr_o[0*DW +: DW]),
            .gain_i(gain0), .thr_i(thr0), .ratio_i(ratio0),
            .atk_i(atk0), .rel_i(rel0), .yv(yv0), .yv_vld(v0));

    fir_drc4_chan #(.BW(BW), .DW(DW), .FRAC_ACC(FRAC_ACC), .FRAC_DRC(FRAC_DRC))
    u_b1 (.clk(clk), .rst(rst), .in_vld(in_vld), .band(band1),
            .env_o(env_o[1*DW +: DW]), .gr_o(gr_o[1*DW +: DW]),
            .gain_i(gain1), .thr_i(thr1), .ratio_i(ratio1),
            .atk_i(atk1), .rel_i(rel1), .yv(yv1), .yv_vld(v1));

    fir_drc4_chan #(.BW(BW), .DW(DW), .FRAC_ACC(FRAC_ACC), .FRAC_DRC(FRAC_DRC))
    u_b2 (.clk(clk), .rst(rst), .in_vld(in_vld), .band(band2),
            .env_o(env_o[2*DW +: DW]), .gr_o(gr_o[2*DW +: DW]),
            .gain_i(gain2), .thr_i(thr2), .ratio_i(ratio2),
            .atk_i(atk2), .rel_i(rel2), .yv(yv2), .yv_vld(v2));

    fir_drc4_chan #(.BW(BW), .DW(DW), .FRAC_ACC(FRAC_ACC), .FRAC_DRC(FRAC_DRC))
    u_b3 (.clk(clk), .rst(rst), .in_vld(in_vld), .band(band3),
            .env_o(env_o[3*DW +: DW]), .gr_o(gr_o[3*DW +: DW]),
            .gain_i(gain3), .thr_i(thr3), .ratio_i(ratio3),
            .atk_i(atk3), .rel_i(rel3), .yv(yv3), .yv_vld(v3));

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
