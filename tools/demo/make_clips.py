#!/usr/bin/env python3
"""make_clips.py — build + self-verify the demo kp42 clips.

Writes tools/demo/clips/*.json in the replay-server wire shape
  {"frames": [{"t_ms": .., "points": [{x,y,z?,score} x42]}, ...]}
(the replay service ignores t_ms — the page stamps wall time; a direct
/live/kp feeder uses it).

Clip set (all REPLAYED-keypoint evidence — synthetic coords, never video):
  palm-hold.json            held open palm        -> PALM-OPEN mode (listen)
  namaste.json              held palms together   -> NAMASTE mode (stop)
  idle.json                 hands-away frames     -> closes any open segment
  teach-<GLOSS>.json        3 takes, absent gaps  -> teach exemplars x3
  perform-<GLOSS>.json      one signing pass      -> match -> bound argv
  reject.json               untrained squiggle    -> honest rejection

The demo signs (CUSTOM motion-distinct paths — see SIGNS comment):
  OPEN-NOTES  = dome arc      -> touch marker           (tier 0)
  TYPE-HELLO  = two-hump wave -> type into SinkEdit     (tier 0)
  LOCK        = drawn square  -> touch marker, held for /act/confirm (tier 2)

Verify mode (default): every clip is fed through a FRESH server/endpointer
so the files on disk are proven to produce their intended events before
run_all ever touches a daemon.

  python3 make_clips.py [out_dir]        # write + verify
  python3 make_clips.py --verify-only    # verify existing files
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "e2e-live"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "server"))
import synth        # noqa: E402
import endpointer   # noqa: E402

FPS_MS = 1000.0 / 15.0

# the demo grammar: gloss -> centroid path. These are CUSTOM shapes, not
# the shared e2e grammar paths (swipe/circle/Z) — the realrun bank is a
# shared resource on this box and other lanes bank exemplars of the
# grammar signs (RR-SWIPE, DIM-LIGHTS-*, OPEN-BROWSER). Distinct shapes
# keep the DTW argmin honest: our performs can't land on their rows.
SIGNS = {
    # dome arc — open a notes app
    "OPEN-NOTES": [(0.25, 0.70), (0.35, 0.45), (0.65, 0.45), (0.75, 0.70)],
    # two-hump wave — "type hello"
    "TYPE-HELLO": [(0.20, 0.55), (0.35, 0.45), (0.50, 0.55), (0.65, 0.45),
                   (0.80, 0.55)],
    # a drawn square — "lock" (tier-2 confirm demo)
    "LOCK":       [(0.35, 0.38), (0.65, 0.38), (0.65, 0.68), (0.35, 0.68),
                   (0.35, 0.38)],
}

PALM = synth.PALM


def _hand(cx, cy, sc):
    return [{"x": cx + ox, "y": cy + oy, "z": 0.0, "score": sc}
            for ox, oy in PALM]


def _away(n):
    """Hands-away frames — the endpointer's cleanest segment closer."""
    return [{"t_ms": 0.0, "points":
             _hand(0.15, 0.82, 0.0) + _hand(0.5, 0.5, 0.0)}
            for _ in range(n)]


def _stamp(frames, t0=0.0):
    for i, f in enumerate(frames):
        f["t_ms"] = t0 + i * FPS_MS
    return frames


def palm_hold():
    """Open palm held ~1.1s (16 frames). ZERO jitter — a jittered 'still'
    hand reads >0.6 palm-widths/s to the endpointer's hold gate and the
    0.45s hold timer keeps resetting (verified against endpointer.py).
    Parked hand absent so only this palm can fire the mode event."""
    fr = [{"t_ms": 0.0, "points":
           _hand(0.15, 0.82, 0.0) + _hand(0.5, 0.52, 0.9)}
          for _ in range(16)]
    return _stamp(fr + _away(5))


def namaste():
    """Both palms together at center, held ~0.9s -> NAMASTE."""
    fr = [{"t_ms": 0.0, "points":
           _hand(0.485, 0.52, 0.9) + _hand(0.515, 0.52, 0.9)}
          for _ in range(14)]
    return _stamp(fr + _away(5))


def idle(n=14):
    """Bare hands-away clip — flushes a stale open segment between scenes."""
    return _stamp(_away(n))


def _take(path, seed):
    """One clean take: still lead-in, eased motion, still tail, hands away
    handled by the caller (take separators are explicit _away blocks)."""
    return synth.render_clip(path, n_motion=18, lead=4, tail=6, absent=0,
                             jitter=0.003, seed=seed, parked=False)


def teach(gloss):
    """3 takes of the gloss's path, separated by ~0.9s of hands-away so the
    endpointer emits one segment per take (daemon stores each as an
    exemplar; /live/state teach.got must reach 3)."""
    path = SIGNS[gloss]
    fr = _away(4)
    for k in (1, 2, 3):
        fr += _take(path, seed=k)
        fr += _away(12)
    return _stamp(fr)


def perform(gloss):
    """One performed pass — DIFFERENT seed + heavier jitter than the takes,
    so an accepted match proves generalization, not memorized frames.
    absent=14: the segment only closes after 0.35s of quiet — a jittered
    still tail keeps the EMA up, so the hands-away block needs the room."""
    path = SIGNS[gloss]
    fr = synth.render_clip(path, n_motion=20, lead=4, tail=8, absent=14,
                           jitter=0.005, seed=17, parked=False)
    return _stamp(_away(3) + fr)


def reject():
    """An untrained meander — must land in last_match with accepted=false."""
    fr = synth.render_clip(synth.unknown_path(7), n_motion=22, lead=4,
                           tail=6, absent=14, jitter=0.005, seed=99,
                           parked=False)
    return _stamp(_away(3) + fr)


def build_all():
    clips = {
        "palm-hold": palm_hold(),
        "namaste": namaste(),
        "idle": idle(),
        "reject": reject(),
    }
    for g in SIGNS:
        clips["teach-" + g] = teach(g)
        clips["perform-" + g] = perform(g)
    return clips


# --- self-verification: feed each clip through a real Endpointer -------
def _events(clip):
    ep = endpointer.Endpointer()
    out = []
    for f in clip:
        out += ep.feed({"t_ms": f["t_ms"], "points": f["points"]})
    return out


def verify(clips):
    """Returns list of (clip, ok, detail). Asserts event-level truth."""
    res = []
    ev = _events(clips["palm-hold"])
    n = [e for e in ev if e.get("type") == "mode"
         and e.get("name") == "PALM-OPEN"]
    res.append(("palm-hold", len(n) == 1, "%d PALM-OPEN" % len(n)))

    ev = _events(clips["namaste"])
    n = [e for e in ev if e.get("type") == "mode"
         and e.get("name") == "NAMASTE"]
    res.append(("namaste", len(n) == 1, "%d NAMASTE" % len(n)))

    ev = _events(clips["idle"])
    res.append(("idle", not [e for e in ev if e["type"] == "segment"],
                "no segments"))

    for g in SIGNS:
        ev = _events(clips["teach-" + g])
        segs = [e for e in ev if e["type"] == "segment"]
        res.append(("teach-" + g, len(segs) == 3,
                    "%d segments (want 3)" % len(segs)))
        ev = _events(clips["perform-" + g])
        segs = [e for e in ev if e["type"] == "segment"]
        res.append(("perform-" + g, len(segs) == 1,
                    "%d segments (want 1)" % len(segs)))
    ev = _events(clips["reject"])
    segs = [e for e in ev if e["type"] == "segment"]
    res.append(("reject", len(segs) == 1,
                "%d segments (want 1)" % len(segs)))
    return res


def write_all(clips, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name, frames in clips.items():
        with open(os.path.join(out_dir, name + ".json"), "w") as f:
            json.dump({"frames": frames}, f)


def main():
    out = os.path.join(_HERE, "clips")
    verify_only = "--verify-only" in sys.argv
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            out = a
    if verify_only:
        clips = {}
        for fn in sorted(os.listdir(out)):
            if fn.endswith(".json"):
                clips[fn[:-5]] = json.load(open(os.path.join(out, fn)))["frames"]
    else:
        clips = build_all()
    res = verify(clips)
    bad = 0
    for name, ok, detail in res:
        print("  %-4s %-22s %s" % ("ok" if ok else "FAIL", name, detail))
        bad += not ok
    if bad:
        print("make_clips: %d clip(s) failed self-verification" % bad)
        return 1
    if not verify_only:
        write_all(clips, out)
        print("wrote %d clips -> %s" % (len(clips), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
