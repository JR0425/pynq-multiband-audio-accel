/* i2s_duplex —— 自造 I2S 主端 + AXI-Stream 适配
 *
 * 为什么要自己造：出货用的 audio_codec_ctrl 只有 AXI-Lite，每个采样都要 PS
 * 读写一次寄存器；duplex_block 的块长下限 360（7.5 ms）就是被这个逐点握手
 * 卡住的（见 performance_profile_20261022.md §1.5）。这里有流端口，
 * axi_dma 一条命令搬一整块，PS 不再逐点碰数据。
 *
 * 时钟：100 MHz 分频，和官方 IP 逐拍一致
 *      BCLK  = cnt[4]  → 3.125 MHz
 *      LRCLK = cnt[10] → 48.828 kHz
 * 两个都先打一拍再输出，和 user_logic.vhd 的 sclk_int/lrclk_int 同相，
 * 所以 codec 看到的时序和出货版一样，I2C 配置序列一个字不用改。
 *
 * 收发状态机逐行照搬 iis_deser.vhd / iis_ser.vhd：
 *   收：LRCLK 跳变后**跳过第一个 BCLK 上升沿**，再移入 24 位（左对齐、MSB 在前）
 *   发：BCLK 下降沿移出，数到 25 位才换声道（第 25 位固定补 0）
 * 这两处是 codec 眼里的格式本身，改一个字都等于换协议。
 *
 * 流格式：交错立体声，一个采样一个 32 位字（低 24 位，符号扩展）
 * 和 audio_stream.cpp 的 duplex_block 一致。
 *
 * 寄存器
 *   ctrl   [0] run      =1 才把 FIFO 里的数据播出去（=0 时输出静音、不消耗 FIFO）
 *          [1] hw_loop  =1 时把收到的流直接灌进 TX FIFO（不经过 DMA）——
 *                        bring-up 第一步就用它验 I2S 通不通，和 DMA 无关
 *          [2] flush    =1 时两侧 FIFO 一起清空（换阶段/重跑前先脉冲一下，
 *                       否则上一次残留的采样会先放出来）
 *          [8] clr      =1 时两个错误计数器清零（电平，写 0 恢复计数）
 *          [31:16] blk_words = 一个 DMA 传输块的字数 - 1。录音方向每收满
 *                       这么多字就拉一次 TLAST，PS 那边 S2MM_LENGTH 要写
 *                       同一个值（字数 × 4）。
 *
 * 为什么要 TLAST：AXI DMA 的 S2MM 靠 TLAST 认定一个"包"的结尾。不给的话
 * 它会把整条流当成一个永远不结束的包，缓冲区一填满就报 DMAIntErr
 * ——数据其实已经搬进去了，2026-10-25 就是在这里卡了两次。
 *
 * FIFO 深度为什么两侧不一样（2026-10-25 实测定下来的）：
 *   录音侧 4096 字（43 ms）。60 秒回环实测 PS 偶尔卡约 10 ms，1024 字
 *   （10.7 ms）会被顶满丢采样，4096 能吸收掉。
 *   播放侧 1024 字（10.7 ms）。**不能用 4096**：MM2S 是"能塞就塞"，水位
 *   会爬到很高（实测 4096 字那版 tx_lvl 恒为 4086，光这一项就是 42 ms）。
 *   试过缩到 512 字：播放积压从 10.5 掉到 5.2 ms，但录音侧同时反弹到
 *   8.3 ms，总延迟一点没变（18.9 ms vs 18.8 ms）—— 那时候缺的是水位控制，
 *   不是浅 FIFO。留着 1024 是因为水位要摆动的余地在它里面。
 *
 * 延迟公式（2026-10-25 深夜板上实测更正）：
 *   嘴到耳 ≈ 一个块周期（采样等自己这一块填满，和在块里排第几前后抵消）
 *          + 录音 FIFO 里排在前面的字 / (2·FS)
 *          + PS 交接（换格式、写 DMA 寄存器、flush 缓存，约 0.7 ms）
 *          + 交出去那一刻播放 FIFO 里排在前面的字 / (2·FS)
 *          + 声学 1.23 ms
 *
 *   两个 FIFO 的水位都是**中性平衡**：每块搬走的和送进来的字数一样，水位
 *   进来多少出去多少，开机时是多少整段就还是多少 —— 不会自己排掉，也不会
 *   自己涨回去。所以：
 *     · 起跑时要把录音 FIFO 里那点积压用一次短传输排空（板上脚本做，
 *       RTL 不管），否则每次开机背的积压都不一样（实测 0.1～8.3 ms）。
 *     · 播放水位要**控制**：交下一块之前等它降到一块（480 字）以内。
 *       不控制的话它会随机漂，60 秒里漏过 275 次断流。
 *   播放 FIFO 的**深度**不是延迟，水位才是；深度只是水位能摆动的范围。
 *
 *   实测（240 帧 + 1024 播放 FIFO + 水位 480 + 起跑排空）：
 *   一块 4.9 + 录音 0.0 + PS 0.7 + 播放 3.3 + 声学 1.23 ≈ 10.1 ms，
 *   60 秒 12000 块 overrun=0 underrun=0。
 *   120 帧能到 8.0 ms，但 60 秒里断流 243 次（PS 余量不够），不用。
 *   stat0  [15:0]  capture overrun 计数：上一对还没送进 FIFO 就又来了一对
 *          [31:16] tx underrun 计数：LRCLK 边沿到了，手里凑不齐一对
 *   stat1  [15:0]  TX FIFO 占用（字）  [31:16] RX FIFO 占用（字）
 */
`timescale 1ns / 1ps

module i2s_duplex #(
    parameter integer CAP_AW = 12,      // 录音 FIFO 深度 = 2^CAP_AW 字
    parameter integer TX_AW  = 10       // 播放 FIFO 深度 = 2^TX_AW 字
) (
    input  wire        clk,             // 100 MHz (FCLK_CLK0)
    input  wire        rst_n,

    input  wire [31:0] ctrl,
    output wire [31:0] stat0,
    output wire [31:0] stat1,

    /* I2S  pads */
    output wire        bclk,
    output wire        lrclk,
    output wire        sdata_o,
    input  wire        sdata_i,

    /* 播放数据（来自 axi_dma MM2S） */
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXIS, TDATA_NUM_BYTES 4, HAS_TKEEP 0, HAS_TSTRB 0, HAS_TLAST 1, TUSER_WIDTH 0, TDEST_WIDTH 0, TID_WIDTH 0" *)
    input  wire [31:0] s_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *)
    input  wire        s_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *)
    output wire        s_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *)
    input  wire        s_axis_tlast,

    /* 录音数据（去 axi_dma S2MM） */
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

    /* ================= 时钟分频 ================= */

    reg [10:0] clk_cntr;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) clk_cntr <= 11'd0;
        else        clk_cntr <= clk_cntr + 11'd1;
    end

    reg bclk_r, lrclk_r;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bclk_r  <= 1'b0;
            lrclk_r <= 1'b0;
        end else begin
            bclk_r  <= clk_cntr[4];
            lrclk_r <= clk_cntr[10];
        end
    end

    assign bclk  = bclk_r;
    assign lrclk = lrclk_r;

    /* ================= 边沿检测 ================= */

    reg bclk_d, lrclk_d;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bclk_d  <= 1'b0;
            lrclk_d <= 1'b0;
        end else begin
            bclk_d  <= bclk_r;
            lrclk_d <= lrclk_r;
        end
    end

    wire start_left  =  lrclk_d & ~lrclk_r;     // 左声道开始
    wire start_right = ~lrclk_d &  lrclk_r;     // 右声道开始
    wire bit_rdy     = ~bclk_d  &  bclk_r;      // BCLK 上升沿（收）
    wire write_bit   =  bclk_d  & ~bclk_r;      // BCLK 下降沿（发）

    /* ================= ctrl 位 ================= */

    wire run     = ctrl[0];
    wire hw_loop = ctrl[1];
    wire flush   = ctrl[2];     // 高电平期间两侧 FIFO 一直保持空
    wire [15:0] blk_words = ctrl[31:16];   // 一个传输块的字数 - 1


    /* ================= 接收（照 iis_deser.vhd） ================= */

    localparam [2:0] R_WAIT_L = 3'd0, R_SKIP_L = 3'd1, R_READ_L = 3'd2,
                     R_WAIT_R = 3'd3, R_SKIP_R = 3'd4, R_READ_R = 3'd5;

    reg [2:0]  rxs;
    reg [4:0]  rbit;            // 0..24，24 表示这一声道收满了
    reg [23:0] rl, rr;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) rxs <= R_WAIT_L;
        else begin
            case (rxs)
            R_WAIT_L: if (start_left)    rxs <= R_SKIP_L;
            R_SKIP_L: if (bit_rdy)       rxs <= R_READ_L;
            R_READ_L: if (rbit == 5'd24) rxs <= R_WAIT_R;
            R_WAIT_R: if (start_right)   rxs <= R_SKIP_R;
            R_SKIP_R: if (bit_rdy)       rxs <= R_READ_R;
            R_READ_R: if (rbit == 5'd24) rxs <= R_WAIT_L;
            default:                     rxs <= R_WAIT_L;
            endcase
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) rbit <= 5'd0;
        else if (rxs == R_READ_L || rxs == R_READ_R) begin
            if (bit_rdy) rbit <= rbit + 5'd1;
        end else rbit <= 5'd0;
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rl <= 24'd0;
            rr <= 24'd0;
        end else begin
            if (rxs == R_READ_L && bit_rdy) rl <= {rl[22:0], sdata_i};
            if (rxs == R_READ_R && bit_rdy) rr <= {rr[22:0], sdata_i};
        end
    end

    /* 一整帧收满的那一拍。此时 rbit=24、状态还是 READ_RIGHT，
     * 而 rl/rr 在上一个时钟沿已经装齐了 24 位。 */
    wire rx_valid = (rxs == R_READ_R) && (rbit == 5'd24);

    /* ================= 接收 → FIFO ================= */

    wire        cap_wr_en;
    wire [31:0] cap_wr_data;
    wire        cap_empty, cap_full;
    wire [CAP_AW:0] cap_level;
    wire [31:0] cap_dout;
    wire        cap_rd_en = m_axis_tvalid && m_axis_tready;

    reg [1:0]  cap_st;          // 0=闲 1=待写 L 2=待写 R
    reg [31:0] cq_l, cq_r;
    reg [15:0] ovf_cnt;

    /* cap_have：这一拍手里有一个字要处理
     * cap_wr_en：这个字真的进了 FIFO
     * 两者分开是因为 hw_loop 直接从 cap_have 取数：接收 FIFO 没人排空
     * （比如只看自环、DMA 还没挂）的时候它会满，但那时不能把自环也堵死。 */
    wire cap_have = (cap_st != 2'd0);

    assign cap_wr_en   = cap_have && !cap_full;
    assign cap_wr_data = (cap_st == 2'd1) ? cq_l : cq_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || flush) begin
            cap_st  <= 2'd0;
            cq_l    <= 32'd0;
            cq_r    <= 32'd0;
            ovf_cnt <= 16'd0;
        end else begin
            if (rx_valid) begin
                cq_l   <= {{8{rl[23]}}, rl};
                cq_r   <= {{8{rr[23]}}, rr};
                if (cap_have) ovf_cnt <= ovf_cnt + 16'd1;   // 上一对还没走完
                cap_st <= 2'd1;
            end else if (cap_have) begin
                if (cap_full) ovf_cnt <= ovf_cnt + 16'd1;   // 想写但 FIFO 满了
                cap_st <= (cap_st == 2'd1) ? 2'd2 : 2'd0;
            end

            if (ctrl[8]) ovf_cnt <= 16'd0;
        end
    end

    assign m_axis_tvalid = !cap_empty;
    assign m_axis_tdata  = cap_dout;

    /* 块尾标记：按"已经交给 DMA 的字数"数，而不是按写进 FIFO 的字数数，
     * 这样 TLAST 一定和对应那一拍的数据同拍。块长由 PS 经 ctrl[31:16] 给，
     * 两边用同一个值（S2MM_LENGTH = (blk_words+1)*4）。 */
    reg [15:0] cap_wcnt;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || flush) cap_wcnt <= 16'd0;
        else if (cap_rd_en)
            cap_wcnt <= (cap_wcnt == blk_words) ? 16'd0 : cap_wcnt + 16'd1;
    end
    assign m_axis_tlast = cap_rd_en && (cap_wcnt == blk_words);

    /* ================= 播放：AXIS → FIFO ================= */

    wire        tx_wr_en;
    wire [31:0] tx_wr_data;
    wire        tx_empty, tx_full;
    wire [TX_AW:0] tx_level;
    wire [31:0] tx_dout;
    wire        tx_rd_en;

    /* hw_loop：收到的流直接写进 TX FIFO（FPGA 内部自环，不碰 DDR）。
     * 这样断 DMA、断 PS 也能听到麦克风 → 喇叭，用来单独验 I2S。
     * 这里用 cap_have 而不是 cap_wr_en：接收 FIFO 满的时候自环不能跟着停。 */
    assign tx_wr_en   = hw_loop ? (cap_have && !tx_full)
                                : (s_axis_tvalid && s_axis_tready);
    assign tx_wr_data = hw_loop ? cap_wr_data : s_axis_tdata;
    assign s_axis_tready = !hw_loop && !tx_full;

    /* ================= 播放：FIFO → 成对取数 ================= */

    reg [31:0] hold0, hold1;
    reg [1:0]  hold_cnt;
    reg [15:0] und_cnt;

    /* 每次 LRCLK 边沿把一对交出去，所以要先攒够两个字。
     * start_left 那一拍不取数：那一拍正是把 hold0/hold1 拷进移位寄存器的拍，
     * 同一拍再改 hold 会拷错。 */
    assign tx_rd_en = (hold_cnt != 2'd2) && !tx_empty && !start_left;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || flush) begin
            hold0    <= 32'd0;
            hold1    <= 32'd0;
            hold_cnt <= 2'd0;
            und_cnt  <= 16'd0;
        end else begin
            if (start_left) begin
                hold_cnt <= 2'd0;
                if (run && hold_cnt != 2'd2) und_cnt <= und_cnt + 16'd1;
            end else if (tx_rd_en) begin
                if (hold_cnt == 2'd0) begin
                    hold0    <= tx_dout;
                    hold_cnt <= 2'd1;
                end else begin
                    hold1    <= tx_dout;
                    hold_cnt <= 2'd2;
                end
            end

            if (ctrl[8]) und_cnt <= 16'd0;
        end
    end

    /* ================= 发送（照 iis_ser.vhd） ================= */

    localparam [2:0] T_WAIT_L = 3'd0, T_WRITE_L = 3'd1,
                     T_WAIT_R = 3'd2, T_WRITE_R = 3'd3;

    reg [2:0]  txs;
    reg [4:0]  tbit;            // 0..25，25 表示这一声道发完了
    reg [23:0] tl, tr;
    reg        sdata_r;

    wire have_pair = run && (hold_cnt == 2'd2);
    wire [23:0] next_l = have_pair ? hold0[23:0] : 24'd0;
    wire [23:0] next_r = have_pair ? hold1[23:0] : 24'd0;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) txs <= T_WAIT_L;
        else begin
            case (txs)
            T_WAIT_L:  if (start_left)   txs <= T_WRITE_L;
            T_WRITE_L: if (tbit == 5'd25) txs <= T_WAIT_R;
            T_WAIT_R:  if (start_right)  txs <= T_WRITE_R;
            T_WRITE_R: if (tbit == 5'd25) txs <= T_WAIT_L;
            default:                     txs <= T_WAIT_L;
            endcase
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) tbit <= 5'd0;
        else if (txs == T_WRITE_L || txs == T_WRITE_R) begin
            if (write_bit) tbit <= tbit + 5'd1;
        end else tbit <= 5'd0;
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            tl <= 24'd0;
            tr <= 24'd0;
        end else if (txs == T_WAIT_L && start_left) begin
            /* 装载和移位互斥：这一拍 txs 还是 WAIT_L，不会同时走移位分支 */
            tl <= next_l;
            tr <= next_r;
        end else begin
            if (txs == T_WRITE_L && write_bit) tl <= {tl[22:0], 1'b0};
            if (txs == T_WRITE_R && write_bit) tr <= {tr[22:0], 1'b0};
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) sdata_r <= 1'b0;
        else if (txs == T_WRITE_L && write_bit) sdata_r <= tl[23];
        else if (txs == T_WRITE_R && write_bit) sdata_r <= tr[23];
    end

    assign sdata_o = sdata_r;

    /* ================= FIFO ================= */

    axis_fifo #(.AW(CAP_AW), .DW(32)) u_cap_fifo (
        .clk   (clk),
        .rst_n (rst_n),
        .wr_en (cap_wr_en),
        .din   (cap_wr_data),
        .rd_en (cap_rd_en),
        .flush (flush),
        .dout  (cap_dout),
        .empty (cap_empty),
        .full  (cap_full),
        .level (cap_level)
    );

    axis_fifo #(.AW(TX_AW), .DW(32)) u_tx_fifo (
        .clk   (clk),
        .rst_n (rst_n),
        .wr_en (tx_wr_en),
        .din   (tx_wr_data),
        .rd_en (tx_rd_en),
        .flush (flush),
        .dout  (tx_dout),
        .empty (tx_empty),
        .full  (tx_full),
        .level (tx_level)
    );

    /* ================= 状态 ================= */

    assign stat0 = {und_cnt, ovf_cnt};
    /* 两个字段各自零扩展到 16 位：CAP_AW=12 → 13 位，TX_AW=10 → 11 位 */
    assign stat1 = {{(16-(CAP_AW+1)){1'b0}}, cap_level,
                    {(16-(TX_AW+1)){1'b0}},  tx_level};

endmodule
