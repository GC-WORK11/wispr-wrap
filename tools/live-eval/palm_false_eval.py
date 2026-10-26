#!/usr/bin/env python3
"""palm_false_eval — count accidental PALM-OPEN listen events on REAL
held-out benchmark keypoints through the shipped server/endpointer.py.

This is the repro for the resting-hand false trigger: the benchmark
signer has 320 clips (64 signs x 5 reps); each clip is padded with 8
still rows before/after, exactly like the wire-eval harness, and the
open palm that is already in view at stream start used to fire PALM-OPEN
after hold_s (0.45 s) without the signer presenting anything.

  python3 tools/live-eval/palm_false_eval.py \
      --kp /path/to/benchmark-kp --signer 6 [--continuous]

The benchmark keypoints are CC BY-NC-SA 4.0 and stay outside this repo;
only the counts are printed. `--continuous` feeds one Endpointer across all clips
(the daemon's LIVE['ep']), the default resets per clip.

Expected after the fix: 0/320 per-clip (2/320 continuous), 0 early
(<1 s) events. Before: 155/320, 125 early.
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "server"))
import endpointer  # noqa: E402


def load(path):
    d = json.load(open(path))
    return d["rows"], float(d.get("hz", 15.0))


def wire(rows, hz, t0_ms, pad):
    rows = [rows[0]] * pad + list(rows) + [rows[-1]] * pad
    out = []
    for i, r in enumerate(rows):
        pts = [{"x": r[j][0], "y": r[j][1], "score": r[j][2]}
               for j in range(11, 53)]
        out.append({"t_ms": int(t0_ms + i * 1000.0 / hz), "points": pts})
    return out, (out[-1]["t_ms"] + 1000.0 / hz if out else t0_ms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kp", required=True)
    ap.add_argument("--signer", type=int, default=6)
    ap.add_argument("--pad", type=int, default=8)
    ap.add_argument("--continuous", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.kp, "*_%03d_*.json" % a.signer)))
    if not files:
        sys.exit("no keypoint files for signer %d under %s" %
                 (a.signer, a.kp))
    ep = endpointer.Endpointer(palm_sign="PALM-OPEN", namaste_sign="NAMASTE")
    t = 0.0
    hit = 0
    times = []
    for path in files:
        rows, hz = load(path)
        frames, t = wire(rows, hz, t, a.pad)
        got = False
        for fr in frames:
            for ev in ep.feed(fr):
                if ev.get("type") == "mode" and \
                        ev.get("name") == "PALM-OPEN":
                    got = True
                    times.append(round(ev["t_ms"] / 1000.0, 2))
        hit += got
        if not a.continuous:
            ep.reset()
            t = 0.0
    out = {"clips": len(files), "palm_clips": hit,
           "early_under_1s": sum(1 for x in times if x < 1.0),
           "mode": "continuous" if a.continuous else "per-clip",
           "signer": a.signer}
    print(json.dumps(out, sort_keys=True))
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
