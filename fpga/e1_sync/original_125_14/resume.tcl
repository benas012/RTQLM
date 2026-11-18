# Resume the isolated build after an interrupted launcher. Completed IP is reused.
set source [file normalize [lindex $argv 0]]
set out [file join $source prj v0.94 out]
set_param general.maxThreads 4
open_project [file join $source prj v0.94 build redpitaya.xpr]
foreach run [get_runs *synth*] {
    if {[get_property PROGRESS $run] ne "100%"} {
        catch {stop_runs $run}
        reset_run $run
    }
}
set_property STEPS.SYNTH_DESIGN.ARGS.RESOURCE_SHARING off [get_runs synth_1]
set_property strategy Performance_ExplorePostRoutePhysOpt [get_runs impl_1]
launch_runs synth_1 -jobs 3
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "Synthesis failed" }
foreach file [glob [file join $source prj v0.94 build redpitaya.runs synth_1 *.rpt]] {
    file copy -force $file $out/
}
launch_runs impl_1 -jobs 3
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "Implementation failed" }
foreach file [glob [file join $source prj v0.94 build redpitaya.runs impl_1 *.rpt]] {
    file copy -force $file $out/
}
open_run impl_1
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
write_bitstream -force [file join $out red_pitaya.bit]
write_cfgmem -format BIN -interface SMAPx32 -disablebitswap -loadbit \
    "up 0x0 [file join $out red_pitaya.bit]" -file [file join $out red_pitaya.bin] -force
exit
