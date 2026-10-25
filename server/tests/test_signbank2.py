#!/usr/bin/env python3
"""Tests for signbank2.py - synthetic kp42 frames in NORMALIZED [0,1] coordinates (what the browser sends),
deterministic (seeded). Run: python3 -m unittest server/tests/test_signbank2.py   (needs numpy; skipped without it)

The first test class is the regression for the defect that motivated v2: with normalized coordinates the v1 engine
collapsed every trajectory to a point, so swipe-left and swipe-right (same hand shape) were indistinguishable.
"""
import math
import os
import random
import stat
import sys
import tempfile
import time
import unittest

try:
    import numpy as np
except ImportError:        # v2 needs numpy; the daemon then keeps the v1 bank
    raise unittest.SkipTest("numpy not installed")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # server/
import signbank2 as sb  # noqa: E402

# a plausible open hand, in palm lengths, wrist at the origin, fingers pointing up (y negative = up)
_BASE = {
    0: (0.0, 0.0),
    1: (-0.35, -0.35), 2: (-0.6, -0.65), 3: (-0.8, -0.9), 4: (-0.95, -1.15),
    5: (-0.40, -0.95), 6: (-0.42, -1.35), 7: (-0.43, -1.65), 8: (-0.44, -1.95),
    9: (-0.10, -1.00), 10: (-0.11, -1.45), 11: (-0.11, -1.8), 12: (-0.11, -2.15),
    13: (0.20, -0.95), 14: (0.22, -1.35), 15: (0.23, -1.62), 16: (0.24, -1.9),
    17: (0.45, -0.85), 18: (0.49, -1.15), 19: (0.51, -1.38), 20: (0.53, -1.6),
}


def hand(cx, cy, palm=0.07, curl=0.0, tilt=0.0, aspect=16 / 9):
    """21 keypoints {x,y,score} in normalized image coords. curl 0 = open, 1 = fist; tilt rotates the hand (rad)."""
    pts = []
    ca, sa = math.cos(tilt), math.sin(tilt)
    for i in range(21):
        x, y = _BASE[i]
        if i in (4, 8, 12, 16, 20) or i in (3, 7, 11, 15, 19) or i in (2, 6, 10, 14, 18):
            # fold the finger segments toward the palm
            k = 1.0 - 0.55 * curl * (1.0 if i % 4 == 0 else 0.7 if i % 4 == 3 else 0.4)
            x, y = x * (0.4 + 0.6 * k), y * k
        rx, ry = x * ca - y * sa, x * sa + y * ca
        pts.append({"x": cx + rx * palm / aspect, "y": cy + ry * palm, "score": 0.95})
    return pts


def absent():
    return [{"x": 0.0, "y": 0.0, "score": 0.0} for _ in range(21)]


def gesture(kind, rng, n=22, jitter=0.0012, shape="open", t0=0, fps=15.0, scale=1.0, shift=(0.0, 0.0)):
    """A gesture path for the RIGHT hand (slot 1), left hand absent. Normalized coordinates."""
    frames = []
    curl = {"open": 0.0, "fist": 0.9, "half": 0.5}[shape]
    for k in range(n):
        u = k / (n - 1)
        if kind == "swipe-r":
            x, y = 0.30 + 0.40 * u, 0.50
        elif kind == "swipe-l":
            x, y = 0.70 - 0.40 * u, 0.50
        elif kind == "circle":
            x, y = 0.50 + 0.16 * math.sin(2 * math.pi * u), 0.50 - 0.16 * math.cos(2 * math.pi * u) * 1.6
        elif kind == "up":
            x, y = 0.50, 0.75 - 0.45 * u
        elif kind == "down":
            x, y = 0.50, 0.30 + 0.45 * u
        elif kind == "zigzag":
            x, y = 0.30 + 0.40 * u, 0.50 + 0.12 * math.sin(6 * math.pi * u)
        elif kind == "hold":
            x, y = 0.50, 0.50
        else:
            raise ValueError(kind)
        x = 0.5 + (x - 0.5) * scale + shift[0]
        y = 0.5 + (y - 0.5) * scale + shift[1]
        x += rng.gauss(0, jitter)
        y += rng.gauss(0, jitter)
        pts = absent() + hand(x, y, curl=curl)
        for p in pts[21:]:
            p["x"] += rng.gauss(0, jitter / 3)
            p["y"] += rng.gauss(0, jitter / 3)
        frames.append({"t_ms": int(t0 + k * 1000.0 / fps), "points": pts})
    return frames


def teach(bank, gloss, kind, seed, takes=3, **kw):
    rng = random.Random(seed)
    for _ in range(takes):
        assert bank.add_exemplar(gloss, gesture(kind, rng, n=rng.randint(19, 26), **kw)), bank.last_issue


class TestNormalizedCoordinates(unittest.TestCase):
    """The regression: motion must matter when the coordinates are normalized to [0,1]."""

    def test_swipes_with_the_same_hand_are_distinguished(self):
        b = sb.SignBank()
        teach(b, "RIGHT", "swipe-r", 1)
        teach(b, "LEFT", "swipe-l", 2)
        rng = random.Random(99)
        for kind, want in (("swipe-r", "RIGHT"), ("swipe-l", "LEFT")):
            m = b.match(gesture(kind, rng))
            self.assertEqual(m.gloss, want, m)
            self.assertTrue(m.accepted, m)
            self.assertLess(m.dist, 1.0)

    def test_direction_not_in_the_bank_is_not_taken_for_the_taught_one(self):
        b = sb.SignBank()
        teach(b, "RIGHT", "swipe-r", 1)
        teach(b, "UP", "up", 2)
        rng = random.Random(5)
        m = b.match(gesture("swipe-l", rng))
        self.assertFalse(m.accepted, m)

    def test_unrelated_movement_is_rejected(self):
        b = sb.SignBank()
        for i, kind in enumerate(("swipe-r", "swipe-l", "circle", "up")):
            teach(b, kind.upper(), kind, 10 + i)
        rng = random.Random(7)
        m = b.match(gesture("zigzag", rng))
        self.assertFalse(m.accepted, m)

    def test_a_held_pose_is_not_a_sign(self):
        b = sb.SignBank()
        teach(b, "RIGHT", "swipe-r", 1)
        teach(b, "LEFT", "swipe-l", 2)
        m = b.match(gesture("hold", random.Random(3)))
        self.assertFalse(m.accepted, m)

    def test_position_in_frame_does_not_matter(self):
        b = sb.SignBank()
        teach(b, "RIGHT", "swipe-r", 1)
        teach(b, "LEFT", "swipe-l", 2)
        m = b.match(gesture("swipe-r", random.Random(4), shift=(-0.08, 0.10)))
        self.assertEqual((m.gloss, m.accepted), ("RIGHT", True), m)

    def test_handshape_separates_same_motion(self):
        b = sb.SignBank()
        teach(b, "OPEN-SWIPE", "swipe-r", 1, shape="open")
        teach(b, "FIST-SWIPE", "swipe-r", 2, shape="fist")
        rng = random.Random(8)
        self.assertEqual(b.match(gesture("swipe-r", rng, shape="fist")).gloss, "FIST-SWIPE")
        self.assertEqual(b.match(gesture("swipe-r", rng, shape="open")).gloss, "OPEN-SWIPE")


class TestOpenSetGates(unittest.TestCase):
    def test_two_nearly_identical_signs_abstain_instead_of_guessing(self):
        b = sb.SignBank()
        teach(b, "A", "swipe-r", 1, takes=3)
        teach(b, "B", "swipe-r", 1, takes=3)       # the same performances taught under another name
        m = b.match(gesture("swipe-r", random.Random(6)))
        self.assertFalse(m.accepted, m)
        self.assertEqual(m.reason, "too-close")

    def test_conflict_check_warns_before_the_sign_is_saved(self):
        b = sb.SignBank()
        teach(b, "NAMASTE", "swipe-r", 1)
        teach(b, "GOVINDA", "swipe-r", 2)
        c = b.check("GOVINDA")
        self.assertEqual(c["other"], "NAMASTE")
        self.assertEqual(c["level"], "conflict", c)
        teach(b, "CIRCLE", "circle", 3)
        self.assertEqual(b.check("CIRCLE")["level"], "ok")

    def test_everyday_movement_recording_vetoes_lookalikes(self):
        b = sb.SignBank()
        teach(b, "UP", "up", 1)
        teach(b, "CIRCLE", "circle", 2)
        rng = random.Random(11)
        idle = []
        for k in range(4):                             # a recording of casual hand movement (zigzag-ish)
            idle += gesture("zigzag", rng, n=24, t0=len(idle) * 70)
        self.assertGreater(b.add_none(idle), 0)
        m = b.match(gesture("zigzag", random.Random(12)))
        self.assertFalse(m.accepted, m)

    def test_thin_or_empty_input_is_refused_not_crashing(self):
        b = sb.SignBank()
        self.assertEqual(b.match([]).reason, "empty-bank")
        teach(b, "UP", "up", 1)
        self.assertEqual(b.match([]).reason, "thin-query")
        nohands = [{"t_ms": i * 66, "points": absent() + absent()} for i in range(20)]
        self.assertFalse(b.add_exemplar("Y", nohands))
        self.assertIn("no hands", b.last_issue)


class TestBankContract(unittest.TestCase):
    def test_list_set_action_remove_and_fifo(self):
        b = sb.SignBank()
        teach(b, "UP", "up", 1, takes=7)
        row = b.list()[0]
        self.assertEqual((row["gloss"], row["n_exemplars"]), ("UP", sb.MAX_EXEMPLARS))
        self.assertTrue(b.set_action("up", label="go up", action={"argv": ["true"], "tier": 1}))
        self.assertEqual(b.list()[0]["tier"], 1)
        self.assertTrue(b.remove("UP"))
        self.assertEqual(b.list(), [])

    def test_save_load_roundtrip_is_identical_and_private(self):
        b = sb.SignBank()
        teach(b, "RIGHT", "swipe-r", 1)
        teach(b, "LEFT", "swipe-l", 2)
        b.add_none(gesture("zigzag", random.Random(2), n=40))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "signbank.json")
            b.save(path)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            b2 = sb.SignBank.load(path)
        q = gesture("swipe-l", random.Random(40))
        m1, m2 = b.match(q), b2.match(q)
        self.assertEqual((m1.gloss, m1.accepted), (m2.gloss, m2.accepted))
        self.assertAlmostEqual(m1.dist, m2.dist, places=2)

    def test_old_format_is_refused_with_a_clear_message(self):
        with self.assertRaises(ValueError) as cm:
            sb.SignBank.from_dict({"format": "wf-signbank/1", "templates": []})
        self.assertIn("v1", str(cm.exception))

    def test_missing_file_is_an_empty_bank(self):
        with tempfile.TemporaryDirectory() as d:
            b = sb.SignBank.load(os.path.join(d, "nope.json"))
        self.assertEqual(b.list(), [])


class TestTimingAndUnits(unittest.TestCase):
    def test_same_gesture_at_30fps_and_15fps_reads_the_same(self):
        b = sb.SignBank()
        teach(b, "RIGHT", "swipe-r", 1)
        teach(b, "LEFT", "swipe-l", 2)
        m15 = b.match(gesture("swipe-r", random.Random(5), n=22, fps=15.0))
        m30 = b.match(gesture("swipe-r", random.Random(5), n=44, fps=30.0))
        self.assertEqual((m15.gloss, m15.accepted), ("RIGHT", True))
        self.assertEqual((m30.gloss, m30.accepted), ("RIGHT", True), m30)

    def test_pixel_coordinates_are_recognised_as_such(self):
        b = sb.SignBank()
        teach(b, "RIGHT", "swipe-r", 1)
        teach(b, "LEFT", "swipe-l", 2)
        q = gesture("swipe-l", random.Random(3))
        for fr in q:                                   # the same gesture in a 1280x720 pixel frame, isotropic
            for p in fr["points"]:
                p["x"] *= 1280.0 * (9 / 16) * (16 / 9) / (16 / 9)
                p["y"] *= 720.0
        # x was normalized by the width; convert faithfully to isotropic pixels
        q = gesture("swipe-l", random.Random(3))
        for fr in q:
            for p in fr["points"]:
                p["x"] *= 1280.0
                p["y"] *= 720.0
        m = b.match(q)
        self.assertEqual(m.gloss, "LEFT", m)

    def test_vectorised_dtw_equals_a_plain_reference(self):
        """dtw_many pinned to a straightforward symmetric-2 banded DP (the Rust dtw_cost) on random sequences."""
        rng = np.random.default_rng(5)

        def mk(n):
            f = np.zeros((n, sb.DIM))
            for h in range(2):
                o = h * sb.HAND
                f[:, o + sb.O_POS_OK] = 1.0
                f[:, o + sb.O_X:o + sb.O_Y + 1] = rng.normal(0, 0.5, (n, 2))
                f[:, o + sb.O_VX:o + sb.O_VY + 1] = rng.normal(0, 0.3, (n, 2))
                f[:, o + sb.O_SHAPE_OK] = (rng.random(n) > 0.2).astype(float)
                f[:, o + sb.O_SHAPE:o + sb.O_SHAPE + sb.SHAPE] = rng.normal(0, 0.4, (n, sb.SHAPE)) * f[:, [o + sb.O_SHAPE_OK]]
            f[::7, 0] = 0.0                             # a frame without the first hand
            return f

        def fd(a, b):
            acc = 0.0
            for h in range(2):
                o = h * sb.HAND
                pa, pb = a[o] > 0.5, b[o] > 0.5
                if pa and pb:
                    acc += sb.W_POS * ((a[o + 1] - b[o + 1]) ** 2 + (a[o + 2] - b[o + 2]) ** 2)
                    acc += sb.W_VEL * ((a[o + 3] - b[o + 3]) ** 2 + (a[o + 4] - b[o + 4]) ** 2)
                    sa, s2 = a[o + 5] > 0.5, b[o + 5] > 0.5
                    if sa and s2:
                        acc += sb.W_SHAPE * float(((a[o + 6:o + 6 + sb.SHAPE] - b[o + 6:o + 6 + sb.SHAPE]) ** 2).sum()) / sb.SHAPE
                    elif sa != s2:
                        acc += sb.P_SHAPE
                elif pa != pb:
                    acc += sb.P_HAND
            return math.sqrt(acc)

        def ref(q, t):
            n, m = len(q), len(t)
            bnd = max(int(math.ceil(sb.BAND * max(n, m))), 2)
            INF = float("inf")
            D = [[INF] * m for _ in range(n)]
            for i in range(n):
                jc = int(round(i * (m - 1) / (n - 1))) if n > 1 else 0
                for j in range(max(jc - bnd, 0), min(jc + bnd, m - 1) + 1):
                    c = fd(q[i], t[j])
                    if i == 0 and j == 0:
                        D[i][j] = 2 * c
                        continue
                    best = INF
                    if i > 0 and j > 0:
                        best = D[i - 1][j - 1] + 2 * c
                    if i > 0:
                        best = min(best, D[i - 1][j] + c)
                    if j > 0:
                        best = min(best, D[i][j - 1] + c)
                    D[i][j] = best
            return D[n - 1][m - 1] / (n + m)

        q = mk(23)
        tpls = [mk(n) for n in (18, 23, 30, 41)]
        got = sb.dtw_many(q, tpls)
        for g, t in zip(got, tpls):
            self.assertAlmostEqual(float(g), ref(q, t), places=6)

    def test_decision_is_fast_enough_for_a_twelve_sign_bank(self):
        b = sb.SignBank()
        kinds = ["swipe-r", "swipe-l", "circle", "up", "down", "zigzag"]
        for i, k in enumerate(kinds):
            for shape in ("open", "fist"):
                teach(b, f"{k}-{shape}".upper(), k, 100 + i, shape=shape)
        q = gesture("circle", random.Random(1))
        b.match(q)                                      # warm the cache
        t0 = time.perf_counter()
        for _ in range(10):
            b.match(q)
        ms = 100 * (time.perf_counter() - t0)
        print(f"\n[signbank2] 12-sign x3-take bank: match {ms:.1f} ms")
        self.assertLess(ms, 120.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
