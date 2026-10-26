# sink.tcl — the laptop's focused editor for the Go Live proofs.
# A real Tk text widget: keystrokes reach it like any editor (XTEST into the
# focused window), and its buffer is mirrored to a file on every KeyRelease,
# so "typed into the editor" is read from the WIDGET, not from a pipe.
#
#   wish sink.tcl <mirror-file> [title]
#
# WM_CLASS SinkEdit (same as tools/realrun/sink.tcl) so the daemon's focus
# report and the proofs identify it like an installed app.
toplevel .w -class SinkEdit
wm title .w [expr {[llength $argv] > 1 ? [lindex $argv 1] : "Notes"}]
wm withdraw .
set sink [lindex $argv 0]
. configure -background #ffffff
text .w.t -width 60 -height 12 -font {Helvetica 20} -wrap word \
    -borderwidth 0 -highlightthickness 0 -padx 28 -pady 24 \
    -background #ffffff -foreground #111111 -insertbackground #c2185b \
    -insertwidth 2 -spacing1 4 -spacing3 4
pack .w.t -fill both -expand 1
bind .w.t <KeyRelease> {
    catch {
        set f [open $sink w]
        puts -nonewline $f [.w.t get 1.0 "end-1c"]
        close $f
    }
}
focus .w.t
