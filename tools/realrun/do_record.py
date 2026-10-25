#!/usr/bin/env python3
"""do_record — drive the SHIPPED record_action engine against real input.

This is the gate's demonstrator: it runs server/record_action.py's real
Recorder (the same object the daemon wires), which spawns `xinput
test-xi2 --root` — resolved on this box to tools/realrun/bin/xinput,
the XI2 shim that prints the same protocol for real X events. While it
records, this driver injects REAL keystrokes + a click via xdotool into
the real focused window (XTEST — indistinguishable from physical input
at the XI2 layer, which is exactly what `xinput test-xi2` reads too).

  do_record.py <name> <recdir> <inject-script>...

  inject-script args:  type:<text> | key:<chord> | click:X,Y,B | sleep:S
Prints a JSON summary: {probe, start, stop, macro_path, steps, n_events}.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "server"))
import record_action  # noqa: E402

D = os.environ.get("DISPLAY", ":97")


def sh(*a):
    return subprocess.run([str(x) for x in a],
                          env={"DISPLAY": D,
                               "PATH": "/usr/bin:/bin"},
                          capture_output=True, timeout=10)


def inject(spec):
    kind, _, arg = spec.partition(":")
    if kind == "type":
        sh("xdotool", "type", "--delay", "60", arg)
    elif kind == "key":
        sh("xdotool", "key", arg)
    elif kind == "click":
        x, y, b = arg.split(",")
        sh("xdotool", "mousemove", x, y, "click", b)
    elif kind == "sleep":
        time.sleep(float(arg))


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "realrun-rec"
    recdir = sys.argv[2] if len(sys.argv) > 2 else None
    steps = sys.argv[3:] or ["type:hi", "key:ctrl+s", "click:400,400,1"]
    rec = record_action.Recorder(name, dir=recdir)
    out = {"probe": rec._probe}
    st = rec.start()
    out["start"] = st
    if not st.get("ok"):
        print(json.dumps(out, indent=1))
        return 3
    time.sleep(0.6)                 # let the capture thread come up
    for spec in steps:
        inject(spec)
        time.sleep(0.15)
    time.sleep(0.6)                 # drain the pipe
    res = rec.stop(save=True)
    out["stop"] = res
    out["macro_path"] = res.get("path")
    out["n_steps"] = res.get("n_steps")
    out["steps"] = res.get("steps")
    try:
        m = record_action.load_macro(name, recdir)
        out["format"] = m.get("format")
        out["engine"] = m.get("engine")
        out["n_events"] = len(m.get("events", []))
        out["mode"] = oct(os.stat(m and res["path"]).st_mode & 0o777)
    except Exception as e:
        out["load_error"] = repr(e)
    print(json.dumps(out, indent=1))
    return 0 if res.get("n_steps") else 4


if __name__ == "__main__":
    sys.exit(main())
