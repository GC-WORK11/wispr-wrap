# sink.tcl — the realrun "focused editor": a real Tk text widget app whose
# buffer is mirrored to a file on every KeyRelease. Keystrokes reach it
# exactly like any real editor (XTEST -> the focused window), so
# "text typed into the editor" proofs read this file — the file contains
# the WIDGET's buffer, not a pipe the test wrote itself.
#
#   wish sink.tcl <sink-file> [title]
#
# WM_CLASS is set to SinkEdit so the proof scripts and the daemon's
# active_window_class() identify it like any installed app.
toplevel .w -class SinkEdit
wm title .w [expr {[llength $argv] > 1 ? [lindex $argv 1] : "realrun-sink"}]
wm withdraw .
set sink [lindex $argv 0]
text .w.t -width 64 -height 14 -font {TkFixedFont 11}
pack .w.t -fill both -expand 1
bind .w.t <KeyRelease> {
    catch {
        set f [open $sink w]
        puts -nonewline $f [.w.t get 1.0 "end-1c"]
        close $f
    }
}
focus .w.t
