# Verdi Tcl Reference: wvGetScope, wvGetSignalsByScope, wvAddSignal -scope.
# Query the loaded FSDB, so changing DUT RTL does not require a signal preset.
proc studio_scope_path {name} {
    if {[string first / $name] < 0} {set name [string map {. /} $name]}
    return /[string trim $name /]
}

proc studio_find_dut {wave requested preferred} {
    set roots [wvGetScope -win $wave]
    set queue $roots
    set seen [dict create]
    set candidates {}
    while {[llength $queue]} {
        set scope [studio_scope_path [lindex $queue 0]]
        set queue [lrange $queue 1 end]
        if {[dict exists $seen $scope]} {continue}
        dict set seen $scope 1
        if {$requested ne "" && $scope eq $requested} {return $scope}
        if {$requested eq "" && $scope eq $preferred} {return $scope}
        if {$requested ne "" && [string first $scope/ $requested/] != 0} {continue}
        if {[lindex [split $scope /] end] eq "dut"} {lappend candidates $scope}
        # Verdi 2018 returns local child names, not full hierarchical paths.
        foreach child [wvGetScope -win $wave $scope] {
            if {[string index $child 0] eq "/"} {
                lappend queue $child
            } else {
                lappend queue $scope/$child
            }
        }
    }
    if {$requested ne ""} {error "DUT scope not found in FSDB: $requested. Check DUT_SCOPE / the GUI instance path."}
    if {[llength $candidates] == 1} {return [lindex $candidates 0]}
    if {[llength $candidates] > 1} {error "Multiple DUT instances: $candidates. Select DUT_SCOPE / the GUI instance path."}
    if {[llength $roots] == 1} {return [studio_scope_path [lindex $roots 0]]}
    error "Cannot select a unique DUT scope. Set DUT_SCOPE / the GUI instance path. FSDB roots: $roots"
}

proc studio_write_lines {path lines} {
    set file [open $path.tmp w]
    fconfigure $file -encoding utf-8
    foreach line $lines {puts $file $line}
    close $file
    file rename -force $path.tmp $path
}

if {[catch {
    set wave [wvGetCurrentWindow]
    set requested ""
    set preferred /tb_cmdbuf_rd/dut
    if {[info exists ::env(CMDBUF_WAVE_DUT_SCOPE)] && $::env(CMDBUF_WAVE_DUT_SCOPE) ne ""} {
        set requested [studio_scope_path $::env(CMDBUF_WAVE_DUT_SCOPE)]
    }
    if {[info exists ::env(CMDBUF_WAVE_DEFAULT_SCOPE)]} {
        set preferred [studio_scope_path $::env(CMDBUF_WAVE_DEFAULT_SCOPE)]
    }
    set scope [studio_find_dut $wave $requested $preferred]
    set signals [wvGetSignalsByScope -win $wave $scope]
    if {![llength $signals]} {error "No dumped signals in DUT scope $scope"}
    set expected {}
    foreach signal $signals {
        if {[string index $signal 0] eq "/"} {
            lappend expected $signal
        } else {
            lappend expected $scope/$signal
        }
    }
    studio_write_lines wave_expected.txt $expected
    # No type filter: include ports, nets, registers and dumped arrays at DUT top.
    if {![wvAddSignal -win $wave -clear -scope $scope]} {error "wvAddSignal failed for $scope"}
    wvZoomAll -win $wave
    set displayed [wvGetDisplaySignals -win $wave -all]
    if {![llength $displayed]} {error "The nWave signal list is empty after adding $scope"}
    studio_write_lines wave_displayed.txt $displayed
    set present [dict create]
    foreach signal $displayed {dict set present $signal 1}
    set missing {}
    foreach signal $expected {
        if {![dict exists $present $signal]} {lappend missing $signal}
    }
    if {[llength $missing]} {error "Some DUT signals were not added: $missing"}
    wvSaveSignal -win $wave dut_top.rc
    studio_write_lines wave_autoload.status [list READY $scope [llength $signals]]
    puts "EDA_WAVE_PRESET_READY scope=$scope signals=[llength $signals]"
} message]} {
    studio_write_lines wave_autoload.status [list ERROR $message]
    puts stderr "EDA_WAVE_PRESET_ERROR: $message"
}
