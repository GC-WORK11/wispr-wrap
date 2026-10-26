#!/usr/bin/env python3
"""kpstream — stream synthetic kp42 sign clips at a REAL live daemon.

This is the gate's camera stand-in: it renders the same wire frames the
phone composer POSTs ({t_ms, points:[{x,y,score} x42]}) — two hands,
21 MediaPipe landmarks each — reusing the e2e-live motion grammar
(tools/e2e-live/synth.py). Frames are delivered over the REAL HTTP
path (POST /live/kp with X-WF-Key), so what the daemon does with them
is the shipped product: its own endpointer -> its own DTW bank -> its
own action routing. Only the pixels are synthetic — never video of a
person, safe for the public repo.

Usage:
  kpstream.py --base http://127.0.0.1:P --key K <clip>...
    clip = palm | namaste | sign:NAME | teach:NAME | wait:MS
           palm    -> held open palm (daemon endpointer -> PALM-OPEN
                      mode event -> listening=True)
           namaste -> held palms together (listening=False)
           sign:X  -> a grammar sign (SWIPE-R, SWIPE-L, PUSH-UP,
                      CIRCLE, Z-SHAPE, V-CHECK, UNKNOWN)
           teach:X -> same as sign:X (call between /teach begin+end)
           wait:N  -> idle frames, no hands, N ms of stream-time
  Flags: --fps 40 (posting cadence, must stay under the daemon's
         LIVE_MAX_FPS=45 rate limit), --quiet.

Prints one line per daemon event that changed state:
  mode=PALM-OPEN | match=GLOSS dist=.. accepted=True | teach=.. | action=..
Exit 0 always — assertions live in the calling verify script.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "e2e-live"))
import synth  # noqa: E402

FRAME_MS = 1000.0 / 15.0    # synthetic stream time runs at 15 Hz
# t_ms seeds from WALL CLOCK: each kpstream process must keep the daemon's
# endpointer clock monotonic (a restart at 0 gives dt<=0 -> zero motion
# energy -> the clip is invisible). Within a clip we still advance 66.7ms
# per frame — the 15 Hz phone cadence.
BASE_MS = [time.monotonic() * 1000.0]

# Jitter budget: the endpointer's exit_v is 0.7 palm-widths/s. Our palm
# is ~0.034 normalized units, so jitter of 0.003 = ~1.3 pw/s of pure
# noise -> a "still" tail reads as motion and the segment NEVER closes.
# 0.0004 keeps realism (no frozen landmarks) while staying under exit_v.
JITTER = 0.0004

# Handshape isolation: synth.render_clip draws an OPEN palm everywhere.
# A still open palm is the endpointer's listen-mode sign by definition —
# any still lead/tail of a "sign" clip would fire PALM-OPEN mid-clip and
# pollute the listen-gate proof (the sign itself would turn listening
# on). So sign/teach clips wear a FIST: fingertips curled to 30% reach,
# wrist+MCP anchors identical -> the centroid trajectory (what DTW
# matches on) is unchanged, but openness ~0.7 < 1.35 -> never a mode
# event. Only the dedicated palm/namaste clips stay open-palmed.
TIPS = (4, 8, 12, 16, 20)


def _fist_block(pts):
    """Curl one 21-point hand's tips toward the wrist; anchors kept."""
    if not pts:
        return pts
    w = pts[0]
    out = []
    for i, p in enumerate(pts):
        if i in TIPS:
            out.append({"x": w["x"] + (p["x"] - w["x"]) * 0.30,
                        "y": w["y"] + (p["y"] - w["y"]) * 0.30,
                        "score": p["score"]})
        else:
            out.append(dict(p))
    return out


def fistify(frames):
    """Rewrite both hands of every present frame to the fist shape."""
    for f in frames:
        ps = f.get("points") or []
        if len(ps) == 42 and ps[0].get("score", 0) + \
                ps[21].get("score", 0) > 0:
            f["points"] = _fist_block(ps[:21]) + _fist_block(ps[21:])
    return frames

# The gate's sign grammar — every bound gloss gets its OWN motion-distinct
# path so the DTW bank never confuses two bound glosses (it is direction
# sensitive: swipe-left != swipe-right). Same kinematics the e2e lane uses.
RR_PATHS = {
    "RR-SWIPE":   [(0.20, 0.60), (0.80, 0.60)],
    "RR-OPEN":    [(0.25, 0.38), (0.75, 0.38), (0.25, 0.74), (0.75, 0.74)],
    "RR-FOCUS":   [(0.50, 0.78), (0.50, 0.26)],
    "RR-FOCUS2":  [(0.25, 0.42), (0.50, 0.70), (0.75, 0.42)],
    "RR-CLOSE":   [(0.80, 0.60), (0.20, 0.60)],
    "RR-TILE":    [(0.50 + 0.20 * __import__("math").cos(2 * __import__("math").pi * i / 24),
                    0.55 + 0.20 * __import__("math").sin(2 * __import__("math").pi * i / 24))
                   for i in range(25)],
    "RR-TILE2":   [(0.50, 0.26), (0.50, 0.78)],
    "RR-DESK":    [(0.20, 0.30), (0.80, 0.75)],
    "RR-DESK2":   [(0.80, 0.30), (0.20, 0.75)],
    "RR-MOVEWIN": [(0.20, 0.30), (0.20, 0.70), (0.80, 0.70)],
    "RR-SHOT":    [(0.80, 0.30), (0.80, 0.70), (0.20, 0.70)],
    "RR-MACRO":   [(0.15, 0.35), (0.30, 0.70), (0.45, 0.40),
                   (0.60, 0.70), (0.75, 0.35)],
    "RR-CTX":     [(0.30, 0.35), (0.70, 0.35), (0.50, 0.72), (0.30, 0.35)],
    "RR-CTX2":    [(0.35, 0.70), (0.65, 0.70), (0.50, 0.30), (0.35, 0.70)],
    "RR-OBS":     [(0.20, 0.25), (0.20, 0.75), (0.80, 0.75), (0.80, 0.25)],
    "RR-PICK":    [(0.15, 0.15), (0.85, 0.15), (0.85, 0.85)],
    "RR-PICK2":   [(0.15, 0.85), (0.85, 0.85), (0.15, 0.15)],
    "RR-PICK3":   [(0.50, 0.15), (0.15, 0.50), (0.50, 0.85), (0.85, 0.50)],
    "RR-PARK":    [(0.70, 0.35), (0.40, 0.35), (0.40, 0.68)],
    "RR-PARK2":   [(0.30, 0.35), (0.60, 0.35), (0.60, 0.68)],
    "RR-REC":     [(0.25, 0.70), (0.25, 0.35), (0.45, 0.55),
                   (0.65, 0.35), (0.65, 0.70)],
}


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


def emit(frames, base, key, fps, quiet=False):
    """POST one rendered clip; synthetic t_ms advances at 15 Hz, posts
    are paced to `fps` wall-clock (rate-limit safe)."""
    delay = 1.0 / max(fps, 1.0)
    last = BASE_MS[0]
    for i, f in enumerate(frames):
        last = max(last + FRAME_MS, f.get("t_ms", 0) or last + FRAME_MS)
        st, body = post(base, key, {"t_ms": int(last),
                                    "points": f["points"],
                                    "hand": f.get("hand", "right")})
        if st != 200:
            print(f"  /live/kp -> {st} {body}", file=sys.stderr)
            return False
        for k in ("mode", "match", "action", "teach", "seg"):
            if k in body:
                v = body[k]
                if k == "match":
                    v = ("match=%s dist=%.3f accepted=%s" %
                         (v.get("gloss"), v.get("dist", 0),
                          v.get("accepted")))
                elif k == "action":
                    v = "action=%s" % v
                if not quiet:
                    print("  %s=%s" % (k, v))
        time.sleep(delay)
    BASE_MS[0] = last + FRAME_MS
    return True


def palm_hold(n=12):
    """Held open palm on the active hand (parked hand also open — the
    endpointer fires on whichever hand qualifies)."""
    path = [(0.50, 0.55)]
    return synth.render_clip(path, n_motion=n, lead=0, tail=0, absent=0,
                             jitter=JITTER)


def namaste_hold(n=12):
    """Two open palms held together at center -> NAMASTE mode event.
    Palm centroids must sit within namaste_gap (1.4 x avg palm size
    ~ 0.048 in normalized units) of each other."""
    frames = []
    for i in range(n):
        pa = [{"x": 0.478 + ox, "y": 0.55 + oy, "score": 0.9}
              for ox, oy in synth.PALM]
        pb = [{"x": 0.522 + ox, "y": 0.55 + oy, "score": 0.9}
              for ox, oy in synth.PALM]
        frames.append({"t_ms": 0.0, "points": pa + pb})
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--fps", type=float, default=40)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("clips", nargs="+")
    a = ap.parse_args()
    ok = True
    for c in a.clips:
        kind, _, name = c.partition(":")
        if kind == "palm":
            ok &= emit(palm_hold(), a.base, a.key, a.fps, a.quiet)
        elif kind == "namaste":
            ok &= emit(namaste_hold(), a.base, a.key, a.fps, a.quiet)
        elif kind == "wait":
            ok &= emit(synth.render_clip(None, absent=max(
                1, int(int(name or "300") / FRAME_MS))),
                a.base, a.key, a.fps, a.quiet)
        elif kind in ("sign", "teach"):
            if name in RR_PATHS:
                path = RR_PATHS[name]
            elif name in synth.sign_paths():
                path = synth.sign_paths()[name]
            else:
                path = synth.unknown_path(7)
            ok &= emit(fistify(synth.render_clip(
                        path, n_motion=18, lead=4,
                        tail=10, absent=10, jitter=JITTER)),
                       a.base, a.key, a.fps, a.quiet)
        else:
            print(f"unknown clip {c!r}", file=sys.stderr)
            return 2
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
