# Keep the stock/pinned hardware sources intact; bound build concurrency here.
set_param general.maxThreads 4
rename launch_runs rtqlm_vendor_launch_runs
proc launch_runs {args} {
    if {[lsearch -exact $args "-jobs"] < 0} { lappend args -jobs 3 }
    return [rtqlm_vendor_launch_runs {*}$args]
}
source red_pitaya_vivado_Z10.tcl
