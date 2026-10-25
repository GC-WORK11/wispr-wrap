"""synth.py — seeded synthetic keypoint generator for the live-eval harness.

Emits MediaPipe-style frames: two 21-point hands (42 keypoints total), each
point [x, y, score] in a virtual 640x480 frame — the shape a `/live/kp`
phone-path payload carries (PRD section 3: keypoints only, ~1KB/frame, no
pixels, no body). What is NOT simulated: pixel motion (no mscore — the Rust
app's stillness squash has no keypoint-path equivalent), z/depth, occlusion.

Determinism: every clip is a pure function of its seed. Speed warp, spatial
jitter, noisy frames, hand drops and performer placement are drawn from one
`random.Random(seed)` per clip; changing the seed changes the performance,
not the sign.

Two coordinate roles:
  - `flat` frame vectors (84 floats) feed motion_energy -> the endpointer;
  - per-hand score-weighted centroids + 19-dim descriptors feed the bank.
"""

import math
import random

WIDTH, HEIGHT = 640, 480
HAND_KP = 21

# ---------------------------------------------------------------------------
# Sign vocabulary — 10 motion-distinct control gestures (PRD north-star:
# "user-chosen, motion-distinct" — including deliberate near-neighbors
# CIRCLE-CW/CCW and SWIPE-R/L to stress the wrong-sign rate).
# Paths are functions t in [0,1] -> (x, y) in the unit box, y DOWN (image
# coords). `bow` adds a per-performance curvature so no two takes are the
# same arc.
# ---------------------------------------------------------------------------

def _circ(cw):
    def f(t, bow):
        th = -math.pi / 2 + (2 * math.pi * t) * (1 if cw else -1)
        return (0.5 + 0.42 * math.cos(th), 0.5 + 0.42 * math.sin(th))
    return f


def _tri(x):
    x = x % 1.0
    return 2 * x if x < 0.5 else 2 - 2 * x


SHAPES = {
    # gloss: (path(t, bow), handshape pose)
    "SWIPE-R":  (lambda t, b: (t, 0.5 + b * math.sin(math.pi * t)), "point"),
    "SWIPE-L":  (lambda t, b: (1 - t, 0.5 + b * math.sin(math.pi * t)), "point"),
    "SWIPE-DN": (lambda t, b: (0.5 + b * math.sin(math.pi * t), t), "point"),
    "SWIPE-UP": (lambda t, b: (0.5 + b * math.sin(math.pi * t), 1 - t), "point"),
    "CIRCLE-CW":  (_circ(True), "point"),
    "CIRCLE-CCW": (_circ(False), "point"),
    "ZIGZAG":   (lambda t, b: (t, 0.35 + 0.3 * _tri(t * 3) + 0.05 * b), "open"),
    "SHAKE":    (lambda t, b: (0.5 + 0.4 * math.sin(3 * 2 * math.pi * t),
                               0.5 + b * t), "open"),
    "V-DRAW":   (lambda t, b: ((0.15 + 0.7 * t,
                                0.2 + 1.6 * t) if t < 0.5 else
                               (0.15 + 0.7 * t, 1.8 - 1.6 * t)), "fist"),
    "HOOK":     (lambda t, b: ((0.15 + 0.9 * t, 0.3) if t < 0.75 else
                               (0.825 + 0.25 * math.sin((t - 0.75) / 0.25 * math.pi / 2),
                                0.3 + 0.4 * (1 - math.cos((t - 0.75) / 0.25 * math.pi / 2)))),
                 "point"),
    "PUSH":     (lambda t, b: (0.5 + b * 0.1,
                               0.6 - 0.5 * math.sin(math.pi * t)), "open"),
}

# Per-pose finger curl profiles (0=straight, 1=folded) thumb..pinky — only
# the fused-bank variant reads them; the centroid bank never sees shape.
POSES = {
    "open":  (0.1, 0.05, 0.05, 0.05, 0.1),
    "point": (0.3, 0.05, 0.75, 0.8, 0.85),
    "fist":  (0.6, 0.9, 0.9, 0.9, 0.9),
    "pinch": (0.5, 0.5, 0.85, 0.85, 0.9),
    "rest":  (0.4, 0.45, 0.5, 0.5, 0.55),
}


# ---------------------------------------------------------------------------
# Hand renderer — a plausible 21-pt hand around the wrist centroid. Not
# anatomy-grade; it exists so centroids, handshape gates and palm-width
# normalization see realistic geometry and noise.
# ---------------------------------------------------------------------------

# MCP layout around the palm (index..pinky x-offsets in palm widths,
# relative to middle-MCP), and per-finger segment lengths in palm widths.
_MCP_DX = (-0.55, 0.0, 0.5, 0.95)          # index, middle, ring, pinky
_FINGER_LEN = (0.75, 0.95, 0.85, 0.6)      # index, middle, ring, pinky
_THUMB_DIR = -1.05                          # rad off palm axis
_THUMB_LEN = 0.7


def render_hand(wrist, angle, scale, curls, rng, kp_noise):
    """21 keypoints for one hand. `wrist`=(x,y); `angle` = palm direction
    (wrist->middle-MCP axis); `scale` = palm width px; `curls` 5 values."""
    ca, sa = math.cos(angle), math.sin(angle)

    def rot(dx, dy):
        return (wrist[0] + (dx * ca - dy * sa) * scale,
                wrist[1] + (dx * sa + dy * ca) * scale)

    pts = [(wrist[0], wrist[1])]            # 0 wrist
    # thumb: angles spread off the palm axis, shorter
    ta = angle + _THUMB_DIR
    tx, ty = wrist[0], wrist[1]
    for k in range(4):                      # 1-4 thumb cmc..tip
        step = _THUMB_LEN * scale * (1 - 0.45 * curls[0]) / 3.0
        bend = 0.5 * curls[0] * k           # curl bends the joints
        tx += math.cos(ta + bend) * step
        ty += math.sin(ta + bend) * step
        pts.append((tx, ty))
    # four fingers: MCP at the knuckle row (~0.55 palm widths out), then
    # pip/dip/tip march along the palm axis, curling progressively —
    # 4 points per finger, MediaPipe layout.
    for f in range(4):
        mcp_dx = _MCP_DX[f]
        bx, by = rot(mcp_dx, 0.55)
        pts.append((bx, by))                # MCP
        fx, fy = bx, by
        flen = _FINGER_LEN[f] * (1 - 0.55 * curls[f + 1])
        for k in range(3):                  # pip, dip, tip
            bend = curls[f + 1] * (k + 1) * 0.55
            seg = flen / 3.0
            fa = angle + bend
            fx += math.cos(fa) * seg * scale
            fy += math.sin(fa) * seg * scale
            pts.append((fx, fy))
    # Already in MediaPipe order: [0 wrist, 1-4 thumb, 5-8 index,
    # 9-12 middle, 13-16 ring, 17-20 pinky].
    out = []
    for (x, y) in pts[:HAND_KP]:
        nx = x + rng.gauss(0, kp_noise)
        ny = y + rng.gauss(0, kp_noise)
        out.append([nx, ny, min(1.0, max(0.0, 0.97 - abs(rng.gauss(0, 0.04))))])
    assert len(out) == HAND_KP
    return out


# ---------------------------------------------------------------------------
# Frame + clip generators.
# ---------------------------------------------------------------------------

def _frame(t_ms, hand_a, hand_b):
    """A wire frame: two hands of 21 [x,y,score] or None when absent."""
    present = hand_a is not None or hand_b is not None
    flat = []
    for hand in (hand_a, hand_b):
        if hand is None:
            flat.extend([0.0] * (HAND_KP * 2))
        else:
            for p in hand:
                flat.extend((p[0], p[1]))
    return {"t_ms": int(t_ms), "present": present,
            "hand_a": hand_a, "hand_b": hand_b, "flat": flat}


def _still_frames(rng, t0, dur_ms, fps, wrist=None, pose="rest", noise=1.2,
                  absent=False, two=False):
    """Near-still frames: a resting hand (or none). Drift << jitter so the
    motion floor sees a still pose with realistic tracker noise."""
    n = max(1, int(dur_ms / 1000 * fps))
    frames = []
    wx, wy = wrist if wrist else (WIDTH * 0.62, HEIGHT * 0.6)
    for i in range(n):
        if absent:
            frames.append(_frame(t0 + i * 1000 / fps, None, None))
            continue
        wx += rng.gauss(0, 0.35)
        wy += rng.gauss(0, 0.35)
        a = -math.pi / 2 + rng.gauss(0, 0.05)
        ha = render_hand((wx, wy), a, 42.0, POSES[pose], rng, noise)
        hb = (render_hand((wx - 140, wy + 30), a + 0.15, 40.0, POSES[pose],
                          rng, noise) if two else None)
        frames.append(_frame(t0 + i * 1000 / fps, ha, hb))
    return frames


def perform(gloss, seed, fps=30, frame_wh=(WIDTH, HEIGHT),
            dur_ms=None, center=None, amp_scale=None, rot=None,
            kp_noise=1.2, drop_p=0.008, bad_p=0.005,
            lead_ms=(120, 320), tail_ms=(780, 980), two_handed=False,
            speed_a=None):
    """One performance of `gloss`: lead-in stillness, the sign, a still
    tail long enough for the 700ms still-hold to fire inside the clip.

    Returns (frames, meta) — meta carries the ground-truth motion window
    (sign_start_ms, sign_end_ms) the latency arm reads.
    """
    if gloss not in SHAPES:
        raise KeyError(f"unknown gloss {gloss}")
    rng = random.Random(seed)
    path_fn, pose = SHAPES[gloss]
    w, h = frame_wh
    dur = dur_ms if dur_ms else rng.uniform(380, 850)
    n = max(9, int(dur / 1000 * fps))
    # Speed warp u(t) = t + a·sin(2πt)/2π — monotonic for |a|<=1, stays the
    # "same sign done at a different pace" (resample-32 is arc-length, so
    # this mostly stresses the endpointer, not the DTW).
    a_warp = speed_a if speed_a is not None else rng.uniform(-0.55, 0.55)
    bow = rng.uniform(-0.12, 0.12)
    amp = amp_scale if amp_scale else rng.uniform(0.75, 1.25)
    rot = rot if rot is not None else math.radians(rng.uniform(-7, 7))
    cx = center[0] if center else rng.uniform(w * 0.30, w * 0.70)
    cy = center[1] if center else rng.uniform(h * 0.28, h * 0.72)
    sx = w * 0.42 * amp
    sy = h * 0.42 * amp
    cr, sr = math.cos(rot), math.sin(rot)
    # Execution wobble: smooth low-frequency offset on the wrist path.
    wob_f = rng.uniform(1.5, 3.0)
    wob_a = rng.uniform(0.010, 0.028)
    wob_p = rng.uniform(0, 2 * math.pi)
    scale = rng.uniform(38, 52)               # palm width px

    frames = []
    t0 = 0
    frames += _still_frames(rng, t0, rng.uniform(*lead_ms), fps,
                            absent=(rng.random() < 0.25), two=two_handed,
                            noise=kp_noise)
    t_sign0 = t0 + len(frames) * 1000 / fps
    # Wrist trajectory at frame cadence.
    state = {"wx": None, "wy": None}
    for i in range(n):
        t = i / (n - 1)
        u = t + a_warp * math.sin(2 * math.pi * t) / (2 * math.pi)
        u = min(max(u, 0.0), 1.0)
        px, py = path_fn(u, bow)
        px += wob_a * math.sin(2 * math.pi * wob_f * t + wob_p)
        py += wob_a * math.cos(2 * math.pi * wob_f * t + wob_p)
        px, py = (px - 0.5) * sx, (py - 0.5) * sy
        wx = cx + px * cr - py * sr
        wy = cy + px * sr + py * cr
        state["wx"], state["wy"] = wx, wy
        ang = -math.pi / 2 + rot + rng.gauss(0, 0.06)
        curls = [min(1, max(0, c + rng.gauss(0, 0.05))) for c in POSES[pose]]
        # Dropped-hand runs: the tracker loses the hand mid-sign.
        if rng.random() < drop_p:
            continue_hand = False
        else:
            continue_hand = True
        if not continue_hand:
            frames.append(_frame(t_sign0 + i * 1000 / fps, None, None))
            continue
        ha = render_hand((wx, wy), ang, scale, curls, rng, kp_noise)
        # "Bad" frames: a detection blow-up — large noise on every point.
        if rng.random() < bad_p:
            ha = render_hand((wx + rng.gauss(0, 30), wy + rng.gauss(0, 30)),
                             ang + rng.gauss(0, 0.6), scale, curls,
                             rng, kp_noise * 3)
        hb = (render_hand((wx - 130, wy + 40), ang + 0.2, scale * 0.95,
                          curls, rng, kp_noise) if two_handed else None)
        frames.append(_frame(t_sign0 + i * 1000 / fps, ha, hb))
    t_sign1 = t_sign0 + n * 1000 / fps
    frames += _still_frames(rng, t_sign1, rng.uniform(*tail_ms), fps,
                            wrist=(state["wx"], state["wy"]), two=two_handed,
                            noise=kp_noise)
    meta = {"gloss": gloss, "seed": seed, "n_sign_frames": n,
            "sign_start_ms": int(t_sign0), "sign_end_ms": int(t_sign1)}
    return frames, meta


# ---------------------------------------------------------------------------
# Idle-motion distractors — non-sign hand movement for the false-accept arm.
# Each yields a run of frames continuing a shared clock.
# ---------------------------------------------------------------------------

def _drift_to(rng, t0, fps, frm, to, dur_ms, pose, noise, scale=44.0,
              two=False):
    """Move a held hand smoothly (half-cosine) from `frm` to `to`."""
    n = max(2, int(dur_ms / 1000 * fps))
    out = []
    for i in range(n):
        t = i / (n - 1)
        u = 0.5 - 0.5 * math.cos(math.pi * t)
        wx = frm[0] + (to[0] - frm[0]) * u + rng.gauss(0, 0.5)
        wy = frm[1] + (to[1] - frm[1]) * u + rng.gauss(0, 0.5)
        a = -math.pi / 2 + rng.gauss(0, 0.06)
        ha = render_hand((wx, wy), a, scale, POSES[pose], rng, noise)
        hb = (render_hand((wx - 130, wy + 40), a + 0.2, scale * 0.95,
                          POSES[pose], rng, noise) if two else None)
        out.append(_frame(t0 + i * 1000 / fps, ha, hb))
    return out


def scrolling(rng, t0, fps, seconds, noise=1.2):
    """Phone-scrolling: present hand, repeated quick vertical flicks
    (down-strokes 25-60px over ~150-260ms) with still gaps between — the
    closest common motion to a taught swipe. A slow drag variant reaches
    the 250ms min-segment gate and is the dangerous case."""
    out = []
    t = t0
    end = t0 + seconds * 1000
    wx, wy = WIDTH * 0.6, HEIGHT * 0.55
    while t < end:
        gap = rng.uniform(400, 1400)
        out += _still_frames(rng, t, gap, fps, wrist=(wx, wy), noise=noise)
        t = out[-1]["t_ms"] + 1000 / fps
        if rng.random() < 0.72:
            # quick flick: short fast stroke — sub-250ms active
            dur, dpx = rng.uniform(140, 240), rng.uniform(28, 58)
        else:
            # slow drag: sustained move that can open a segment
            dur, dpx = rng.uniform(300, 520), rng.uniform(45, 95)
        dy = dpx * (1 if rng.random() < 0.8 else -1)
        seg = _drift_to(rng, t, fps, (wx, wy), (wx + rng.gauss(0, 8), wy + dy),
                        dur, "rest", noise)
        out += seg
        wy += dy
        wy = min(max(wy, HEIGHT * 0.2), HEIGHT * 0.85)
        t = out[-1]["t_ms"] + 1000 / fps
        # Return drift (slower, like settling the thumb back) — sometimes.
        if rng.random() < 0.5 and t < end:
            dur2 = rng.uniform(250, 450)
            seg = _drift_to(rng, t, fps, (wx, wy),
                            (wx + rng.gauss(0, 6), wy - dy * rng.uniform(0.3, 0.8)),
                            dur2, "rest", noise)
            out += seg
            wy -= dy * rng.uniform(0.3, 0.8)
            t = out[-1]["t_ms"] + 1000 / fps
    return out


def waving(rng, t0, fps, seconds, noise=1.2):
    """Hello-waving: a present hand oscillating laterally at ~1-2Hz for
    several seconds — sustained active motion, a SHAKE/ZIGZAG near-miss."""
    out = []
    end = t0 + seconds * 1000
    wx, wy = WIDTH * 0.55, HEIGHT * 0.5
    while t0 + len(out) * 1000 / fps < end:
        dur = rng.uniform(1600, 3800)
        n = max(3, int(dur / 1000 * fps))
        freq = rng.uniform(1.0, 2.2)
        ampl = rng.uniform(70, 130)
        phase = rng.uniform(0, 2 * math.pi)
        for i in range(n):
            tt = i * 1000 / fps
            px = wx + ampl * math.sin(2 * math.pi * freq * tt / 1000 + phase)
            py = wy + rng.gauss(0, 6)
            a = -math.pi / 2 + rng.gauss(0, 0.08)
            out.append(_frame(t0 + len(out) * 1000 / fps,
                              render_hand((px, py), a, 46.0, POSES["open"],
                                          rng, noise), None))
        # still gap between bouts
        gap = _still_frames(rng, t0 + len(out) * 1000 / fps,
                            rng.uniform(700, 1600), fps, wrist=(wx, wy),
                            noise=noise)
        out += gap
    return out


def fidget(rng, t0, fps, seconds, noise=1.2):
    """Adjusting/scratching: random smooth mid-amplitude arcs — continuous
    motion that opens segments and must be rejected on DTW distance."""
    out = []
    t = t0
    end = t0 + seconds * 1000
    wx, wy = WIDTH * 0.55, HEIGHT * 0.55
    while t < end:
        to = (rng.uniform(WIDTH * 0.15, WIDTH * 0.9),
              rng.uniform(HEIGHT * 0.15, HEIGHT * 0.9))
        seg = _drift_to(rng, t, fps, (wx, wy), to,
                        rng.uniform(350, 900), "rest", noise)
        out += seg
        wx, wy = to
        t = out[-1]["t_ms"] + 1000 / fps
        if rng.random() < 0.35:
            gap = _still_frames(rng, t, rng.uniform(300, 900), fps,
                                wrist=(wx, wy), noise=noise)
            out += gap
            t = out[-1]["t_ms"] + 1000 / fps
    return out


def resting(rng, t0, fps, seconds, noise=1.2, absent_p=0.3):
    """Hand at rest (or away entirely): slow drift under the motion
    floor — should never even open a segment."""
    out = []
    t = t0
    end = t0 + seconds * 1000
    while t < end:
        dur = rng.uniform(1500, 4500)
        absent = rng.random() < absent_p
        w = (rng.uniform(WIDTH * 0.2, WIDTH * 0.8),
             rng.uniform(HEIGHT * 0.45, HEIGHT * 0.85))
        seg = _still_frames(rng, t, dur, fps, wrist=w, absent=absent,
                            noise=noise)
        out += seg
        t = out[-1]["t_ms"] + 1000 / fps
    return out


DISTRACTORS = {"scrolling": scrolling, "waving": waving,
               "fidget": fidget, "resting": resting}


def idle_stream(seed, seconds, fps=30, noise=1.2):
    """A T-second stream of non-sign motion: distractor runs concatenated
    in a seeded order with still/absent gaps between."""
    rng = random.Random(seed)
    out = []
    t = 0
    end_ms = seconds * 1000
    names = list(DISTRACTORS)
    while t < end_ms:
        kind = rng.choice(names)
        dur = min(rng.uniform(4, 12), (end_ms - t) / 1000)
        out += DISTRACTORS[kind](rng, t, fps, dur, noise)
        t = out[-1]["t_ms"] + 1000 / fps
        gap = _still_frames(rng, t, rng.uniform(600, 1800), fps,
                            absent=(rng.random() < 0.4), noise=noise)
        out += gap
        t = out[-1]["t_ms"] + 1000 / fps
    return out
