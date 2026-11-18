"""Compare the complete modified generator against the pinned original RTL."""
import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, default=ROOT / "upstream")
parser.add_argument("--output", type=Path, default=ROOT / "sim/generated_asg_equivalence.sv")
args = parser.parse_args()
SRC = args.source.resolve()
name = "rtl/rtl/classic/red_pitaya_asg_ch.sv"
original = subprocess.check_output(["git", "show", "HEAD:" + name], cwd=SRC, text=True)
header = original.split(");", 1)[0]
ports = re.findall(r"^\s*(input|output)\s*(?:reg\s+)?(\[[^\]]+\])?\s*(\w+)\s*[, ]", header, re.MULTILINE)
declarations = []
connections = [[], []]
outputs = []
for direction, width, port in ports:
    if direction == "input":
        declarations.append(f"logic {width} {port}='0;")
        for variant in range(2):
            connections[variant].append(f".{port}({port})")
    else:
        declarations.append(f"wire {width} {port}[0:1];")
        outputs.append(port)
        for variant in range(2):
            connections[variant].append(f".{port}({port}[{variant}])")
for variant in range(2):
    connections[variant].append(f".axi_sys(axi{variant})")
tb = "module tb_asg_equivalence;\nlocalparam RSZ=14;\n" + "\n".join(declarations)
tb += "\naxi_sys_if axi0(dac_clk_i,dac_rstn_i),axi1(dac_clk_i,dac_rstn_i);\n"
tb += "always #4 dac_clk_i=~dac_clk_i;\n"
tb += "red_pitaya_asg_ch_gold gold(" + ",".join(connections[0]) + ");\n"
tb += "red_pitaya_asg_ch test(" + ",".join(connections[1]) + ");\n"
tb += """
integer checked=0,nonzero=0;
initial begin
    trig_src_i=1;set_amp_i=8191;set_wrap_i=1;
    set_size_i=30'h3fffffff;set_step_i=32'h01234567;set_step_lo_i=32'h89abcdef;
    set_seed_i=32'hdeadbeef;set_rdly_i=2000;
    repeat(5) @(negedge dac_clk_i);
    dac_rstn_i=1;set_rst_i=1;buf_we_i=1;
    for(int n=0;n<16384;n++) begin
        buf_addr_i=n;buf_wdata_i=(n*37)^14'h25a5;
        @(negedge dac_clk_i);
    end
    buf_we_i=0;set_rst_i=0;
    repeat(8) @(negedge dac_clk_i);
    for(int n=0;n<100000;n++) begin
        trig_sw_i=(n%1000==5);
        if(n%1000==0) begin
            set_rst_i=1;
            set_step_i=$urandom;set_step_lo_i=$urandom;
            set_rdly_i=(n%3000==0)?0:$urandom_range(1,3000);
            set_ncyc_i=(n%2000==0)?0:$urandom_range(1,8);
            set_rnum_i=(n%2000==0)?0:$urandom_range(1,10);
            set_ofs_i=$urandom;
            set_wrap_i=$urandom_range(0,1);
        end
        if(n%1000==2) set_rst_i=0;
        @(negedge dac_clk_i);
"""
for port in outputs:
    tb += f'if({port}[0] !== {port}[1]) $fatal(1,"Generator mismatch: {port}");\n'
tb += """
        if(gold.dac_pnt !== test.dac_pnt || gold.dly_cnt !== test.dly_cnt)
            $fatal(1,"Pointer or delay state changed");
        if($isunknown(dac_o[1]) || $isunknown(test.dac_pnt))
            $fatal(1,"Unknown generator data: vacuous comparison");
        if(dac_o[1]!=0) nonzero++;
        checked++;
    end
    if(nonzero<1000) $fatal(1,"Generator was not exercised");
    $display("PASS: %0d full-generator cycles match original RTL; %0d nonzero output samples; fractional frequency, bursts, delay and wrap exercised",checked,nonzero);
    $finish;
end
endmodule
"""
files = ["rtl/rtl/interface/axi_sys_if.sv", "rtl/rtl/rand_lfsr.v",
         "rtl/rtl/classic/rp_asg_axi.sv", "prj/v0.94/rtl/rp_e1_fast_add.sv"]
text = "`timescale 1ns/1ps\n`define RTQLM_E1_SYNC\n"
text += "\n".join((SRC / path).read_text() for path in files)
def simulator_declarations(source):
    # Vivado synthesis accepts the upstream forward register declaration;
    # xvlog requires it before first use. Only declaration order changes.
    source = source.replace("RDLY_MODE_CONST  = 2'b00", "RDLY_MODE_CONST  = 1'b0")
    source = source.replace("RDLY_MODE_COPY  = 2'b01", "RDLY_MODE_COPY  = 1'b1")
    pattern = r"^\s*((?:reg|wire)\s+\w+\s*;)"
    declarations = re.findall(pattern, source, flags=re.MULTILINE)
    source = re.sub(pattern, "", source, flags=re.MULTILINE)
    position = source.index(");") + 2
    return source[:position] + "\n" + "\n".join(declarations) + "\n" + source[position:]

text += "\n" + simulator_declarations(original.replace("module red_pitaya_asg_ch", "module red_pitaya_asg_ch_gold", 1))
text += "\n" + simulator_declarations((SRC / name).read_text()) + "\n" + tb
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(text)
print("Prepared full original/modified generator equivalence simulation")
