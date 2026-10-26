#!/usr/bin/env python3
"""sink.py — the demo's 'editor': a real program that visibly receives
typed text. Runs inside xfce4-terminal on the scene display.

A real editor sees every keystroke the moment it's pressed — xdotool
type does NOT send a trailing Return — so stdin runs in cbreak mode
(chars available immediately, Ctrl-C still kills) and every received
chunk is echoed on screen AND appended to $WF_SINK_LOG for the driver
to verify. It's deliberately dumb — the point is that real keystrokes
really land.

Hardening (learned the hard way): the log file is opened and stamped
BEFORE any termios call — a missing sink.log can only mean this process
never ran. And if stdin isn't a tty we still run (raw os.read); a
silent death with a live window is exactly the lie this demo refuses.
"""
import codecs
import os
import sys
import time

LOG = os.environ.get("WF_SINK_LOG", "/tmp/demo/out/sink.log")


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    # stamp existence first — drive.py asserts this file within seconds
    log = open(LOG, "a", buffering=1)
    log.write("# sink start %s\n" % time.strftime("%H:%M:%S"))
    fd = sys.stdin.fileno()
    print("┌────────────────────────────────────────────────┐")
    print("│  wf editor — a real text sink                  │")
    print("│  whatever the phone types lands here           │")
    print("└────────────────────────────────────────────────┘")
    print()
    sys.stdout.flush()

    old = None
    try:
        import termios
        import tty
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)                  # chars arrive as typed
    except Exception as e:                 # no tty (piped) — still a sink
        log.write("# note: stdin not a tty (%r) — raw read\n" % e)
        print("[note: stdin not a tty — reading raw]")

    dec = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        with os.fdopen(os.dup(fd), "rb", buffering=0) as raw:
            while True:
                data = raw.read(64)
                if not data:
                    time.sleep(0.05)
                    continue
                txt = dec.decode(data)
                sys.stdout.write(txt)        # cbreak: no tty echo —
                sys.stdout.flush()           # we show what we got
                log.write(txt)
    finally:
        if old is not None:
            import termios
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        log.close()


if __name__ == "__main__":
    main()
