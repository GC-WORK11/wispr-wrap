#!/usr/bin/env python3
"""feed.py — push a demo clip file straight at the daemon's /live/kp.

Verification driver for tools/demo/clips/*.json — same wire the replayed
browser path produces ({t_ms, points[42]}), without needing chrome up.
Stream time is a persisted synthetic clock ($DEMO_STATE/kpclock) so frames
never go backward across invocations — the endpointer measures speed off
t_ms deltas, and a regressed clock would freeze motion at dt=0.

  feed.py --base http://127.0.0.1:8874 --key K clips/palm-hold.json ...
  flags: --fps 40 (wall pacing; daemon caps at 45)
         --clock PATH (default $DEMO_STATE/kpclock or /tmp/wf-demo/kpclock)
         --clock-reset  (restart the synthetic clock at wall now)
Prints every state-changing event the daemon returns:
  mode=PALM-OPEN | match=GLOSS dist=.. accepted=.. | action=.. | teach=..
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

FRAME_MS = 1000.0 / 15.0


def post(base, key, obj):
    r = urllib.request.Request(base + "/live/kp",
                               data=json.dumps(obj).encode(),
                               method="POST",
                               headers={"X-WF-Key": key,
                                        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)}


def clock_load(path):
    try:
        return float(open(path).read().strip())
    except Exception:
        return 0.0


def clock_save(path, v):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(str(v))


def emit(frames, base, key, fps, clock_path):
    delay = 1.0 / max(fps, 1.0)
    last = clock_load(clock_path)
    if last <= 0:
        last = time.time() * 1000.0    # start ahead of any stale daemon _t
    ok = True
    for f in frames:
        last += FRAME_MS
        # the daemon rate-limits /live/kp in a sliding 1s window
        # (LIVE_MAX_FPS=45): a frame that lands 429 is retried after a
        # beat, not dropped — the clip's t_ms clock is synthetic anyway.
        for attempt in range(6):
            st, body = post(base, key, {"t_ms": int(last),
                                        "points": f["points"]})
            if st == 200:
                break
            if st == 429:
                time.sleep(0.2)
                continue
            break
        if st != 200:
            print("  /live/kp -> %s %s" % (st, body), file=sys.stderr)
            ok = False
            break
        for k in ("mode", "match", "action", "teach", "seg"):
            if k in body:
                v = body[k]
                if k == "match":
                    v = ("match=%s dist=%.3f accepted=%s reason=%s" %
                         (v.get("gloss"), v.get("dist", 0),
                          v.get("accepted"), v.get("reason", "")))
                print("  %s=%s" % (k, v))
        time.sleep(delay)
    clock_save(clock_path, last)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--clock",
                    default=os.path.join(
                        os.environ.get("DEMO_STATE", "/tmp/wf-demo"),
                        "kpclock"))
    ap.add_argument("--clock-reset", action="store_true")
    ap.add_argument("clips", nargs="+")
    a = ap.parse_args()
    if a.clock_reset:
        clock_save(a.clock, time.time() * 1000.0)
    ok = True
    for c in a.clips:
        frames = json.load(open(c))["frames"]
        print("# %s (%d frames)" % (os.path.basename(c), len(frames)))
        ok &= emit(frames, a.base, a.key, a.fps, a.clock)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
