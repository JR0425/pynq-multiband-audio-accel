/* fir_axis_monitor —— AXI-Stream 直通 + 调试引出（只给抓波形图那版位流用）
 *
 * 为什么要有这么个东西：
 *   BD 里两条流是用接口对接口连的，接口成员引脚已经被占住了，
 *   抓取器（fir_wave_cap）的输入不能直接挂到 `fir_stream_shell_0/s_axis_tvalid` 上。
 *   在数据通路上串一个直通模块，由它把 tvalid/tready/tlast/tdata
 *   另外引出成**普通输出端口**，抓取器按普通引脚接过去。
 *
 *   代价是两条组合路径（tdata 前向、tready 反向）各多一级走线。
 *   所以这一版位流**只用来抓波形图**，报告里的拍数和吞吐一律用
 *   不带抓取器的 fir_rtl_dma.bit 量（PLAN §11.14 §4 的拔高档口径）。
 */

`timescale 1ns / 1ps

module fir_axis_monitor (
    input  wire        clk,
    input  wire        rst_n,

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

    /* 调试引出：普通标量端口，BD 里用 connect_bd_net 直接接 ILA 的 probe */
    output wire        dbg_tvalid,
    output wire        dbg_tready,
    output wire        dbg_tlast,
    output wire [15:0] dbg_tdata
);

    assign m_axis_tdata  = s_axis_tdata;
    assign m_axis_tvalid = s_axis_tvalid;
    assign m_axis_tlast  = s_axis_tlast;
    assign s_axis_tready = m_axis_tready;

    assign dbg_tvalid = s_axis_tvalid;
    assign dbg_tready = s_axis_tready;
    assign dbg_tlast  = s_axis_tlast;
    assign dbg_tdata  = s_axis_tdata[15:0];

endmodule
