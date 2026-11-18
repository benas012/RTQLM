`timescale 1ns/1ps
module tb_fast_add;
    logic [62:0] a,b,c;
    wire [62:0] s,wrap;
    wire [62:0] cs=a^b^c;
    wire [62:0] cc=((a&b)|(a&c)|(b&c))<<1;
    rp_e1_fast_add #(.W(63)) simple_add(a,b,s);
    rp_e1_fast_add #(.W(63)) wrap_add(cs,cc,wrap);
    initial begin
        for(int i=0;i<50000;i++) begin
            a={$urandom,$urandom}; b={$urandom,$urandom}; c={$urandom,$urandom};
            if(i==0) begin a='1;b=1;c=1;end
            if(i==1) begin a='1;b='1;c='1;end
            if(i==2) begin a=0;b=0;c=0;end
            #1;
            if(s !== ((a+b)&63'h7fffffffffffffff)) $fatal(1,"Two-operand addition changed");
            if(wrap !== ((a+b+c)&63'h7fffffffffffffff)) $fatal(1,"Carry-save wrap arithmetic changed");
        end
        $display("PASS: 50000 random/overflow 63-bit additions and three-operand ASG wrap sums");
        $finish;
    end
endmodule
