# Validate the isolated original-125-14 Z10 implementation. No hardware access.
set root [file normalize [lindex $argv 0]]
set source [file normalize [lindex $argv 1]]
open_project [file join $source prj v0.94 build redpitaya.xpr]
if {[get_property PART [current_project]] ne "xc7z010clg400-1"} {
    error "Unexpected FPGA part for original 125-14"
}
set defs [get_property VERILOG_DEFINE [get_filesets sources_1]]
foreach required {Z10 RTQLM_E1_SYNC} {
    if {[lsearch -exact $defs $required] < 0} { error "Missing build define $required" }
}
open_run impl_1
set meta [get_cells -hier -filter {NAME =~ *e1_tag/e1_meta_reg}]
set sync [get_cells -hier -filter {NAME =~ *e1_tag/e1_sync_reg}]
if {[llength $meta] != 1 || [llength $sync] != 1} { error "E1 synchronizer registers missing" }
if {![get_property ASYNC_REG $meta] || ![get_property ASYNC_REG $sync]} {
    error "E1 synchronizer attributes missing"
}
set sync_paths [get_timing_paths -from $meta -to $sync -max_paths 1]
if {[llength $sync_paths] != 1} { error "E1 synchronizer stage-to-stage path is not timed" }
set sync_slack [get_property SLACK $sync_paths]
if {$sync_slack < 0} { error "E1 synchronizer stage-to-stage path fails timing" }
set e1 [get_ports {exp_p_io[0]}]
if {[get_property PACKAGE_PIN $e1] ne "G17" || [get_property IOSTANDARD $e1] ne "LVCMOS33"} {
    error "Original-board E1 physical pin 3 constraint changed"
}
set out [file join $source prj v0.94 out]
report_timing -from $meta -to $sync -file [file join $out e1_synchronizer_timing.rpt]
report_cdc -details -file [file join $out red_pitaya_cdc.rpt]
report_io -file [file join $out original_125_14_io.rpt]
set info [open [file join $out original_125_14_implementation.txt] w]
puts $info "targetModel=z10_125"
puts $info "part=[get_property PART [current_project]]"
puts $info "defines=$defs"
puts $info "e1PackagePin=[get_property PACKAGE_PIN $e1]"
puts $info "e1IOStandard=[get_property IOSTANDARD $e1]"
puts $info "e1MetaAsync=[get_property ASYNC_REG $meta]"
puts $info "e1SyncAsync=[get_property ASYNC_REG $sync]"
puts $info "e1SynchronizerSlackNs=$sync_slack"
puts $info "hardwareTested=false"
close $info
puts "PASS: original 125-14 target, E1 pin, and routed synchronizer checks"
exit
