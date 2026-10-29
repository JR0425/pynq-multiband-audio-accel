/* mux2 —— 1 位二选一，用来在 BD 里切换 sdata_o 的来源 */
`timescale 1ns / 1ps
module mux2 (
    input  wire s,   /* 0 -> a, 1 -> b */
    input  wire a,
    input  wire b,
    output wire y
);
    assign y = s ? b : a;
endmodule
