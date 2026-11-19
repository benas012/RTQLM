`timescale 1ns/1ps
module tb_e1_tag;
    logic clk=0, rstn=0, e=0;
    logic [13:0] a=0,b=0;
    logic [31:0] control=32'h00000501;
    wire [15:0] wa,wb;
    rp_e1_tag dut(clk,rstn,e,a,b,control,wa,wb);
    always #4 clk=~clk;
    logic [129:0] expected_e='0;
    integer cycles=0;
    always @(posedge clk) begin
        if (!rstn) expected_e <= '0;
        else expected_e <= {expected_e[128:0],e};
    end
    initial begin
        repeat(4) @(negedge clk);
        rstn=1;
        for(integer n=0;n<20000;n=n+1) begin
            e=$urandom_range(0,1);
            a=$urandom;
            b=$urandom;
            if(n==4000) control=32'h00000001;
            if(n==8000) control=32'h00007f01;
            if(n==12000) control=32'h00000503;
            @(negedge clk);
            if(wa[15]!==1'b1 || wb[15]!==1'b1) $fatal(1,"Missing record marker");
            if(wa[14]!==wb[14]) $fatal(1,"Different rail E1 tags");
            if(control[1]) begin
                if(wb[13:0] !== ~wa[13:0]) $fatal(1,"Diagnostic rail mismatch");
                if(wa[14] !== wa[5]) $fatal(1,"Diagnostic sample/tag mismatch");
            end else begin
                if(wa[13:0]!==a || wb[13:0]!==b) $fatal(1,"ADC bits changed");
                if(wa[14]!==expected_e[control[14:8]+2]) $fatal(1,"E1 history alignment changed");
            end
            cycles=cycles+1;
        end
        $display("PASS: %0d random and diagnostic sample pairs; taps 0,5,127; marker, ADC preservation, and common E1 tags",cycles);
        $finish;
    end
endmodule
