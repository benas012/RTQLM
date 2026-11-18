"""Apply the minimal E1 sideband changes to pinned Release_2026.1 sources."""
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, default=ROOT / "upstream",
                    help="Separate pinned source checkout to prepare")
SOURCE = parser.parse_args().source.resolve()
revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True).strip()
if revision != "c3fc4f6ebebf9b24f008ecb606eb7a3a23f62422":
    raise RuntimeError("FPGA source is not the pinned Release_2026.1 commit")

# Keep the new module outside the disposable upstream checkout too.
for name in ("rp_e1_tag.sv", "rp_e1_fast_add.sv"):
    (SOURCE / "prj/v0.94/rtl" / name).write_bytes((ROOT / "rtl" / name).read_bytes())


def edit(path, old, new):
    p = SOURCE / path
    text = p.read_text()
    if new in text:
        return
    if text.count(old) != 1:
        raise RuntimeError(f"Unexpected upstream source: {path}: {old[:80]!r}")
    p.write_text(text.replace(old, new), newline="\n")


edit("rtl/rtl/classic/red_pitaya_hk.v",
     "  // LED\n", "  // RTQLM E1 capture configuration\n"
     "`ifdef RTQLM_E1_SYNC\n  output reg [31:0] e1_control_o,\n`endif\n  // LED\n")
edit("rtl/rtl/classic/red_pitaya_hk.v",
     "  digital_loop <= 2'h0;", "`ifdef RTQLM_E1_SYNC\n  e1_control_o <= 32'h00000500;\n`endif\n  digital_loop <= 2'h0;")
edit("rtl/rtl/classic/red_pitaya_hk.v",
     "end else if (sys_wen) begin", "end else if (sys_wen) begin\n`ifdef RTQLM_E1_SYNC\n"
     "  if (sys_addr[19:0]==20'h00114) e1_control_o <= sys_wdata;\n`endif")
edit("rtl/rtl/classic/red_pitaya_hk.v",
     "  casez (sys_addr[19:0])", "  casez (sys_addr[19:0])\n`ifdef RTQLM_E1_SYNC\n"
     "    20'h00110: begin sys_ack <= sys_en; sys_rdata <= 32'h45513101; end\n"
     "    20'h00114: begin sys_ack <= sys_en; sys_rdata <= e1_control_o; end\n`endif")

top = "prj/v0.94/rtl/red_pitaya_top.sv"
edit(top, "logic          trig_asg_out;", "logic          trig_asg_out;\n"
     "`ifdef RTQLM_E1_SYNC\nlogic [31:0] e1_control;\nlogic [15:0] e1_word_a, e1_word_b;\n`endif")
edit(top, "  // LED\n  .led_o", "`ifdef RTQLM_E1_SYNC\n  .e1_control_o(e1_control),\n`endif\n  // LED\n  .led_o")
edit(top, "localparam int unsigned SCOPE_DW = 16;",
     "`ifdef RTQLM_E1_SYNC\n"
     "rp_e1_tag e1_tag (.clk(adc_clk), .rstn(adc_rstn), .e1_i(exp_p_in[0]),\n"
     "  .adc_a_i(adc_dat[0][13:0]), .adc_b_i(adc_dat[1][13:0]),\n"
     "  .control_i(e1_control), .word_a_o(e1_word_a), .word_b_o(e1_word_b));\n"
     "`endif\nlocalparam int unsigned SCOPE_DW = 16;")
edit(top, "  .adc_dat_i     ({adc_scope_dat[1], adc_scope_dat[0]}  ),",
     "`ifdef RTQLM_E1_SYNC\n  .e1_enable_i(e1_control[0]),\n"
     "  .e1_tagged_dat_i({e1_word_b, e1_word_a}),\n`endif\n"
     "  .adc_dat_i     ({adc_scope_dat[1], adc_scope_dat[0]}  ),")

scope = "rtl/rtl/classic/rp_scope_com.v"
edit(scope, "   input      [N_CH*DW-1: 0] adc_dat_i",
     "`ifdef RTQLM_E1_SYNC\n   input e1_enable_i,\n"
     "   input [N_CH*DW-1:0] e1_tagged_dat_i,\n`endif\n"
     "   input      [N_CH*DW-1: 0] adc_dat_i")
edit(scope, "rp_delay #(\n", "`ifdef RTQLM_E1_SYNC\n"
     "// Tagged raw samples bypass calibration/filter/decimation as a unit.\n"
     "// Both ADC rails and E1 enter the SAME existing delay/FIFO/DMA path.\n"
     "wire [DW-1:0] e1_dly_dat = e1_enable_i ? e1_tagged_dat_i[GV*DW +: DW] : adc_dly_in;\n"
     "wire e1_dly_valid = e1_enable_i ? 1'b1 : dec_val_65;\n`endif\nrp_delay #(\n")
edit(scope, "  .dly_dat_i     ( adc_dly_in                     ),\n  .dly_val_i     ( dec_val_65                        ),",
     "`ifdef RTQLM_E1_SYNC\n  .dly_dat_i(e1_dly_dat),\n  .dly_val_i(e1_dly_valid),\n`else\n"
     "  .dly_dat_i     ( adc_dly_in                     ),\n  .dly_val_i     ( dec_val_65                        ),\n`endif")

# Latest upstream DAC DDR streaming fails 250 MHz timing on the Zynq 7010.
# This acquisition-only image retains the BRAM waveform generators and keeps
# unused DAC AXI masters idle, removing those paths rather than masking timing.
asg = "rtl/rtl/classic/rp_asg_axi.sv"
edit(asg, "localparam DW = 64;", "`ifdef RTQLM_E1_SYNC\n"
     "// Capture image: DAC DDR streaming is unavailable; BRAM generators remain.\n"
     "assign dac_o = '0;\nassign axi_state_o = '0;\n"
     "assign axi_last_o = 1'b0;\nassign axi_last_pre_o = 1'b0;\nassign axi_first_o = 1'b0;\n"
     "assign axi_sys.waddr = '0;\nassign axi_sys.wdata = '0;\nassign axi_sys.wsel = '0;\n"
     "assign axi_sys.wvalid = 1'b0;\nassign axi_sys.wlen = '0;\n"
     "assign axi_sys.wsize = 3'd3;\nassign axi_sys.wfixed = 1'b0;\n"
     "assign axi_sys.raddr = '0;\nassign axi_sys.rsel = '0;\n"
     "assign axi_sys.rvalid = 1'b0;\nassign axi_sys.rlen = '0;\n"
     "assign axi_sys.rsize = 3'd3;\nassign axi_sys.rfixed = 1'b0;\n"
     "assign axi_sys.rrdys = 1'b0;\n`else\nlocalparam DW = 64;")
edit(asg, "\nendmodule", "\n`endif // RTQLM_E1_SYNC\nendmodule")

# Avoid serial 62-bit add/subtract carry chains in the upstream ASG. Compute
# the three-operand wrap sum in carry-save form, then use short carry-select
# adders. The arithmetic and waveform pointer precision are unchanged.
ch = "rtl/rtl/classic/red_pitaya_asg_ch.sv"
edit(ch, "assign dac_npnt_sub = dac_npnt - {1'b0,set_size_i,32'h0} - 1;",
     "`ifdef RTQLM_E1_SYNC\n"
     "wire [PNT_SIZE:0] e1_pnt = {1'b0,dac_pnt};\n"
     "wire [PNT_SIZE:0] e1_step = {1'b0,set_step[RSZ+15:0],set_step_lo};\n"
     "wire [PNT_SIZE:0] e1_size_inv = ~{1'b0,set_size_i,32'h0};\n"
     "wire [PNT_SIZE:0] e1_wrap_sum = e1_pnt ^ e1_step ^ e1_size_inv;\n"
     "wire [PNT_SIZE:0] e1_wrap_carry = ((e1_pnt & e1_step) | (e1_pnt & e1_size_inv) | (e1_step & e1_size_inv)) << 1;\n"
     "wire [PNT_SIZE:0] e1_next, e1_wrap, e1_idle_wrap;\n"
     "rp_e1_fast_add #(.W(PNT_SIZE+1)) e1_add_wrap (.a(e1_wrap_sum), .b(e1_wrap_carry), .sum(e1_wrap));\n"
     "rp_e1_fast_add #(.W(PNT_SIZE+1)) e1_add_next (.a(e1_pnt), .b(e1_step), .sum(e1_next));\n"
     "rp_e1_fast_add #(.W(PNT_SIZE+1)) e1_add_idle (.a(e1_pnt), .b(e1_size_inv), .sum(e1_idle_wrap));\n"
     "assign dac_npnt = dac_do ? e1_next : e1_pnt;\n"
     "assign dac_npnt_sub = dac_do ? e1_wrap : e1_idle_wrap;\n"
     "`else\nassign dac_npnt_sub = dac_npnt - {1'b0,set_size_i,32'h0} - 1;\n`endif")
edit(ch, "assign dac_npnt = dac_do ? dac_pnt + {set_step[RSZ+15:0],set_step_lo} : dac_pnt;",
     "`ifndef RTQLM_E1_SYNC\nassign dac_npnt = dac_do ? dac_pnt + {set_step[RSZ+15:0],set_step_lo} : dac_pnt;\n`endif")
edit(ch, "// Set after the first real output sample", "`ifdef RTQLM_E1_SYNC\n"
     "// Keep independent -1 results; a late trigger must select AFTER arithmetic.\n"
     "(* keep = \"true\" *) wire [31:0] e1_delay_load = (set_rdly_i > 0) ? set_rdly_i - 32'd1 : 32'd0;\n"
     "(* keep = \"true\" *) wire [31:0] e1_delay_dec = dly_cnt - 32'd1;\n"
     "`endif\n// Set after the first real output sample")
edit(ch, "            dly_cnt <= (set_rdly_i > 32'h0) ? (set_rdly_i - 32'h1) : 32'h0;",
     "`ifdef RTQLM_E1_SYNC\n            dly_cnt <= e1_delay_load;\n`else\n"
     "            dly_cnt <= (set_rdly_i > 32'h0) ? (set_rdly_i - 32'h1) : 32'h0;\n`endif")
edit(ch, "            dly_cnt <= dly_cnt - 32'h1;",
     "`ifdef RTQLM_E1_SYNC\n            dly_cnt <= e1_delay_dec;\n`else\n"
     "            dly_cnt <= dly_cnt - 32'h1;\n`endif")

# Avoid an optional network download in the vendor build script.
edit("red_pitaya_vivado_Z10.tcl", "tclapp::install -quiet ultrafast", "# Optional ultrafast Tcl app is not required for bitstream generation.")
edit("red_pitaya_vivado_Z10.tcl", "launch_runs impl_1\n",
     "set_property strategy Performance_ExplorePostRoutePhysOpt [get_runs impl_1]\nlaunch_runs impl_1\n")
edit("red_pitaya_vivado_Z10.tcl", "launch_runs synth_1\n",
     "set_property STEPS.SYNTH_DESIGN.ARGS.RESOURCE_SHARING off [get_runs synth_1]\nlaunch_runs synth_1\n")
# Define only the asynchronous pad -> first-stage synchronizer path as false.
p = SOURCE / "prj/v0.94/sdc/red_pitaya.xdc"
text = p.read_text()
constraint = "\n# RTQLM: asynchronous E1 input; subsequent synchronizer stages are timed normally.\nset_false_path -to [get_pins -hier -filter {NAME =~ *e1_tag/e1_meta_reg/D}]\n"
if "RTQLM: asynchronous E1 input" not in text:
    p.write_text(text + constraint, newline="\n")
print("Prepared E1 tagged-ADC project", SOURCE)
