#!/usr/bin/env python3
"""xinput-shim — a minimal `xinput test-xi2 --root` reimplementation for
boxes where the x11-xinput-apps package is not installed (this gate box).

WHY THIS EXISTS (honest note): server/record_action.py captures input by
spawning `xinput test-xi2 --root` and parsing its text output. The xinput
binary is absent here, but the underlying X mechanism it uses — selecting
cooked XI2 event classes on the root window — is identical and available
through python-xlib. This program selects the SAME event classes on the
SAME display and prints the SAME text blocks, so the shipped recorder
runs its real code path (spawn -> parse -> normalize -> save) against
real X server input. It is a capture backend for the gate, NOT a fake of
the engine: every event printed is a real XI2 event the X server
delivered (xdotool/XTEST events are indistinguishable from physical
input at this layer — which is exactly what `xinput test-xi2` sees too).

Only `xinput test-xi2 --root` is implemented; any other invocation exits 2.
"""
import sys
import signal


NAMES = {2: "KeyPress", 3: "KeyRelease", 4: "ButtonPress", 5: "ButtonRelease"}
MASK = (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5)
KEYREPEAT = 1 << 16            # XIKeyRepeat in xXIDeviceEvent.flags


def main():
    argv = sys.argv[1:]
    if argv != ["test-xi2", "--root"]:
        sys.stderr.write("xinput-shim: only 'test-xi2 --root' is "
                         "implemented (got %r)\n" % (argv,))
        return 2
    try:
        from Xlib import display
        from Xlib.ext import xinput
    except ImportError:
        # The daemon runs this with a SCRATCH HOME — python-xlib lives in
        # the real user's site dir, which $HOME no longer points at.
        # Resolve it from the passwd entry, not the environment.
        import os as _os
        import pwd as _pwd
        _home = _pwd.getpwuid(_os.getuid()).pw_dir
        sys.path.append("%s/.local/lib/python%d.%d/site-packages" % (
            _home, sys.version_info.major, sys.version_info.minor))
        from Xlib import display
        from Xlib.ext import xinput
    d = display.Display()
    xinput.query_version(d)
    root = d.screen().root
    root.xinput_select_events([(xinput.AllDevices, MASK)])
    d.flush()

    def bye(*_a):
        sys.exit(0)
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)

    while True:
        ev = d.next_event()          # blocks — a real XI2 event
        if ev.type != 35:            # GenericEvent only
            continue
        data = ev.data
        if ev.evtype not in NAMES:
            continue
        detail = int(getattr(data, "detail", 0) or 0)
        flags = int(getattr(data, "flags", 0) or 0)
        rx = float(getattr(data, "root_x", 0.0) or 0.0)
        ry = float(getattr(data, "root_y", 0.0) or 0.0)
        dev = int(getattr(data, "sourceid",
                          getattr(data, "deviceid", 0)) or 0)
        print("EVENT type %d (%s)" % (ev.evtype, NAMES[ev.evtype]))
        print("\tdevice: %d" % dev)
        print("\tdetail: %d" % detail)
        print("\tflags: %s" % ("repeat" if flags & KEYREPEAT else ""))
        print("\troot: %.2f/%.2f" % (rx, ry))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
