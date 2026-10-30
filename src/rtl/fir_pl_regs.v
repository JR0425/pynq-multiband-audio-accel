/* fir_pl_regs —— 全 PL 直连版的 AXI4-Lite 从机寄存器文件
 *
 * 契约就是 `桌面\PYNQ音频项目\PL直连_寄存器表.md`,地址/位/默认值一个字都别单边改。
 *
 * ================= 为什么用 initial 给默认值 =================
 *
 * 参数类寄存器(增益/阈值/压缩比/起停/斜坡)在 FPGA 配置时就该是出厂值,
 * 不需要跟着 rst_n 走 —— 用 initial 给初值,省掉 40 个寄存器的复位分支,
 * 综合也认(Xilinx 器件上电按 initial 装载)。**只有控制位和握手状态走 rst_n。**
 *
 * ================= 写操作只认整字 =================
 *
 * 字节使能只支持 4'hF(整字写)。页面/后端一律按 32 位对齐写,不需要部分写;
 * 真要部分写就先读-改-写。这样省掉 40 条按字节的赋值。
 *
 * ================= 地址分区 =================
 *
 *   0x000-0x07F  控制/状态/电平/限幅/音源/抓取
 *   0x100-0x17F  左耳参数 bank0
 *   0x200-0x27F  右耳参数 bank1(布局同 bank0)
 *   组内相对偏移: +0x00 GAIN[4] / +0x10 THR[4] / +0x20 RATIO[4]
 *                 +0x30 ATK[4]  / +0x40 REL[4] / +0x50 ENV[4](RO)
 *                 +0x60 GR[4](RO) / +0x70 RAMP_DIV,[+0x74] RAMP_BUSY(RO)
 *
 * 波形缓冲和播放缓冲不在这里,走 axi_bram_ctrl 的独立窗口。
 */

`timescale 1ns / 1ps

module fir_pl_regs #(
    parameter [31:0] ID_VALUE = 32'h504C_0002
) (
    input  wire        clk,
    input  wire        rst_n,

    /* ---- AXI4-Lite ---- */
    input  wire [11:0] s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    output wire [1:0]  s_axi_bresp,
    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [11:0] s_axi_araddr,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rvalid,
    input  wire        s_axi_rready,

    /* ---- 控制输出 ---- */
    output wire [31:0] ctrl,          // bit0 run / 1 drc_en / 2 flush(自清) / 3 mpo / 4 tone / 5 src / 8 clr
    output wire        clr_pulse,     // 单拍脉冲:清计数器

    /* ---- 参数组(打包,高位在前) ----
     * 位宽直接按下游 `fir_pl_direct` 的用法裁好：增益每段 16 位、其余每段 24 位。
     * 2026-10-28 晚修：这里原本是 128 位(每段 32 位)，BD 里用 xlslice 取 [63:0] 再当
     * 四个 16 位用 —— 四段 32 位打包后 seg1 落在 [63:32]，取低 64 位得到的是
     * {seg0 高 16, seg0 低 16, seg1 高 16, seg1 低 16}，于是 b1/b3 段吃到的增益恒 0、
     * b2 段吃到 b1 的值。批 1 位流就这样跑过去了（四段同增益所以只听得出"变响了"）。 */
    output wire [63:0]  gain_b0, gain_b1,                        // 四段 × 16 位 Q7.8
    output wire [95:0]  thr_b0, ratio_b0, atk_b0, rel_b0,        // 四段 × 24 位
    output wire [95:0]  thr_b1, ratio_b1, atk_b1, rel_b1,
    output wire [15:0]  ramp_div_b0, ramp_div_b1,

    /* ---- 其余控制 ---- */
    output wire [2:0]  ear_ctl,       // [0] indep [2:1] out_mode
    output wire [1:0]  tone_sel,
    output wire [15:0] tone_amp,
    output wire [15:0] mpo_th,
    output wire [31:0] mpo_rel,
    output wire [15:0] win_len,
    output wire [31:0] ply_ctl,       // [0] en [1] loop
    output wire [31:0] ply_len,
    output wire [31:0] scope_ctl,     // [0] arm [1] auto

    /* ---- 只读输入 ---- */
    input  wire [31:0]  stat0, stat1, stat2,
    input  wire [31:0]  smp_cnt, frm_cnt, ply_rd, ply_und,
    input  wire [127:0] env_b0, gr_b0, env_b1, gr_b1,
    input  wire [31:0]  lvl_in_lo, lvl_in_hi, lvl_out_lo, lvl_out_hi,
    input  wire [15:0]  peak_in, peak_out,
    input  wire [31:0]  mpo_cnt,
    input  wire [15:0]  mpo_peak,
    /* 2026-10-31：PL 里量的相邻采样跳变（fir_pl_direct 出）。
     * 0x78/0x7C 那个读窗口 40 µs/字，1024 点要 41 ms，比 21 ms 的可读窗口还长，
     * 当测量仪器不成立（§0.0.12 C 三条全被自己的伪影骗过），所以把判据下到 PL。 */
    input  wire [15:0]  pop_dx,
    input  wire         pop_vld,
    input  wire [31:0]  scope_seq,
    input  wire [12:0]  scope_wr,
    /* 抓取缓冲的读取窗口（2026-10-30 夜）。
     * 原本 PS 直接从 axi_bram_ctrl 读 0x43C60000，板上实测**读回恒为 0x8** ——
     * 写能进（波形确实抓得到），读通道不通。两片 BRAM（波表 + 抓取）都是这个
     * 症状，而同一块 AXI 上的 axi_gpio / fir_pl_regs 读得好好的，说明是 BRAM
     * 控制器这条读通路的问题，不是地址映射。
     * 绕开的办法：把 BRAM 的 A 口交给本模块——写 0x78 选下标，读 0x7C 取数据。
     * 抓取已经"冻住"时环形缓冲不再变，4096 个点分 8192 次 MMIO 读完约 10 ms。 */
    input  wire [31:0]  cap_rdata,
    output wire [12:0]  cap_raddr,
    input  wire         ramp_busy_b0, ramp_busy_b1
);

    /* ================= 存储 ================= */

    reg [31:0] ctrl_reg;
    reg [31:0] ear_reg, tone_sel_reg, tone_amp_reg, mpo_th_reg, mpo_rel_reg;
    reg [31:0] win_len_reg, ply_ctl_reg, ply_len_reg, scope_ctl_reg;
    reg [12:0] cap_idx_r;          // 0x78：抓取缓冲读下标（0x7C 返回该字）

    /* 参数:idx[2]=bank(0 左 1 右),[1:0]=段号 */
    reg [31:0] gain_r  [0:7];
    reg [31:0] thr_r   [0:7];
    reg [31:0] ratio_r [0:7];
    reg [31:0] atk_r   [0:7];
    reg [31:0] rel_r   [0:7];
    reg [15:0] ramp_r  [0:1];

    integer i;

    /* 出厂值:与 v25 逐位一致(见寄存器表 §5) */
    initial begin
        ctrl_reg     = 32'h0;          // run=0, drc_en=0(只过前端), 其余关
        ear_reg      = 32'h0;
        tone_sel_reg = 32'h0;
        tone_amp_reg = 32'h0;
        mpo_th_reg   = 32'd32767;      // 限幅阈值满量程 = 等于不动作
        mpo_rel_reg  = 32'd896;        // 100 ms
        win_len_reg  = 32'd1024;
        ply_ctl_reg  = 32'h0;
        ply_len_reg  = 32'h0;
        scope_ctl_reg= 32'h0;
        cap_idx_r  = 13'h0;
        for (i = 0; i < 8; i = i + 1) begin
            gain_r[i]  = 32'd256;      // 0 dB
            thr_r[i]   = 32'd419456;   // 3277(Q1.15) << 7 = -20 dBFS
            ratio_r[i] = 32'd2936064;  // 22938(Q1.15) << 7 = 0.7
            /* 2026-10-28 批 2：ATK/REL 原来是 0（= 无记忆压缩）。四参数改成
             * 运行期之后，0 仍然是"无记忆"的合法选择，但**出厂值必须是 v25
             * 那套 WDRC 参数**，否则批 2 位流一上电听着就和批 1 不一样。
             * 136/7 是 Q1.15 的原始整数，核里 <<7 → 17408 / 896。 */
            atk_r[i]   = 32'd17408;    // 136 << 7 = 5 ms
            rel_r[i]   = 32'd896;      // 7   << 7 = 100 ms
        end
        /* RAMP_DIV 默认 0 = 每采样走 1 LSB 的线性斜坡（寄存器表 v25 约定）。
         * 2026-10-28 教训：这里的 +0x70 是 **bank 内相对偏移**，bank0 绝对地址
         * 是 0x170 不是 0x070 —— 板上自检曾读 0x070 读到恒 0，误判成
         * "写不进去"，其实 RTL 一直是对的。 */
        ramp_r[0] = 16'd0;
        ramp_r[1] = 16'd0;
    end

    /* ================= 写通道 ================= */

    reg        aw_have;
    reg [11:0] aw_addr;
    reg        w_have;
    reg [31:0] w_data;
    reg [3:0]  w_strb;
    reg        b_valid;

    assign s_axi_awready = ~aw_have;
    assign s_axi_wready  = ~w_have;
    assign s_axi_bvalid  = b_valid;
    assign s_axi_bresp   = 2'b00;

    /* flush 自清:写 CTRL 时 bit2 置 1,16 拍后自动落回 0 */
    reg [3:0] flush_cnt;

    wire        aw_en = s_axi_awvalid && ~aw_have;
    wire        w_en  = s_axi_wvalid  && ~w_have;
    wire        do_wr = aw_have && w_have;
    wire [11:0] wa    = aw_addr;

    /* 单拍清零脉冲:写 0x070 或 CTRL[8] */
    reg        clr_r;
    assign clr_pulse = clr_r;

    /* ================= 跳变计数（0x80 / 0x84，2026-10-31） ================= */
    /* 判据（§0.0.12 C）：安静 60 s POP_CNT 不涨 = 链路干净；涨而 POP_MAX 只有
     * 几百 = 是噪声；POP_MAX 上万 = 真的丢样点/重样点。
     * 8192 = 满量程的 1/4，比任何语音/音乐的单采样变化都大得多。 */
    reg [31:0] pop_cnt_r;
    reg [15:0] pop_max_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || clr_pulse) begin
            pop_cnt_r <= 32'd0;
            pop_max_r <= 16'd0;
        end else if (pop_vld) begin
            if (pop_dx > 16'd8192)  pop_cnt_r <= pop_cnt_r + 32'd1;
            if (pop_dx > pop_max_r) pop_max_r <= pop_dx;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            aw_have   <= 1'b0;
            aw_addr   <= 12'd0;
            w_have    <= 1'b0;
            w_data    <= 32'd0;
            w_strb    <= 4'd0;
            b_valid   <= 1'b0;
            flush_cnt <= 4'd0;
            clr_r     <= 1'b0;
            ctrl_reg  <= 32'd0;
        end else begin
            clr_r <= 1'b0;

            if (aw_en) begin
                aw_addr <= s_axi_awaddr;
                aw_have <= 1'b1;
            end
            if (w_en) begin
                w_data <= s_axi_wdata;
                w_strb <= s_axi_wstrb;
                w_have <= 1'b1;
            end

            if (do_wr) begin
                aw_have <= 1'b0;
                w_have  <= 1'b0;
                b_valid <= 1'b1;

                if (w_strb == 4'hF) begin
                    /* ---- 参数组:0x1xx / 0x2xx ---- */
                    if (wa[11:7] == 5'b00010 || wa[11:7] == 5'b00100) begin
                        /* bank0 = 0x1xx(bit8)、bank1 = 0x2xx(bit9)。
                         * 2026-10-29 板上抓到的 bug：这里原来用 wa[11] 选 bank，
                         * 而 0x100/0x200 的 bit11 都是 0 —— 于是**所有写给右耳的
                         * 参数都落到左耳上**（读 0x2xx 也读到左耳）。现象是"双耳
                         * 两套处方怎么调都一起变"，看着像参数没接进通路。 */
                        case (wa[6:4])
                            3'd0: gain_r[{wa[9], wa[3:2]}]  <= w_data;
                            3'd1: thr_r [{wa[9], wa[3:2]}]  <= w_data;
                            3'd2: ratio_r[{wa[9], wa[3:2]}] <= w_data;
                            3'd3: atk_r [{wa[9], wa[3:2]}]  <= w_data;
                            3'd4: rel_r [{wa[9], wa[3:2]}]  <= w_data;
                            3'd7: if (wa[3:2] == 2'd0) ramp_r[wa[9]] <= w_data[15:0];
                            default: ;   /* 0x50/0x60 是 RO */
                        endcase
                    end else begin
                        /* ---- 控制区 ---- */
                        case (wa[6:0])
                            7'h04: begin
                                ctrl_reg <= w_data;
                                if (w_data[2]) flush_cnt <= 4'd15;
                                if (w_data[8]) clr_r <= 1'b1;
                            end
                            7'h24: ply_ctl_reg   <= {30'd0, w_data[1:0]};
                            7'h28: ply_len_reg   <= {18'd0, w_data[13:0]};
                            7'h3C: win_len_reg   <= {16'd0, w_data[15:0]};
                            7'h48: mpo_th_reg    <= {16'd0, w_data[15:0]};
                            7'h4C: mpo_rel_reg   <= w_data;
                            7'h58: tone_sel_reg  <= {30'd0, w_data[1:0]};
                            7'h5C: tone_amp_reg  <= {16'd0, w_data[15:0]};
                            7'h60: ear_reg       <= {29'd0, w_data[2:0]};
                            7'h64: scope_ctl_reg <= {30'd0, w_data[1:0]};
                            7'h70: clr_r         <= 1'b1;      // CLR_STAT
                            7'h78: cap_idx_r     <= w_data[12:0];   // 抓取缓冲读下标
                            default: ;
                        endcase
                    end
                end
            end

            if (b_valid && s_axi_bready) b_valid <= 1'b0;

            /* flush 自清 */
            if (flush_cnt != 4'd0) begin
                flush_cnt <= flush_cnt - 4'd1;
                if (flush_cnt == 4'd1) ctrl_reg[2] <= 1'b0;
            end
        end
    end

    /* ================= 读通道 ================= */

    reg        r_valid;
    reg [31:0] r_data;

    assign s_axi_arready = ~r_valid;
    assign s_axi_rvalid  = r_valid;
    assign s_axi_rdata   = r_data;
    assign s_axi_rresp   = 2'b00;

    /* 参数组读:bank 由 addr[9] 选(0x1xx=0, 0x2xx=1),段号 addr[3:2] */
    function [31:0] bank_rd;
        input [11:0] a;
        input [2:0]  grp;
        reg   [2:0]  idx;
        begin
            idx = {a[9], a[3:2]};
            case (grp)
                3'd0: bank_rd = gain_r[idx];
                3'd1: bank_rd = thr_r[idx];
                3'd2: bank_rd = ratio_r[idx];
                3'd3: bank_rd = atk_r[idx];
                3'd4: bank_rd = rel_r[idx];
                3'd5: bank_rd = (a[9] ? env_b1 : env_b0) >> ({a[3:2], 5'd0});
                3'd6: bank_rd = (a[9] ? gr_b1  : gr_b0 ) >> ({a[3:2], 5'd0});
                default: bank_rd = 32'd0;
            endcase
        end
    endfunction

    function [31:0] rd_mux;
        input [11:0] a;
        begin
            rd_mux = 32'd0;
            if (a[11:7] == 5'b00010 || a[11:7] == 5'b00100) begin
                if (a[6:4] == 3'd7) begin
                    if (a[3:2] == 2'd0) rd_mux = {16'd0, ramp_r[a[9]]};
                    else if (a[3:2] == 2'd1)
                        rd_mux = {31'd0, a[9] ? ramp_busy_b1 : ramp_busy_b0};
                end else begin
                    rd_mux = bank_rd(a, a[6:4]);
                end
            end else begin
                // 2026-10-30: 原来按 a[6:0] 索引，7'h80 被截成 7'h00，
                // 读 0x80 拿到的是 ID_VALUE(0x504C0002)、0x84 落回 ctrl_reg。
                // 扩到 a[7:0]，0x80/0x84 才真的读得到跳变计数器。
                case (a[7:0])
                    8'h00: rd_mux = ID_VALUE;
                    8'h04: rd_mux = ctrl_reg;
                    8'h08: rd_mux = stat0;
                    8'h0C: rd_mux = stat1;
                    8'h10: rd_mux = stat2;
                    8'h14: rd_mux = smp_cnt;
                    8'h18: rd_mux = frm_cnt;
                    8'h1C: rd_mux = ply_rd;
                    8'h20: rd_mux = ply_und;
                    8'h24: rd_mux = ply_ctl_reg;
                    8'h28: rd_mux = ply_len_reg;
                    8'h2C: rd_mux = lvl_in_lo;
                    8'h30: rd_mux = lvl_in_hi;
                    8'h34: rd_mux = lvl_out_lo;
                    8'h38: rd_mux = lvl_out_hi;
                    8'h3C: rd_mux = win_len_reg;
                    8'h40: rd_mux = {16'd0, peak_in};
                    8'h44: rd_mux = {16'd0, peak_out};
                    8'h48: rd_mux = mpo_th_reg;
                    8'h4C: rd_mux = mpo_rel_reg;
                    8'h50: rd_mux = mpo_cnt;
                    8'h54: rd_mux = {16'd0, mpo_peak};
                    8'h58: rd_mux = tone_sel_reg;
                    8'h5C: rd_mux = tone_amp_reg;
                    8'h60: rd_mux = ear_reg;
                    8'h64: rd_mux = scope_ctl_reg;
                    8'h68: rd_mux = scope_seq;
                    8'h6C: rd_mux = {19'd0, scope_wr};
                    8'h78: rd_mux = {19'd0, cap_idx_r};
                    8'h7C: rd_mux = cap_rdata;
                    8'h80: rd_mux = pop_cnt_r;
                    8'h84: rd_mux = {16'd0, pop_max_r};
                    default: rd_mux = 32'd0;
                endcase
            end
        end
    endfunction

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            r_valid <= 1'b0;
            r_data  <= 32'd0;
        end else begin
            if (s_axi_arvalid && !r_valid) begin
                r_data  <= rd_mux(s_axi_araddr);
                r_valid <= 1'b1;
            end
            if (r_valid && s_axi_rready) r_valid <= 1'b0;
        end
    end

    /* ================= 输出 ================= */

    assign ctrl = ctrl_reg;

    /* 打包一律「高位在前」，且只留下游要的低位宽：增益 16 位、其余 24 位。
     * gain_r 是有符号 Q7.8，截低 16 位就是它的二进制补码，负增益照样对。 */
    assign gain_b0  = {gain_r[3][15:0],  gain_r[2][15:0],  gain_r[1][15:0],  gain_r[0][15:0]};
    assign gain_b1  = {gain_r[7][15:0],  gain_r[6][15:0],  gain_r[5][15:0],  gain_r[4][15:0]};
    assign thr_b0   = {thr_r[3][23:0],   thr_r[2][23:0],   thr_r[1][23:0],   thr_r[0][23:0]};
    assign thr_b1   = {thr_r[7][23:0],   thr_r[6][23:0],   thr_r[5][23:0],   thr_r[4][23:0]};
    assign ratio_b0 = {ratio_r[3][23:0], ratio_r[2][23:0], ratio_r[1][23:0], ratio_r[0][23:0]};
    assign ratio_b1 = {ratio_r[7][23:0], ratio_r[6][23:0], ratio_r[5][23:0], ratio_r[4][23:0]};
    assign atk_b0   = {atk_r[3][23:0],   atk_r[2][23:0],   atk_r[1][23:0],   atk_r[0][23:0]};
    assign atk_b1   = {atk_r[7][23:0],   atk_r[6][23:0],   atk_r[5][23:0],   atk_r[4][23:0]};
    assign rel_b0   = {rel_r[3][23:0],   rel_r[2][23:0],   rel_r[1][23:0],   rel_r[0][23:0]};
    assign rel_b1   = {rel_r[7][23:0],   rel_r[6][23:0],   rel_r[5][23:0],   rel_r[4][23:0]};

    assign ramp_div_b0 = ramp_r[0];
    assign ramp_div_b1 = ramp_r[1];

    assign ear_ctl   = ear_reg[2:0];
    assign tone_sel  = tone_sel_reg[1:0];
    assign tone_amp  = tone_amp_reg[15:0];
    assign mpo_th    = mpo_th_reg[15:0];
    assign mpo_rel   = mpo_rel_reg;
    assign win_len   = win_len_reg[15:0];
    assign ply_ctl   = ply_ctl_reg;
    assign ply_len   = ply_len_reg;
    assign scope_ctl = scope_ctl_reg;
    assign cap_raddr = cap_idx_r;

endmodule
