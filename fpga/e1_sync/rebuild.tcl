# Rebuild modified RTL while retaining the already synthesized vendor IP.
open_project upstream/prj/v0.94/build/redpitaya.xpr
foreach name {rp_e1_tag.sv rp_e1_fast_add.sv} {
    if {[llength [get_files -quiet *$name]] == 0} { add_files upstream/prj/v0.94/rtl/$name }
}
update_compile_order -fileset sources_1
set_property STEPS.SYNTH_DESIGN.ARGS.RESOURCE_SHARING off [get_runs synth_1]
set_property strategy Performance_ExplorePostRoutePhysOpt [get_runs impl_1]
reset_run synth_1
launch_runs synth_1
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "Synthesis failed" }
launch_runs impl_1
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "Implementation failed" }
set out upstream/prj/v0.94/out
foreach file [glob upstream/prj/v0.94/build/redpitaya.runs/synth_1/*.rpt] { file copy -force $file $out/ }
foreach file [glob upstream/prj/v0.94/build/redpitaya.runs/impl_1/*.rpt] { file copy -force $file $out/ }
open_run impl_1
set meta [get_cells -hier -filter {NAME =~ *e1_tag/e1_meta_reg}]
set sync [get_cells -hier -filter {NAME =~ *e1_tag/e1_sync_reg}]
if {[llength $meta] != 1 || [llength $sync] != 1} { error "E1 synchronizer registers missing" }
if {![get_property ASYNC_REG $meta] || ![get_property ASYNC_REG $sync]} {
    error "E1 synchronizer attributes missing"
}
report_timing -from $meta -to $sync -file $out/e1_synchronizer_timing.rpt
report_cdc -details -file $out/red_pitaya_cdc.rpt
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
write_bitstream -force $out/red_pitaya
write_cfgmem -format BIN -interface SMAPx32 -disablebitswap -loadbit "up 0x0 $out/red_pitaya.bit" -file $out/red_pitaya.bin -force
exit
