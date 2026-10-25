/* i2s_duplex_dbg —— 只为定位"送出去了但 codec 没声音"而做的带仪表版本
 *
 * 和 i2s_duplex 完全同一个核，外面加三件仪器，全部只读、通过 AXI GPIO 读回：
 *
 *   1. 内置音源（ctrl[3]=1）：每帧往 S_AXIS 灌两个字，24 位满量程方的 ±2^21
 *      （约 -12 dBFS）440 Hz 方波。这样验声音完全不用话筒、不用 DMA、不用 PS。
 *   2. 自收：把 sdata_o **在 FPGA 内部**接回一个和 iis_deser 同构的接收器，
 *      解出来的左/右字直接可读。串行器和输出寄存器对不对，看这里就知道。
 *   3. 翻转计数：sdata_o 每变一次加一。计数不动 = 引脚上根本没波形。
 *
 * 为什么需要这个：仿真（连官方 iis_deser 逐字对拍）是过的、引脚分配和出货版
 * 逐脚一致、codec 寄存器被证实停在"亲耳听到过声音"的那一份上，但耳机里
 * 就是没声。这三件仪器是把"引脚上到底有没有波形"从推断变成读数。
 *
 * 寄存器（地址见 build_audio_i2sdbg.tcl）
 *   dbg0 = {自收左字[23:0], 帧计数[3:0], run, hw_loop, tone_en, sdata_o}
 *   dbg1 = {自收右字[23:0], 8'h00}
 *   dbg2 = {sdata_o 翻转计数[15:0], 喂进 S_AXIS 的字数[15:0]}
 */
`timescale 1ns / 1ps

module i2s_duplex_dbg #(
    parameter integer CAP_AW = 12,
    parameter integer TX_AW  = 10
) (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [31:0] ctrl,
    output wire [31:0] stat0,
    output wire [31:0] stat1,

    output wire        bclk,
    output wire        lrclk,
    output wire        sdata_o,
    input  wire        sdata_i,

    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXIS, TDATA_NUM_BYTES 4, HAS_TKEEP 0, HAS_TSTRB 0, HAS_TLAST 1, TUSER_WIDTH 0, TDEST_WIDTH 0, TID_WIDTH 0" *)
    input  wire [31:0] s_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *)
    input  wire        s_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *)
    output wire        s_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *)
    input  wire        s_axis_tlast,

    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_AXIS, TDATA_NUM_BYTES 4, HAS_TKEEP 0, HAS_TSTRB 0, HAS_TLAST 1, TUSER_WIDTH 0, TDEST_WIDTH 0, TID_WIDTH 0" *)
    output wire [31:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *)
    output wire        m_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *)
    input  wire        m_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *)
    output wire        m_axis_tlast,

    output wire [31:0] dbg0,
    output wire [31:0] dbg1,
    output wire [31:0] dbg2
);

    wire run     = ctrl[0];
    wire hw_loop = ctrl[1];
    wire tone_en = ctrl[3];

    /* ================= 内置音源 ================= */

    /* 每 2048 拍（一个立体声帧）出两个字。相位累加器只在帧首加一次：
     * 48828.125 Hz / 2^16 * 591 ≈ 440.5 Hz。方波取相位最高位。 */
    reg [10:0] fc;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) fc <= 11'd0;
        else        fc <= fc + 11'd1;
    end

    reg [15:0] ph;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)         ph <= 16'd0;
        else if (fc == 11'd0) ph <= ph + 16'd591;
    end

    /* i2s_duplex 取的是 hold[23:0]，所以幅度必须写在低 24 位。
     * ±2^21 / 2^23 = 1/4 满量程 = -12 dBFS。 */
    wire [23:0] tone24 = ph[15] ? 24'h200000 : 24'hE00000;
    wire [31:0] tone32 = {{8{tone24[23]}}, tone24};

    wire tone_vld = tone_en && (fc == 11'd0 || fc == 11'd1);

    wire [31:0] s_tdata = tone_en ? tone32 : s_axis_tdata;
    wire        s_tvld  = tone_en ? tone_vld : s_axis_tvalid;

    /* ================= 核本体 ================= */

    i2s_duplex #(.CAP_AW(CAP_AW), .TX_AW(TX_AW)) u_core (
        .clk (clk), .rst_n (rst_n), .ctrl (ctrl),
        .stat0 (stat0), .stat1 (stat1),
        .bclk (bclk), .lrclk (lrclk), .sdata_o (sdata_o), .sdata_i (sdata_i),
        .s_axis_tdata (s_tdata), .s_axis_tvalid (s_tvld),
        .s_axis_tready (s_axis_tready), .s_axis_tlast (s_axis_tlast),
        .m_axis_tdata (m_axis_tdata), .m_axis_tvalid (m_axis_tvalid),
        .m_axis_tready (m_axis_tready), .m_axis_tlast (m_axis_tlast)
    );

    /* ================= 自收：sdata_o → 和 iis_deser 同构的接收器 ================= */

    reg bc_d, lc_d;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin bc_d <= 1'b0; lc_d <= 1'b0; end
        else        begin bc_d <= bclk; lc_d <= lrclk; end
    end

    wire p_left  =  lc_d & ~lrclk;
    wire p_right = ~lc_d &  lrclk;
    wire p_bit   = ~bc_d &  bclk;

    localparam [2:0] P_WAIT_L = 3'd0, P_SKIP_L = 3'd1, P_READ_L = 3'd2,
                     P_WAIT_R = 3'd3, P_SKIP_R = 3'd4, P_READ_R = 3'd5;

    reg [2:0]  pxs;
    reg [4:0]  pbit;
    reg [23:0] pl, pr;
    reg [3:0]  pframe;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) pxs <= P_WAIT_L;
        else case (pxs)
            P_WAIT_L: if (p_left)      pxs <= P_SKIP_L;
            P_SKIP_L: if (p_bit)       pxs <= P_READ_L;
            P_READ_L: if (pbit == 5'd24) pxs <= P_WAIT_R;
            P_WAIT_R: if (p_right)     pxs <= P_SKIP_R;
            P_SKIP_R: if (p_bit)       pxs <= P_READ_R;
            P_READ_R: if (pbit == 5'd24) pxs <= P_WAIT_L;
            default:                   pxs <= P_WAIT_L;
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) pbit <= 5'd0;
        else if (pxs == P_READ_L || pxs == P_READ_R) begin
            if (p_bit) pbit <= pbit + 5'd1;
        end else pbit <= 5'd0;
    end

    /* 只移前 24 位。iis_ser 在第 25 个下降沿会多送一个 0，
     * 那一位不能移进来，否则整个字错一位。 */
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin pl <= 24'd0; pr <= 24'd0; end
        else begin
            if (pxs == P_READ_L && p_bit && pbit != 5'd24) pl <= {pl[22:0], sdata_o};
            if (pxs == P_READ_R && p_bit && pbit != 5'd24) pr <= {pr[22:0], sdata_o};
        end
    end

    /* pbit==24 会一直保持到下一个上升沿，所以必须再用 p_bit 限定成一拍，
     * 否则一个帧会被记成十几次。 */
    wire p_frame_done = (pxs == P_READ_R) && (pbit == 5'd24) && p_bit;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) pframe <= 4'd0;
        else if (p_frame_done) pframe <= pframe + 4'd1;
    end

    /* ================= 翻转计数 / 喂入字数 ================= */

    reg sd_d;
    reg [15:0] tog;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin sd_d <= 1'b0; tog <= 16'd0; end
        else begin
            sd_d <= sdata_o;
            if (sdata_o != sd_d) tog <= tog + 16'd1;
        end
    end

    reg [15:0] wr_words;
    wire s_fire = s_tvld && s_axis_tready;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n || ctrl[8]) wr_words <= 16'd0;
        else if (s_fire)       wr_words <= wr_words + 16'd1;
    end

    assign dbg0 = {pl, pframe, run, hw_loop, tone_en, sdata_o};
    assign dbg1 = {pr, 8'h00};
    assign dbg2 = {tog, wr_words};

endmodule
