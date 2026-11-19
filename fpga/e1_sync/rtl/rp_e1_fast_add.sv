`timescale 1ns/1ps
// Carry-select blocks with short carry lookahead for the extended ASG pointer.
module rp_e1_fast_add #(parameter int W=63) (
    input wire [W-1:0] a, b,
    output wire [W-1:0] sum
);
    localparam int N=(W+15)/16;
    localparam int PW=N*16;
    wire [PW-1:0] aa={{(PW-W){1'b0}},a};
    wire [PW-1:0] bb={{(PW-W){1'b0}},b};
    wire [PW-1:0] result;
    wire [N:0] carry;
    assign carry[0]=1'b0;
    for (genvar i=0;i<N;i++) begin: block_add
        wire [16:0] s0={1'b0,aa[i*16+:16]}+{1'b0,bb[i*16+:16]};
        wire [15:0] s1=aa[i*16+:16]+bb[i*16+:16]+16'd1;
        wire propagate=&(aa[i*16+:16]^bb[i*16+:16]);
        assign carry[i+1]=s0[16] | (propagate & carry[i]);
        assign result[i*16+:16]=carry[i] ? s1 : s0[15:0];
    end
    assign sum=result[W-1:0];
endmodule
