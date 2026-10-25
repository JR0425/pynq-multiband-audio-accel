/* fir_rtl_shell —— 给 fir_mb_fe 套一个「块模式」外壳，只为了能上板量拍数
 *
 * ================== 它解决什么问题 ==================
 *
 * fir_mb_fe 自己是"3 拍/采样"的，但那个数一直只是 xsim 数出来的。
 * 这一层把 PS 和前端接起来，让板上也能数一遍，并且能和 HLS 那版同音源对拍。
 *
 * 明确**不做**（和 PLAN §11.14 §4 的三条护栏一致）：
 *   · 不写 AXI4-Master —— 数据全靠 axi_bram_ctrl 的 PORTB 走，本模块没有任何 AXI 逻辑
 *   · 不做 DMA、不管缓存一致性 —— PS 侧 mmap 读写，慢点无所谓，这是离线块模式
 *   · 不碰出货的 fir.bit —— 这块只进独立的 fir_rtl.bit
 *
 * ================== 为什么分四段跑 ==================
 *
 * 朴素写法是"PORTB 读一个 → 喂 fir 一个"，但 PORTB 的读延迟不在本模块手里
 * （取决于 axi_bram_ctrl 的配置），差一拍就整体错位，而现象是"数据全对、
 * 只是晚一个采样"—— 这个坑 fir_mb_fe 里踩过一次。
 *
 * 所以刻意分成四段，慢一点但**显然正确**：
 *
 *   ⓿ RESET  每次启动先给前端 8 拍复位，清空延迟线与相位
 *            （不这么做的话，第二块会被上一块最后 195 个采样污染）
 *   ① LOAD   每 5 拍读一个采样，全读进本地 buf_mem（等够再抓，延迟 1~4 都安全）
 *   ② RUN    buf_mem 按 fir 的 din_ready 节奏喂进去，这一段就是"3 拍/采样"
 *   ③ DRAIN  停止喂数，等流水排空，把残余的 dout 写出去
 *
 * 计时**只覆盖 ②③**（第一个 din 被接收 → 最后一个 dout 出来），
 * LOAD 那段的慢不掺进来。
 *
 * ================== 寄存器映射（和 build_rtl.tcl 里的一致） ==================
 *
 *   ctrl   [31:0]  PS 写（axi_gpio 输出通道，0x43C10000 + 0x00）
 *                  bit[15:0] = n_samples（1..8192）
 *                  bit[16]   = start（上升沿启动；PS 用完要写回 0）
 *                  bit[17]   = drc_en（0 = 只过前端；1 = 再过四段增益 + WDRC）
 *                              **在 start 上升沿那一刻锁存**，一块之内不变
 *
 *   stat_lo[31:0]  PS 读（axi_gpio 双通道的通道 1，0x43C20000 + 0x00）
 *                  total_cycles
 *   stat_hi[31:0]  PS 读（axi_gpio 双通道的通道 2，0x43C20000 + 0x08）
 *                  [15:0]  = n_out
 *                  [16]    = done
 *                  [17]    = busy
 *                  [18]    = overflow（块长非法 / 计数溢出）
 *
 *   拍/采样 ≈ total_cycles / (n_out - 1)
 *   n=4800、3 拍/采样时 total_cycles ≈ 3*(n-1) + 10（实测 14407）
 *   drc_en=1 时末尾再多 10 拍（DRC 流水 9 级 + 写回 1 拍），≈ 3*(n-1) + 20（实测 14417）
 *
 * ⚠️ 这是**核内**数。整机一块的往返时间由 PS 侧 mmap/MMIO 搬运决定，
 *    两者不能换算成一个"整机加速比"（PLAN 3.4.3 口径）。
 *
 * ================== BRAM 端口用的是**字**地址 ==================
 *
 * 外壳直接连 blk_mem_gen 的 addrb/web/dinb/doutb（普通信号，不走 bram 接口），
 * 所以地址就是字下标 0..8191，13 位。
 * 另一侧 A 口由 axi_bram_ctrl 驱动：它给的是 15 位**字节**地址，
 * 经 bram 接口接到 13 位的 addra 时正好丢掉低 2 位，语义一致。
 */

`timescale 1ns / 1ps

module fir_rtl_shell #(
    parameter integer DW      = 32,     // BRAM 数据宽度
    parameter integer AW      = 13,     // BRAM **字**地址宽度（8192 字）
    parameter integer MAXN    = 8192,   // 单块最大采样数
    parameter integer RD_WAIT = 2,      // LOAD 段每个采样额外等的拍数
    parameter integer RST_CYC = 8       // 每块启动前给前端的复位拍数
) (
    input  wire           clk,
    input  wire           rst_n,

    /* 控制（来自 axi_gpio 的输出通道） */
    input  wire [31:0]    ctrl,

    /* 状态（回给 axi_gpio 的两个输入通道） */
    output wire [31:0]    stat_lo,
    output wire [31:0]    stat_hi,

    /* 输入缓冲：接一块双口 BRAM 的 B 口
     *
     * 故意**不用** bram 接口标注：bram 接口的主端定义里 CLK/RST 是输出，
     * 而本模块需要的是输入时钟；HDL 推断会因此把整个接口判成从端，
     * 又从端不能连从端（BD 41-171）。这里全部当普通信号，在 BD 里一根根连。 */
    input  wire           bram_in_clk,
    input  wire           bram_in_rst,
    output wire           bram_in_en,
    output wire [3:0]     bram_in_we,
    output wire [AW-1:0]  bram_in_addr,
    output wire [DW-1:0]  bram_in_din,
    input  wire [DW-1:0]  bram_in_dout,

    /* 输出缓冲：另一块双口 BRAM 的 B 口 */
    input  wire           bram_out_clk,
    input  wire           bram_out_rst,
    output wire           bram_out_en,
    output wire [3:0]     bram_out_we,
    output wire [AW-1:0]  bram_out_addr,
    output wire [DW-1:0]  bram_out_din,
    input  wire [DW-1:0]  bram_out_dout
);

    localparam integer IDXW = AW;       // 字下标位宽

    wire        start     = ctrl[16];
    wire [15:0] n_samples = ctrl[15:0];

    reg         done, busy, overflow;
    reg  [31:0] total_cycles;
    reg  [15:0] n_out;

    assign stat_lo = total_cycles;
    assign stat_hi = {13'd0, overflow, busy, done, n_out};

    /* ================= fir_mb_fe 本体 ================= */

    wire signed [15:0] f_din;
    wire               f_din_vld;
    wire               f_din_ready;
    wire signed [15:0] f_dout;
    wire               f_dout_vld;

    /* 9 路对拍口不往外引 —— 上板只量拍数和最终输出，
     * 那 9 路在 xsim 里已经逐位对过 1000/1000（rtl_vs_hls.md §5.5）。 */
    wire signed [39:0] dbg_ylp0, dbg_ylp1, dbg_ylp2;
    wire signed [39:0] dbg_b0, dbg_b1, dbg_b2, dbg_b3, dbg_acc;

    reg fe_rst;

    fir_mb_fe u_fe (
        .clk       (clk),
        .rst       (fe_rst),
        .din       (f_din),
        .din_vld   (f_din_vld),
        .din_ready (f_din_ready),
        .ylp0      (dbg_ylp0),
        .ylp1      (dbg_ylp1),
        .ylp2      (dbg_ylp2),
        .band0     (dbg_b0),
        .band1     (dbg_b1),
        .band2     (dbg_b2),
        .band3     (dbg_b3),
        .acc_raw   (dbg_acc),
        .dout      (f_dout),
        .dout_vld  (f_dout_vld)
    );

    /* ================= 四段增益 + WDRC（出货 v25 的压缩路径） ================= */
    /* 前端的 band0..3 和 dout/dout_vld 是同一拍寄存出来的，所以四个 band
     * 在 f_dout_vld 为高的那一拍就是对齐的 —— 直接拿它当 in_vld。
     * 复位用 fe_rst：每块 RUN 之前都会先走 8 拍 S_RST，把包络清掉。 */
    wire signed [15:0] drc_y;
    wire               drc_y_vld;

    fir_drc4 u_drc (
        .clk    (clk),
        .rst    (fe_rst),
        .in_vld (f_dout_vld),
        .band0  (dbg_b0),
        .band1  (dbg_b1),
        .band2  (dbg_b2),
        .band3  (dbg_b3),
        .y      (drc_y),
        .y_vld  (drc_y_vld)
    );

    /* drc_en 在 start 上升沿锁存：一块之内两条路不会来回切，
     * 免得中途切换时 n_out 的计数节奏对不上。 */
    reg drc_en_r;

    wire signed [15:0] out_q   = drc_en_r ? drc_y     : f_dout;
    wire               out_vld = drc_en_r ? drc_y_vld : f_dout_vld;

    /* ================= 本地缓存：写口 LOAD，读口 RUN ================= */
    /* 寄存器化读口（1 拍延迟），配合 din_ready 的 3 拍节奏提前取下一个。
     * 不用组合读 —— 8192×16 的组合 mux 会吃掉上万 LUT。 */
    reg signed [15:0] buf_mem [0:MAXN-1];
    reg  [IDXW-1:0]   buf_ra;
    reg               buf_re;
    reg signed [15:0] buf_q;

    always @(posedge clk) begin
        if (!rst_n)      buf_q <= 16'sd0;
        else if (buf_re) buf_q <= buf_mem[buf_ra];
    end

    assign f_din = buf_q;

    /* ================= 状态机 ================= */

    localparam [2:0] S_IDLE  = 3'd0,
                     S_RST   = 3'd1,
                     S_LOAD  = 3'd2,
                     S_RUN   = 3'd3,
                     S_DRAIN = 3'd4,
                     S_DONE  = 3'd5;

    reg [2:0]    st;
    reg          start_d;
    reg [15:0]   n;
    reg [15:0]   ld_ptr;
    reg [3:0]    ld_wait;
    reg          ld_en;
    reg [IDXW-1:0] ld_idx;
    reg [15:0]   run_ptr;
    reg          primed;         // buf_q 已经装好第一个采样
    reg          wr_en;
    reg [IDXW-1:0] wr_idx;
    reg [DW-1:0] wr_data;
    reg [31:0]   cyc;
    reg [31:0]   t0;
    reg          t0_vld;
    reg [3:0]    rst_cnt;
    reg [15:0]   drain_wait;

    wire start_rise = start & ~start_d;

    /* 喂数：fir 说 ready、还有数据没喂、且第一个采样已装好 */
    wire feed = (st == S_RUN) && f_din_ready && primed && (run_ptr < n);
    assign f_din_vld = feed;

    /* 输入侧只读；地址是**字**下标（BD 里直接连 blk_mem_gen 的 addrb） */
    assign bram_in_en   = ld_en;
    assign bram_in_we   = 4'b0000;
    assign bram_in_addr = ld_idx;
    assign bram_in_din  = {DW{1'b0}};

    /* 输出侧只写 */
    assign bram_out_en   = wr_en;
    assign bram_out_we   = wr_en ? 4'b1111 : 4'b0000;
    assign bram_out_addr = wr_idx;
    assign bram_out_din  = wr_data;

    always @(posedge clk) begin
        if (!rst_n) begin
            st           <= S_IDLE;
            start_d      <= 1'b0;
            n            <= 16'd0;
            ld_ptr       <= 16'd0;
            ld_wait      <= 4'd0;
            ld_en        <= 1'b0;
            ld_idx       <= {IDXW{1'b0}};
            run_ptr      <= 16'd0;
            primed       <= 1'b0;
            wr_en        <= 1'b0;
            wr_idx       <= {IDXW{1'b0}};
            wr_data      <= {DW{1'b0}};
            cyc          <= 32'd0;
            t0           <= 32'd0;
            t0_vld       <= 1'b0;
            rst_cnt      <= 4'd0;
            drain_wait   <= 16'd0;
            done         <= 1'b0;
            busy         <= 1'b0;
            total_cycles <= 32'd0;
            n_out        <= 16'd0;
            overflow     <= 1'b0;
            buf_ra       <= {IDXW{1'b0}};
            buf_re       <= 1'b0;
            fe_rst       <= 1'b1;
            drc_en_r     <= 1'b0;
        end else begin
            start_d <= start;
            cyc     <= cyc + 32'd1;
            ld_en   <= 1'b0;
            wr_en   <= 1'b0;
            buf_re  <= 1'b0;
            fe_rst  <= (st == S_RST);      // 只在 S_RST 那几拍为 1

            /* ---- 输出写回：任何一个采样出来就写一格 ---- */
            if (out_vld) begin
                wr_en   <= 1'b1;
                wr_idx  <= n_out[IDXW-1:0];
                wr_data <= {{(DW-16){out_q[15]}}, out_q};
                if (n_out == 16'hFFFF) overflow <= 1'b1;
                else                   n_out    <= n_out + 16'd1;
            end

            /* ---- 计时 ---- */
            if (feed && !t0_vld) begin
                t0     <= cyc;
                t0_vld <= 1'b1;
            end
            if (out_vld && t0_vld && (n_out == n - 16'd1)) begin
                total_cycles <= (cyc + 32'd1) - t0;
            end

            case (st)
            S_IDLE: begin
                busy <= 1'b0;
                if (start_rise) begin
                    done     <= 1'b0;
                    n_out    <= 16'd0;
                    t0_vld   <= 1'b0;
                    run_ptr  <= 16'd0;
                    ld_ptr   <= 16'd0;
                    ld_wait  <= 4'd0;
                    primed   <= 1'b0;
                    overflow <= 1'b0;
                    drc_en_r <= ctrl[17];
                    if ((n_samples == 16'd0) || (n_samples > MAXN)) begin
                        overflow <= 1'b1;
                        done     <= 1'b1;
                        st       <= S_DONE;
                    end else begin
                        n       <= n_samples;
                        busy    <= 1'b1;
                        rst_cnt <= RST_CYC;
                        st      <= S_RST;
                    end
                end
            end

            /* ⓿ 前置复位：把前端的延迟线/相位清干净，块与块之间互不影响 */
            S_RST: begin
                if (rst_cnt == 4'd0) st <= S_LOAD;
                else                 rst_cnt <= rst_cnt - 4'd1;
            end

            /* ① LOAD：慢速读，每 5 拍一个，等够再抓 */
            S_LOAD: begin
                if (ld_wait == 4'd0) begin
                    ld_en   <= 1'b1;
                    ld_idx  <= ld_ptr[IDXW-1:0];
                    ld_wait <= RD_WAIT + 2;
                end else begin
                    ld_wait <= ld_wait - 4'd1;
                    if (ld_wait == 4'd1) begin
                        buf_mem[ld_ptr[IDXW-1:0]] <= bram_in_dout[15:0];
                        if (ld_ptr == n - 16'd1) begin
                            /* 进 RUN 之前就把第 0 个采样读出来 */
                            buf_ra     <= {IDXW{1'b0}};
                            buf_re     <= 1'b1;
                            drain_wait <= 16'd0;
                            st         <= S_RUN;
                        end else begin
                            ld_ptr <= ld_ptr + 16'd1;
                        end
                    end
                end
            end

            /* ② RUN：按 din_ready 喂数。每喂一个，立刻预取下一个
             *    （读口 1 拍延迟，下一次 feed 至少 2 拍之后，一定来得及） */
            S_RUN: begin
                if (!primed) primed <= 1'b1;      // buf_q 已装好 run_ptr=0

                if (feed) begin
                    if (run_ptr == n - 16'd1) begin
                        st         <= S_DRAIN;
                        drain_wait <= 16'd0;
                    end else begin
                        buf_ra  <= run_ptr[IDXW-1:0] + 1'b1;
                        buf_re  <= 1'b1;
                        run_ptr <= run_ptr + 16'd1;
                    end
                end
            end

            /* ③ DRAIN：不再喂数，等最后 8 拍流水把残余吐完 */
            S_DRAIN: begin
                drain_wait <= drain_wait + 16'd1;
                if (n_out == n)                 st <= S_DONE;
                else if (drain_wait > 16'd4000) st <= S_DONE;   // 保护：不死等
            end

            S_DONE: begin
                busy <= 1'b0;
                done <= 1'b1;
                st   <= S_IDLE;
            end

            default: st <= S_IDLE;
            endcase
        end
    end

endmodule
