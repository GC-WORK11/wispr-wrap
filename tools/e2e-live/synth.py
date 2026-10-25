#!/usr/bin/env python3
"""synth — synthetic kp42 sign clips for the headless live-pipeline e2e.

A "clip" is a list of frames in the exact POST /live/kp shape the PRD
fixes: {"t_ms": <ms>, "points": [{"x","y","score"} x 42]} — two hands,
21 landmarks each, MediaPipe/COCO-WholeBody hand order (0 wrist;
1-4 thumb; 5-8 index; 9-12 middle; 13-16 ring; 17-20 pinky). Hand A
occupies indices 0..21, hand B (the ACTIVE signing hand here) 21..42.
Hand A sits parked at a corner at low score — realistic two-hand frames
that still leave the dominant hand unambiguous to any reasonable
score/motion hand picker.

The motion grammar is deliberately small and motion-DISTINCT — the same
honest scope matcher.rs claims (swipes, circle, push, shapes), not ASL.
Each sign renders with hold lead-in/tail and an optional hands-away
tail so an endpointer sees a clean segment however it cuts (motion
threshold, stillness, or disappearance).

Every clip also knows its truth: `truth_path()` returns the exact 2D
centroid trajectory the renderer moved through — used to seed banks and
to assert decisions without re-deriving centroids.
"""
import json
import math
import os
import random

HAND_KP = 21
FRAME_MS = 1000.0 / 15.0  # 15 Hz — the phone-camera cadence

# A fixed open-palm layout (x,y offsets from the wrist, ~0.10 tall palm —
# shape realism only needs to be plausible; the bank consumes the
# score-weighted centroid).
PALM = [
    (0.000, 0.000),                                    # 0 wrist
    (0.012, 0.010), (0.024, 0.014), (0.034, 0.018), (0.042, 0.022),   # thumb
    (0.012, -0.028), (0.018, -0.048), (0.021, -0.060), (0.023, -0.070),  # index
    (0.000, -0.034), (0.000, -0.056), (0.000, -0.068), (0.000, -0.078),  # middle
    (-0.012, -0.028), (-0.017, -0.048), (-0.020, -0.058), (-0.022, -0.064),  # ring
    (-0.022, -0.018), (-0.030, -0.028), (-0.036, -0.036), (-0.040, -0.042),  # pinky
]


def _path_point(path, u):
    """Evaluate a [(x,y)] polyline at arc position u in [0,1] by piece count
    (uniform index — shape detail, not timing fidelity)."""
    if not path:
        return (0.5, 0.5)
    pos = u * (len(path) - 1)
    i = min(int(pos), len(path) - 2)
    t = pos - i
    a, b = path[i], path[i + 1]
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _ease(u):
    """Smooth start/stop — signs don't move at constant velocity."""
    return u * u * (3.0 - 2.0 * u)


# --- the e2e motion grammar -------------------------------------------
def sign_paths():
    c = 2.0 * math.pi
    return {
        "SWIPE-R": [(0.20, 0.60), (0.80, 0.60)],
        "SWIPE-L": [(0.80, 0.60), (0.20, 0.60)],
        "PUSH-UP": [(0.50, 0.78), (0.50, 0.26)],
        "CIRCLE": [(0.50 + 0.22 * math.cos(c * i / 24),
                    0.55 + 0.22 * math.sin(c * i / 24)) for i in range(25)],
        "Z-SHAPE": [(0.25, 0.38), (0.75, 0.38), (0.25, 0.74), (0.75, 0.74)],
        "V-CHECK": [(0.25, 0.42), (0.50, 0.70), (0.75, 0.42)],
    }


def unknown_path(seed):
    """A meandering squiggle — must NEVER match a taught gloss."""
    rng = random.Random(seed)
    x, y = 0.5, 0.55
    pts = []
    for _ in range(26):
        x = min(max(x + rng.uniform(-0.09, 0.09), 0.1), 0.9)
        y = min(max(y + rng.uniform(-0.09, 0.09), 0.1), 0.9)
        pts.append((x, y))
    return pts


def truth_path(name, n=18, seed=0):
    """The raw centroid trajectory for a gloss (pre-normalization — what
    a daemon's centroid extractor should approximately observe)."""
    if name == "UNKNOWN":
        src = unknown_path(seed)
    else:
        src = sign_paths()[name]
    return [_path_point(src, _ease(i / (n - 1))) for i in range(n)]


def render_clip(path, n_motion=16, lead=4, tail=5, absent=3, jitter=0.003,
                seed=0, parked=True, score=0.9, t0_ms=0.0):
    """Render a centroid path into kp42 frames.

    Frames: `lead` still at path start, `n_motion` eased samples along the
    path, `tail` still at path end, `absent` frames with every score 0.
    Parked hand A included at score 0.45 (below the active hand's score)
    unless parked=False -> all hand-A scores 0.
    """
    rng = random.Random(seed)
    frames = []

    def hand(cx, cy, sc):
        return [{"x": cx + ox + rng.uniform(-jitter, jitter),
                 "y": cy + oy + rng.uniform(-jitter, jitter),
                 "score": sc} for ox, oy in PALM]

    def frame(cx, cy, active=True, away=False):
        # `away` models hands leaving the camera entirely — BOTH blocks at
        # score 0. (A parked second hand at 0.45 during ACTIVE frames is
        # the realistic distractor an endpointer must ignore; keeping it
        # parked during the away tail would look like a still hand on
        # screen and must not be what closes a segment.)
        pa = hand(0.15, 0.82, 0.0 if away else (0.45 if parked else 0.0))
        pb = hand(cx, cy, 0.0 if away else (score if active else 0.0))
        return {"t_ms": 0.0, "points": pa + pb}

    seq = []
    if path:
        p0, pN = path[0], path[-1]
        seq += [frame(p0[0], p0[1]) for _ in range(lead)]
        for i in range(n_motion):
            cx, cy = _path_point(path, _ease(i / (n_motion - 1)))
            seq.append(frame(cx, cy))
        seq += [frame(pN[0], pN[1]) for _ in range(tail)]
    seq += [frame(0.5, 0.5, away=True) for _ in range(absent)]
    for i, f in enumerate(seq):
        f["t_ms"] = t0_ms + i * FRAME_MS
    return seq


def synthetic_suite(seed=11):
    """teach dict + perform list for the canonical e2e:
    teach each grammar sign once (the daemon/side stores the exemplar),
    then perform each sign + one unknown that must be rejected."""
    rng = random.Random(seed)
    teach = {}
    perform = []
    names = list(sign_paths())
    for i, name in enumerate(names):
        teach[name] = render_clip(sign_paths()[name], n_motion=14, lead=3,
                                  tail=3, absent=2, seed=seed * 100 + i)
    for i, name in enumerate(names):
        # a DIFFERENT performance: slower, more jitter — same trajectory
        perform.append((name, render_clip(sign_paths()[name], n_motion=20,
                                          lead=5, tail=6, absent=3,
                                          jitter=0.005, seed=seed * 200 + i)))
    perform.append((None, render_clip(unknown_path(seed * 300),
                                      n_motion=22, lead=5, tail=6, absent=3,
                                      jitter=0.006, seed=seed * 300)))
    return teach, perform


# --- JSONL fixtures ------------------------------------------------------
# Line format (one clip per file):
#   {"kind":"meta","name":"SWIPE-R","expect":"SWIPE-R"|null}
#   {"kind":"frame","t_ms":66.7,"points":[{x,y,score} x42]}  (or a bare
#     {"t_ms":..,"points":..} line — same thing, kind optional)
#   {"kind":"end"}
# Keypoints are synthetic/derived coordinates — never video, never a
# person. Safe for this public repo.

def write_fixture(path, name, expect, frames):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(json.dumps({"kind": "meta", "name": name, "expect": expect}) + "\n")
        for fr in frames:
            f.write(json.dumps({"kind": "frame", **fr}) + "\n")
        f.write(json.dumps({"kind": "end"}) + "\n")


def read_fixture(path):
    """-> (expect_gloss|None, [frames]). Bare frame lines accepted; a file
    with no meta expects nothing (caller supplies the expectation)."""
    expect, frames = None, []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            kind = o.get("kind", "frame" if "points" in o else "?")
            if kind == "meta":
                expect = o.get("expect")
            elif kind == "frame":
                frames.append({"t_ms": o.get("t_ms", 0.0), "points": o["points"]})
    return expect, frames


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "fixtures")
    teach, perform = synthetic_suite()
    n = 0
    for name, frames in teach.items():
        write_fixture(os.path.join(out, f"teach-{name.lower()}.jsonl"), name, name, frames)
        n += 1
    for expect, frames in perform:
        nm = expect or "unknown"
        write_fixture(os.path.join(out, f"perform-{nm.lower()}.jsonl"), nm, expect, frames)
        n += 1
    print(f"wrote {n} fixture clips -> {out}")
