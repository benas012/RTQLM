`timescale 1ns/1ps
// RTQLM: atomic {ADC14, E1} records, on the ADC clock. No software timestamps.
// Word bits: [13:0] signed ADC counts, [14] E1, [15] format marker (1).
// Control: bit 0 enable (handled by scope); bit 1 diagnostic pattern;
// bits [14:8] extra GPIO history cycles. Default tap 5 gives seven clock
// edges from the input sample through meta/sync/history. Physical ADC/input
// path skew must be measured separately before claiming aperture alignment.
module rp_e1_tag (
    input  logic        clk,
    input  logic        rstn,
    input  logic        e1_i,
    input  logic [13:0] adc_a_i,
    input  logic [13:0] adc_b_i,
    input  logic [31:0] control_i,
    output logic [15:0] word_a_o,
    output logic [15:0] word_b_o
);
    (* ASYNC_REG = "TRUE" *) logic e1_meta, e1_sync;
    logic [127:0] history;
    logic [13:0] counter;
    always_ff @(posedge clk) begin
        if (!rstn) begin
            e1_meta <= 0;
            e1_sync <= 0;
            history <= '0;
            counter <= '0;
        end else begin
            e1_meta <= e1_i;
            e1_sync <= e1_meta;
            history <= {history[126:0], e1_sync};
            counter <= counter + 1'b1;
        end
    end
    wire e1 = control_i[1] ? counter[5] : history[control_i[14:8]];
    assign word_a_o = {1'b1, e1, control_i[1] ? counter : adc_a_i};
    assign word_b_o = {1'b1, e1, control_i[1] ? ~counter : adc_b_i};
endmodule
