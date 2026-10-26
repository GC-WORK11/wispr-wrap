#!/usr/bin/env python3
"""clips.py — synthetic hand-motion clips for the Go Live proofs (no video, no people).

Writes <out>/<name>.json as {"frames": [{"t_ms", "points": [{x,y,z,score} x42]}]},
the shape the page's test seam (window.WFFakeTrack) plays into the real /live/kp.

Signs (custom centroid paths, same hand shape; the trajectory tells them apart):
  HELLO     two-hump wave        Text: "hello"
  WORLD     a check mark         Text: "world"
  TERMINAL  dome arc             Control: open the Terminal app
  CLOSE     drawn square         Control: close the Terminal window (needs your OK)
Plus palm-hold (listen), namaste (pause), idle and an untaught meander (reject).

Self-check (default): every clip goes through a FRESH server/endpointer, and the
four signs are taught x3 into a fresh server/signbank and matched, so the files
are proven to do what the proof expects before a browser ever plays them.

  python3 clips.py <out_dir>
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools", "e2e-live"))
sys.path.insert(0, os.path.join(ROOT, "tools", "demo"))
sys.path.insert(0, os.path.join(ROOT, "server"))
import synth        # noqa: E402
import make_clips   # noqa: E402  (palm_hold, namaste, idle, reject, _away, _stamp)
import endpointer   # noqa: E402
import signbank     # noqa: E402

SIGNS = {
    "HELLO":    [(0.20, 0.55), (0.35, 0.45), (0.50, 0.55), (0.65, 0.45), (0.80, 0.55)],
    "WORLD":    [(0.30, 0.42), (0.44, 0.66), (0.72, 0.32)],
    "TERMINAL": [(0.25, 0.70), (0.35, 0.45), (0.65, 0.45), (0.75, 0.70)],
    "CLOSE":    [(0.35, 0.38), (0.65, 0.38), (0.65, 0.68), (0.35, 0.68), (0.35, 0.38)],
}


def teach(path):
    fr = make_clips._away(4)
    for k in (1, 2, 3):
        fr += synth.render_clip(path, n_motion=18, lead=4, tail=6, absent=0,
                                jitter=0.003, seed=k, parked=False)
        fr += make_clips._away(12)
    return make_clips._stamp(fr)


def perform(path, seed=17):
    fr = synth.render_clip(path, n_motion=20, lead=4, tail=8, absent=14,
                           jitter=0.005, seed=seed, parked=False)
    return make_clips._stamp(make_clips._away(3) + fr)


def build():
    # hands-away first: an open palm already resting in view is not a
    # presentation (endpointer rest-palm rule), a palm that ENTERS is
    clips = {"palm-hold": make_clips._stamp(make_clips._away(4) + make_clips.palm_hold()),
             "namaste": make_clips.namaste(),
             "idle": make_clips.idle(), "reject": make_clips.reject()}
    for g, p in SIGNS.items():
        clips["teach-" + g] = teach(p)
        clips["perform-" + g] = perform(p)
    return clips


def segments(frames):
    ep = endpointer.Endpointer()
    out = []
    for f in frames:
        out += ep.feed({"t_ms": f["t_ms"], "points": f["points"]})
    return out


def verify(clips):
    ok = True
    bank = signbank.SignBank()
    for g in SIGNS:
        segs = [e for e in segments(clips["teach-" + g]) if e["type"] == "segment"]
        good = len(segs) == 3
        ok &= good
        print(("ok  " if good else "BAD ") + "teach-%s: %d segments (want 3)" % (g, len(segs)))
        for s in segs:
            bank.add_exemplar(g, s["frames"], g.lower(), None)
    for g in SIGNS:
        segs = [e for e in segments(clips["perform-" + g]) if e["type"] == "segment"]
        if len(segs) != 1:
            ok = False
            print("BAD perform-%s: %d segments (want 1)" % (g, len(segs)))
            continue
        m = bank.match(segs[0]["frames"])
        good = bool(m and m.accepted and m.gloss == g)
        ok &= good
        print(("ok  " if good else "BAD ") + "perform-%s -> %s d=%.3f accepted=%s" % (
            g, m.gloss if m else None, m.dist if m else -1, m.accepted if m else None))
    segs = [e for e in segments(clips["reject"]) if e["type"] == "segment"]
    m = bank.match(segs[0]["frames"]) if segs else None
    good = bool(segs) and not (m and m.accepted)
    ok &= good
    print(("ok  " if good else "BAD ") + "reject -> %s accepted=%s" % (m.gloss if m else None, m.accepted if m else None))
    for name, want in (("palm-hold", "PALM-OPEN"), ("namaste", "NAMASTE")):
        n = [e for e in segments(clips[name]) if e.get("type") == "mode" and e.get("name") == want]
        good = len(n) == 1
        ok &= good
        print(("ok  " if good else "BAD ") + "%s: %d %s" % (name, len(n), want))
    return ok


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/wf-live/clips"
    clips = build()
    os.makedirs(out, exist_ok=True)
    for name, frames in clips.items():
        with open(os.path.join(out, name + ".json"), "w") as f:
            json.dump({"frames": frames}, f)
    ok = verify(clips)
    print("clips ->", out, "| self-check", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
