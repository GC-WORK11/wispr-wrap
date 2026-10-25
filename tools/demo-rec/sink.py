#!/usr/bin/env python3
"""sink.py — the demo's 'editor': a real program that visibly receives
typed text. Runs inside xfce4-terminal on the scene display. Each line
the laptop receives (xdotool-typed) is echoed with a counter and logged
to $WF_SINK_LOG for the driver to verify. It's deliberately dumb — the
point is that real keystrokes really land.
"""
import os
import sys
import time

LOG = os.environ.get("WF_SINK_LOG", "/tmp/demo/out/sink.log")


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    n = 0
    print("┌────────────────────────────────────────────────┐")
    print("│  wf editor — a real text sink                  │")
    print("│  whatever the phone types lands here           │")
    print("└────────────────────────────────────────────────┘")
    print()
    sys.stdout.flush()
    with open(LOG, "a", buffering=1) as log:
        log.write("# sink start %s\n" % time.strftime("%H:%M:%S"))
        while True:
            line = sys.stdin.readline()
            if line == "":
                time.sleep(0.05)
                continue
            n += 1
            line = line.rstrip("\n")
            sys.stdout.write("  %2d │ %s\n" % (n, line))
            sys.stdout.flush()
            log.write("%s\n" % line)


if __name__ == "__main__":
    main()
