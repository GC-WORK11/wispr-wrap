#!/usr/bin/env python3
"""sink.py — the demo's 'editor': a real program that visibly receives
typed text. Runs inside xfce4-terminal on the scene display.

A real editor sees every keystroke the moment it's pressed — xdotool
type does NOT send a trailing Return — so stdin runs in cbreak mode
(chars available immediately, Ctrl-C still kills) and every received
chunk is echoed on screen AND appended to $WF_SINK_LOG for the driver
to verify. It's deliberately dumb — the point is that real keystrokes
really land.
"""
import codecs
import os
import sys
import termios
import time
import tty

LOG = os.environ.get("WF_SINK_LOG", "/tmp/demo/out/sink.log")


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    print("┌────────────────────────────────────────────────┐")
    print("│  wf editor — a real text sink                  │")
    print("│  whatever the phone types lands here           │")
    print("└────────────────────────────────────────────────┘")
    print()
    sys.stdout.flush()
    dec = codecs.getincrementaldecoder("utf-8")("replace")
    tty.setcbreak(fd)                       # chars arrive as typed
    try:
        with open(LOG, "a", buffering=1) as log, os.fdopen(
                os.dup(fd), "rb", buffering=0) as raw:
            log.write("# sink start %s\n" % time.strftime("%H:%M:%S"))
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
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


if __name__ == "__main__":
    main()
