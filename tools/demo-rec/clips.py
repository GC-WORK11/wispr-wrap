#!/usr/bin/env python3
"""clips.py — build the kp42 clips the demo drives into the page.

Reuses tools/e2e-live/synth.py's renderer so the demo exercises the same
motion grammar the e2e harness asserts. Two extra clip kinds are defined
here (not in synth): the held open palm that starts listening, and the
held palms-together that stops it — both real endpointer mode signs.

  parked=False everywhere: the e2e fixtures park a still open hand at
  score 0.45 which the endpointer would (correctly) read as a held palm;
  we want mode events only from the explicit palm/namaste clips.

Usage: python3 clips.py <out_dir>   # writes <name>.json {"frames":[...]}
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "e2e-live"))
import synth  # noqa: E402

FPS_MS = 1000.0 / 15.0


def _hand(cx, cy, sc, jitter=0.0, seed=0):
    import random
    rng = random.Random(seed)
    return [{"x": cx + ox + rng.uniform(-jitter, jitter),
             "y": cy + oy + rng.uniform(-jitter, jitter),
             "z": 0.0, "score": sc} for ox, oy in synth.PALM]


def _away(n=4):
    return [{"t_ms": 0.0, "points":
             _hand(0.15, 0.82, 0.0) + _hand(0.5, 0.5, 0.0)}
            for _ in range(n)]


def _stamp(frames, t0=0.0):
    for i, f in enumerate(frames):
        f["t_ms"] = t0 + i * FPS_MS
    return frames


def palm_open():
    """Held still open palm ~1s — endpointer fires PALM-OPEN at 0.45s."""
    fr = [{"t_ms": 0.0, "points":
           _hand(0.15, 0.82, 0.0) + _hand(0.5, 0.52, 0.9)}
          for _ in range(15)]
    return _stamp(fr + _away())


def namaste():
    """Both palms together, held ~0.8s — fires NAMASTE (stop listening)."""
    fr = [{"t_ms": 0.0, "points":
           _hand(0.485, 0.52, 0.9) + _hand(0.515, 0.52, 0.9)}
          for _ in range(13)]
    return _stamp(fr + _away())


def sign(name, seed=0, jitter=0.003):
    """A performed sign from the shared grammar (parked hand absent)."""
    path = synth.sign_paths()[name]
    return synth.render_clip(path, n_motion=16, lead=3, tail=4, absent=4,
                             jitter=jitter, seed=seed, parked=False)


def unknown(seed=7):
    """An untrained squiggle — must be rejected by the bank."""
    return synth.render_clip(synth.unknown_path(seed), n_motion=18,
                             lead=3, tail=4, absent=4, parked=False)


ALL = {"palm": palm_open, "namaste": namaste}


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/demo/clips"
    os.makedirs(out, exist_ok=True)
    clips = {"palm": palm_open(), "namaste": namaste()}
    for name in synth.sign_paths():
        for v in (1, 2, 3):
            clips["%s-take%d" % (name.lower(), v)] = sign(name, seed=v)
        clips["%s-perform" % name.lower()] = sign(name, seed=17)
    clips["unknown"] = unknown()
    for k, fr in clips.items():
        with open(os.path.join(out, k + ".json"), "w") as f:
            json.dump({"frames": fr}, f)
    print("wrote %d clips -> %s" % (len(clips), out))
