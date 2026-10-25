/* fir_stream_shell —— 把 fir_mb_fe + fir_drc4 接到 AXI-Stream 上（DMA 版）
 *
 * 和 fir_rtl_shell（块模式，AXI-Lite + 双口 BRAM）的关系：
 *   同一个前端、同一个压缩级、同一套复位与计时口径；
 *   区别只在数据怎么进出 —— 这里没有 BRAM，输入从 s_axis 进来、输出从 m_axis 出去。
 *
 * 约束（照 PLAN §11.14 §4 的护栏）：本模块**不写任何 AXI4 / AXI-Lite 逻辑**，
 * 只做 valid/ready 握手。地址、突发、描述符全在 axi_dma 里。
 *
 * ================== 时序与复位 ==================
 *
 *   · 上电和每传完一块，都先给前端 RST_CYC 拍复位（清延迟线、相位、包络），
 *     所以每块都是独立的、和块模式逐位一致的结果。
 *   · 复位期间 s_axis_tready = 0，MM2S 自然被挡住，不会丢数据。
 *   · 输入按前端的 din_ready（3 拍一个采样）节流；输出侧有一个 16 深的
 *     小 FIFO 吸收流水线错位。FIFO 到 FIFO_LIM 就不再收新输入 ——
 *     不能用"挡住输出"的办法背压，因为 fir_mb_fe 没有输出 ready，
 *     它的 dout_vld 是推式的，只能从输入端节流。
 *
 * ================== TLAST ==================
 *
 * 输入流的 TLAST 标记本块的最后一个采样（axi_dma 的 MM2S 会带出来）。
 * 记下 n_exp，输出第 n_exp 个采样时拉出 TLAST，S2MM 靠它结束一次传输。
 * 前端的群延迟体现在**值**上（输出 = 输入延后 96 个采样），不体现在个数上：
 * 复位后延迟线是 0，前 96 个输出就是 0，所以进多少个就出多少个。
 *
 * ================== 寄存器（与块模式同一套编码） ==================
 *
 *   ctrl   [31:0]  bit[0] = drc_en（在本次传输开始时锁存）
 *   stat_lo[31:0]  total_cycles（第一个输入被接收 → 最后一个输出被接收）
 *   stat_hi[31:0]  [15:0] n_out  [16] done  [17] busy  [18] overflow
 *                  overflow 只在输出 FIFO 真的溢出时置位 —— 正常路径不该出现。
 */

`timescale 1ns / 1ps

module fir_stream_shell #(
    parameter integer RST_CYC  = 8,     // 每块启动前给前端的复位拍数
    parameter integer FIFO_AW  = 4,     // 输出 FIFO 深度 = 2^FIFO_AW
    parameter integer FIFO_LIM = 12     // 输入节流门限（到这么多就停止收输入）
) (
    input  wire        clk,
    input  wire        rst_n,

    /* 控制 / 状态（axi_gpio） */
    input  wire [31:0] ctrl,
    output wire [31:0] stat_lo,
    output wire [31:0] stat_hi,

    /* AXI-Stream 从端（来自 axi_dma MM2S）
     * X_INTERFACE_INFO 是给 Vivado 看的：BD 里 module reference 靠这几行把
     * 四根普通端口合成一个 S_AXIS 接口，才能和 axi_dma 的 M_AXIS_MM2S 直接连。
     * xsim 不认识这些属性，不影响仿真。 */
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXIS, TDATA_NUM_BYTES 4, HAS_TKEEP 0, HAS_TSTRB 0, HAS_TLAST 1, TUSER_WIDTH 0, TDEST_WIDTH 0, TID_WIDTH 0" *)
    input  wire [31:0] s_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *)
    input  wire        s_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *)
    output wire        s_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *)
    input  wire        s_axis_tlast,

    /* AXI-Stream 主端（去 axi_dma S2MM） */
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_AXIS, TDATA_NUM_BYTES 4, HAS_TKEEP 0, HAS_TSTRB 0, HAS_TLAST 1, TUSER_WIDTH 0, TDEST_WIDTH 0, TID_WIDTH 0" *)
    output wire [31:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *)
    output wire        m_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *)
    input  wire        m_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *)
    output wire        m_axis_tlast
);

    /* ================= fir_mb_fe ================= */

    wire signed [15:0] f_din;
    wire               f_din_vld;
    wire               f_din_ready;
    wire signed [15:0] f_dout;
    wire               f_dout_vld;

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

    /* ================= 四段增益 + WDRC ================= */

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

    reg drc_en_r;

    wire signed [15:0] out_q   = drc_en_r ? drc_y     : f_dout;
    wire               out_vld = drc_en_r ? drc_y_vld : f_dout_vld;

    /* ================= 输出 FIFO ================= */

    reg  [15:0]     fifo_mem [0:(1<<FIFO_AW)-1];
    reg  [FIFO_AW:0] wptr, rptr;

    wire [FIFO_AW:0] fifo_cnt  = wptr - rptr;
    wire             fifo_full = (fifo_cnt == (1 << FIFO_AW));
    wire             fifo_wr   = out_vld && !fifo_full;

    always @(posedge clk) begin
        if (fifo_wr) fifo_mem[wptr[FIFO_AW-1:0]] <= out_q;
    end

    /* 读侧组合出数：rptr 只在握手成立时前进，所以 tvalid 高、tready 低期间
     * tdata 保持不变（AXI-Stream 的要求）。 */
    wire [15:0] fifo_q = fifo_mem[rptr[FIFO_AW-1:0]];

    assign m_axis_tvalid = (fifo_cnt != 0);
    assign m_axis_tdata  = {{16{fifo_q[15]}}, fifo_q};   // 符号扩展到 32 位，PS 侧按 int32 读

    /* ================= 状态 ================= */

    localparam [0:0] S_RST = 1'b0,     // 前端复位中，不收输入
                     S_RUN = 1'b1;     // 正常流

    reg  [0:0]  st;
    reg  [3:0]  rst_cnt;
    reg  [15:0] n_in;                  // 已接收的输入个数
    reg  [15:0] n_exp;                 // 本块应有的输出个数（输入 TLAST 时锁存）
    reg  [15:0] n_out;                 // 已发出的输出个数
    reg  [31:0] cyc;
    reg  [31:0] t0;
    reg         t0_vld;
    reg  [31:0] total_cycles;
    reg         done, busy, overflow;

    assign stat_lo = total_cycles;
    assign stat_hi = {13'd0, overflow, busy, done, n_out};

    /* 输出端的 TLAST 看计数，不看 FIFO 内容 —— n_exp 在最后一个输入
     * 到达时就已知，而对应的输出还要 8 拍才出来，来得及。 */
    assign m_axis_tlast = (n_exp != 16'd0) && ((n_out + 16'd1) == n_exp);

    wire out_fire = m_axis_tvalid && m_axis_tready;

    /* ================= 输入侧 ================= */

    wire fifo_room = (fifo_cnt < FIFO_LIM);

    assign f_din         = {{16{s_axis_tdata[15]}}, s_axis_tdata[15:0]};
    assign s_axis_tready = (st == S_RUN) && f_din_ready && fifo_room;
    assign f_din_vld     = s_axis_tvalid && s_axis_tready;

    wire in_fire = f_din_vld;

    /* ================= 指针 ================= */

    always @(posedge clk) begin
        if (!rst_n) begin
            wptr <= {(FIFO_AW+1){1'b0}};
            rptr <= {(FIFO_AW+1){1'b0}};
        end else begin
            if (fifo_wr)  wptr <= wptr + 1'b1;
            if (out_fire) rptr <= rptr + 1'b1;
        end
    end

    /* ================= 主状态机 ================= */

    always @(posedge clk) begin
        if (!rst_n) begin
            st           <= S_RST;
            rst_cnt      <= RST_CYC;
            fe_rst       <= 1'b1;
            drc_en_r     <= 1'b0;
            n_in         <= 16'd0;
            n_exp        <= 16'd0;
            n_out        <= 16'd0;
            cyc          <= 32'd0;
            t0           <= 32'd0;
            t0_vld       <= 1'b0;
            total_cycles <= 32'd0;
            done         <= 1'b0;
            busy         <= 1'b0;
            overflow     <= 1'b0;
        end else begin
            cyc <= cyc + 32'd1;

            /* ---- 输出计数 + 收尾 ---- */
            if (out_fire) begin
                n_out <= n_out + 16'd1;
                if (n_exp != 16'd0 && (n_out + 16'd1) == n_exp) begin
                    done         <= 1'b1;
                    busy         <= 1'b0;
                    total_cycles <= (cyc + 32'd1) - t0;
                    st           <= S_RST;          // 为下一块清前端
                    rst_cnt      <= RST_CYC;
                end
            end

            /* ---- 输入计数 ---- */
            if (in_fire) begin
                n_in <= n_in + 16'd1;
                if (s_axis_tlast) n_exp <= n_in + 16'd1;
            end

            /* ---- 计时起点：第一个采样被接收 ---- */
            if (in_fire && !t0_vld) begin
                t0     <= cyc;
                t0_vld <= 1'b1;
                done   <= 1'b0;          // done 一直保持到下一块真的开始，
                overflow <= 1'b0;        // 否则 PS 走 MMIO/DMA 轮询一定错过它
                n_out  <= 16'd0;         // 同理：n_out 也要留到下一块开始，
                                         // 不能在上面的 S_RST 里清 —— 一块跑完到
                                         // PS 读状态之间隔着几微秒，清了就读成 0
                /* drc_en 在这里锁存，不在 S_RST→S_RUN 那一拍：DMA 版上电后
                 * 外壳一直在 S_RUN 等着，PS 是加载完位流才写 ctrl 的，
                 * 在 S_RUN 入口锁存会拿到上电时的 0。 */
                drc_en_r <= ctrl[0];
            end

            /* ---- FIFO 满还来数据 = overflow（正常不该发生） ---- */
            if (out_vld && fifo_full) overflow <= 1'b1;

            if (st == S_RST) begin
                fe_rst <= 1'b1;
                if (rst_cnt == 4'd0) begin
                    fe_rst   <= 1'b0;
                    st       <= S_RUN;
                    busy     <= 1'b1;
                    n_in     <= 16'd0;
                    n_exp    <= 16'd0;
                    t0_vld   <= 1'b0;
                    drc_en_r <= ctrl[0];
                end else begin
                    rst_cnt <= rst_cnt - 4'd1;
                end
            end
        end
    end

endmodule
