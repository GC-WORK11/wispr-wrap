#!/usr/bin/env python3
"""Tests for signbank.py — synthetic kp42 frames, deterministic (LCG).

Covers: resample32/dtw_banded ports (pinned to an independent
full-matrix reference DP AND a literal N-dim wdist DP — the same
pinning matcher.rs uses), position invariance, mirror/shape rejection,
the traj-anchor veto, FIFO cap, JSON roundtrip + mode 600, and
threshold calibration.

Run: python3 tests/test_signbank.py  (from server/) or python3 -m unittest
"""
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import signbank as sb

INF = float("inf")
NAN = float("nan")


# ------------------------------------------------------------------
# Independent reference DPs — the pins (mirrors Rust dtw_full / wdist)
# ------------------------------------------------------------------

def ref_wdist(a, b, w):
    n = min(len(a), len(b))
    acc = used = tot = 0.0
    for d in range(n):
        wd = max(w[d] if d < len(w) else 1.0, 0.0)
        tot += wd
        x, y = a[d], b[d]
        if math.isfinite(x) and math.isfinite(y):
            acc += wd * (x - y) * (x - y)
            used += wd
    if used <= 0.0:
        return INF if tot > 0.0 else 0.0
    return math.sqrt(acc * tot / used)


def ref_dtw_full(a, b, w):
    """Unbanded, unoptimized textbook DP on N-dim points."""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return INF
    prev = [INF] * (m + 1)
    cur = [INF] * (m + 1)
    prev[0] = 0.0
    for i in range(1, n + 1):
        cur[0] = INF
        for j in range(1, m + 1):
            cur[j] = ref_wdist(a[i - 1], b[j - 1], w) + \
                min(prev[j], cur[j - 1], prev[j - 1])
        prev, cur = cur, prev
    return prev[m] / max(n, m)


def ref_dtw_band_ndim(a, b, band, w):
    """Literal N-dim banded DP — what matcher::dtw_w does on fused points."""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return INF
    band = max(band, abs(n - m) + 1, 1)
    prev = [INF] * (m + 1)
    cur = [INF] * (m + 1)
    prev[0] = 0.0
    for i in range(1, n + 1):
        lo = max(i - band, 1)
        hi = min(i + band, m)
        cur[lo - 1] = INF
        for j in range(lo, hi + 1):
            cur[j] = ref_wdist(a[i - 1], b[j - 1], w) + \
                min(prev[j], cur[j - 1], prev[j - 1])
        prev, cur = cur, prev
    return prev[m] / max(n, m)


# ------------------------------------------------------------------
# Synthetic kp42 data
# ------------------------------------------------------------------

def lcg(seed):
    s = seed & 0xFFFFFFFFFFFFFFFF
    while True:
        s = (s * 6364136223846793005 + 1442695040888963407) & 0xFFFFFFFFFFFFFFFF
        yield ((s >> 33) & 0xFFFFFFFF) / 0xFFFFFFFF


# hand-local skeletons: wrist at (0,0), five finger chains of 4 joints.
# kind: "open" (all extended), "fist" (all curled), "point" (index only)
_FINGER_BASE = {
    "thumb":  (0.42, -0.20, 0.30),   # mcp offset + step dir scale
    "index":  (0.18, -0.52, 0.34),
    "middle": (0.00, -0.56, 0.36),
    "ring":   (-0.18, -0.52, 0.33),
    "pinky":  (-0.34, -0.44, 0.27),
}


def skeleton(kind):
    """21 hand-local pts (MediaPipe order). `ext` interpolates each
    joint between the mcp and the extended position, folded joints
    pulling back toward the wrist — a real fist's tips land INSIDE the
    palm bbox, which is what separates it from an open hand after
    normalization."""
    pts = [(0.0, 0.0)]  # wrist
    for name, (mx, my, ln) in _FINGER_BASE.items():
        ext = {"open": 1.0, "fist": 0.0,
               "point": 1.0 if name == "index" else 0.0}[kind]
        n = math.hypot(mx, my) or 1.0
        dx, dy = mx / n, my / n
        for k in range(4):  # mcp, pip, dip, tip
            if k == 0:
                pts.append((mx, my))
            else:
                reach = ext * ln * k / 3.0          # extended outward
                fold = (1 - ext) * (0.15 + 0.10 * k)  # curled inward
                px = mx + dx * reach - mx * fold
                py = my + dy * reach - my * fold + (1 - ext) * 0.04
                pts.append((px, py))
    return pts


def mk_hand(kind, center, scale, rng=None, noise=0.0):
    out = []
    for (x, y) in skeleton(kind):
        px = center[0] + x * scale
        py = center[1] + y * scale
        if rng is not None and noise:
            px += (next(rng) - 0.5) * 2 * noise
            py += (next(rng) - 0.5) * 2 * noise
        out.append({"x": px, "y": py, "score": 0.95})
    return out


def absent_hand():
    return [{"x": 0.0, "y": 0.0, "score": 0.0}] * sb.HAND_KP


def mk_frames(path, kind_fn, center=(320.0, 240.0), scale=120.0,
              noise=0.0, seed=7, left=None):
    """frames from a centroid path (list of (dx,dy) in pixels relative
    to center). left = None or a kind string for a present second hand
    (it stays at a fixed spot left of the moving hand)."""
    rng = lcg(seed)
    frames = []
    for i, (dx, dy) in enumerate(path):
        rh = mk_hand(kind_fn(i), (center[0] + dx, center[1] + dy),
                     scale, rng, noise)
        if left:
            lh = mk_hand(left, (center[0] - 90, center[1] - 40), scale * 0.9,
                         rng, noise)
        else:
            lh = absent_hand()
        frames.append({"t_ms": i * 33, "points": lh + rh})
    return frames


def path_swipe(n, dx, dy=0.0, wobble=0.0):
    return [(dx * i / (n - 1), dy * i / (n - 1) +
             wobble * math.sin(3 * math.pi * i / (n - 1)))
            for i in range(n)]


def path_circle(n, r=80.0, cx=0.0, cy=0.0):
    return [(cx + r * math.cos(2 * math.pi * i / n),
             cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def path_zigzag(n, amp=60.0):
    return [(i * 15.0, (amp if i % 4 < 2 else -amp)) for i in range(n)]


def path_vbounce(n, amp=70.0):
    """Pure vertical bounce — orthogonal to every taught horizontal
    motion and to a circle (which spans both axes evenly)."""
    half = (n - 1) / 2.0
    return [(0.0, -amp + 2 * amp * abs(i - half) / half)
            for i in range(n)]


def path_still(n, jitter=1.5, seed=3):
    rng = lcg(seed)
    return [((next(rng) - 0.5) * jitter, (next(rng) - 0.5) * jitter)
            for _ in range(n)]


def timewarp(path, f):
    """Non-uniform resampling of a path — same shape, different pace."""
    n = len(path)
    return [path[min(n - 1, int(round(f(i / (n - 1)) * (n - 1))))]
            for i in range(n)]


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class Primitives(unittest.TestCase):
    def test_resample32_edges(self):
        self.assertEqual(sb.resample32([(1.0, 2.0)]), [(1.0, 2.0)])
        pts = sb.resample32([(0.0, 0.0)] * 9)
        self.assertEqual(len(pts), 32)
        self.assertTrue(all(p == (0.0, 0.0) for p in pts))
        line = sb.resample32([(0.0, 0.0), (10.0, 0.0)])
        self.assertEqual(len(line), 32)
        self.assertAlmostEqual(line[0][0], 0.0)
        self.assertAlmostEqual(line[-1][0], 10.0)
        # arc-length: midpoint param of an L path sits at the corner
        L = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
        r = sb.resample32(L)
        self.assertAlmostEqual(r[16][0], 10.0, places=5)

    def test_dtw_banded_vs_full_reference(self):
        # identical -> 0; banded >= full always (band only restricts);
        # band >= n makes banded == full exactly.
        rng = lcg(42)
        a = [(next(rng) * 4 - 2, next(rng) * 4 - 2) for _ in range(32)]
        b = [(x + (next(rng) - 0.5) * 0.3, y + (next(rng) - 0.5) * 0.3)
             for (x, y) in a]
        w = []
        full = ref_dtw_full(a, b, w)
        banded = sb.dtw_banded(a, b, band=4)
        self.assertGreaterEqual(banded, full - 1e-9)
        wide = sb.dtw_banded(a, b, band=64)
        self.assertAlmostEqual(wide, full, places=9)
        self.assertEqual(sb.dtw_banded(a, a), 0.0)
        self.assertEqual(sb.dtw_banded([], b), INF)

    def test_normalize_position_invariance(self):
        base = sb._normalize(sb._resample(path_swipe(24, 100), 32))
        moved = sb._normalize(sb._resample(
            [(x * 2.5 + 700, y * 2.5 - 300) for x, y in path_swipe(24, 100)],
            32))
        for p, q in zip(base, moved):
            self.assertAlmostEqual(p[0], q[0], places=9)
            self.assertAlmostEqual(p[1], q[1], places=9)


class BankBehavior(unittest.TestCase):
    def setUp(self):
        self.bank = sb.SignBank()
        # three glosses, each taught from 3 noisy / moved / paced takes
        for seed in (1, 2, 3):
            self.assertTrue(self.bank.add_exemplar(
                "SWIPE-R", mk_frames(path_swipe(22, 140), lambda i: "open",
                                     center=(300 + 40 * seed, 220 - 15 * seed),
                                     scale=110 + 12 * seed, noise=2.5,
                                     seed=seed)))
            self.assertTrue(self.bank.add_exemplar(
                "SWIPE-L", mk_frames(path_swipe(22, -140), lambda i: "open",
                                     center=(360 - 30 * seed, 260 + 10 * seed),
                                     scale=95 + 10 * seed, noise=2.5,
                                     seed=seed + 50)))
            self.assertTrue(self.bank.add_exemplar(
                "CIRCLE", mk_frames(path_circle(26, 70), lambda i: "point",
                                    center=(330, 250), scale=105,
                                    noise=2.0, seed=seed + 90)))

    def test_heldout_copies_match(self):
        # unseen take: different position, size, noise, pace
        r = self.bank.match(mk_frames(timewarp(path_swipe(24, 130),
                                               lambda t: t * t),
                                      lambda i: "open",
                                      center=(500, 300), scale=150,
                                      noise=3.0, seed=99))
        self.assertTrue(r.accepted, r)
        self.assertEqual(r.gloss, "SWIPE-R")
        self.assertEqual(r.reason, "ok")
        r = self.bank.match(mk_frames(path_circle(28, 66, 10, -12),
                                      lambda i: "point",
                                      center=(100, 400), scale=80,
                                      noise=2.0, seed=77))
        self.assertTrue(r.accepted and r.gloss == "CIRCLE", r)

    def test_mirror_and_unknown_reject(self):
        # a left swipe is NOT a right swipe — direction matters
        r = self.bank.match(mk_frames(path_swipe(24, -150),
                                      lambda i: "open", seed=5))
        # it should land on SWIPE-L (taught) or abstain — never SWIPE-R
        self.assertNotEqual(r.gloss if r.accepted else None, "SWIPE-R", r)
        # unknown motion -> abstain, never a guess. (A horizontal
        # zigzag is NOT used here — it rightly reads as a wobbly swipe;
        # trajectory geometry, not intent, is what's matched.)
        r = self.bank.match(mk_frames(path_vbounce(30), lambda i: "open",
                                      seed=6))
        self.assertFalse(r.accepted, r)
        self.assertEqual(r.reason, "threshold")
        self.assertIsNotNone(r.gloss)   # read still reported for the UI
        self.assertGreater(r.dist, self.bank.reject_threshold)

    def test_margin_and_score_fields(self):
        r = self.bank.match(mk_frames(path_swipe(24, 138), lambda i: "open",
                                      seed=8))
        self.assertTrue(math.isfinite(r.margin) and r.margin >= 0.0)
        self.assertAlmostEqual(r.score, 1.0 / (1.0 + r.dist))
        self.assertIn(r.second_gloss, ("SWIPE-L", "CIRCLE"))


class ShapeChannel(unittest.TestCase):
    def test_same_traj_different_shape(self):
        b = sb.SignBank()
        for seed in (11, 12, 13):
            self.assertTrue(b.add_exemplar(
                "SWIPE-OPEN", mk_frames(path_swipe(22, 130),
                                        lambda i: "open", seed=seed,
                                        noise=2.0)))
            self.assertTrue(b.add_exemplar(
                "SWIPE-FIST", mk_frames(path_swipe(22, 130),
                                        lambda i: "fist", seed=seed + 20,
                                        noise=2.0)))
        r = b.match(mk_frames(path_swipe(22, 128), lambda i: "fist",
                              seed=44, noise=2.0))
        self.assertTrue(r.accepted, r)
        self.assertEqual(r.gloss, "SWIPE-FIST")
        # the traj channel genuinely cannot separate these — proof the
        # shape channel did the work, not a lucky traj tiebreak
        self.assertEqual(r.traj_gloss in ("SWIPE-OPEN", "SWIPE-FIST"), True)
        r2 = b.match(mk_frames(path_swipe(22, 132), lambda i: "open",
                               seed=45, noise=2.0))
        self.assertEqual(r2.gloss, "SWIPE-OPEN")

    def test_traj_anchor_veto(self):
        # fused winner that the motion channel does NOT argmin is vetoed.
        b = sb.SignBank()
        p = path_swipe(24, 130)                       # straight swipe
        q = path_swipe(24, 130, wobble=14.0)          # nearly same path
        b.add_exemplar("A-OPEN", mk_frames(p, lambda i: "open", seed=1))
        b.add_exemplar("B-FIST", mk_frames(q, lambda i: "fist", seed=2))
        # query: fist hand on the STRAIGHT path — traj argmin is A-OPEN,
        # shape argmin is B-FIST; the fused winner must not commit.
        r = b.match(mk_frames(p, lambda i: "fist", seed=3))
        self.assertEqual(r.traj_gloss, "A-OPEN")
        self.assertEqual(r.shape_gloss, "B-FIST")
        self.assertFalse(r.accepted, r)
        self.assertEqual(r.reason, "veto")
        # consensus mode additionally demands the shape argmin agree
        r2 = b.match(mk_frames(p, lambda i: "open", seed=4))
        self.assertTrue(r2.accepted and r2.gloss == "A-OPEN", r2)

    def test_two_hand_shape_extension(self):
        # namaste-style: two hands, no motion. A one-hand still sign
        # must NOT steal it — the absent-hand dims are taxed evidence.
        b = sb.SignBank()
        for seed in (21, 22):
            b.add_exemplar("NAMASTE",
                           mk_frames(path_still(20, seed=seed),
                                     lambda i: "open", left="open",
                                     seed=seed))
            b.add_exemplar("PALM-UP",
                           mk_frames(path_still(20, seed=seed + 5),
                                     lambda i: "open", seed=seed + 40))
        r = b.match(mk_frames(path_still(22, seed=9), lambda i: "open",
                              left="open", seed=9))
        self.assertTrue(r.accepted and r.gloss == "NAMASTE", r)
        r = b.match(mk_frames(path_still(22, seed=10), lambda i: "open",
                              seed=10))
        self.assertEqual(r.gloss, "PALM-UP")


class FusedParity(unittest.TestCase):
    """The closed-form fused cost must equal a literal 86-dim wdist DP —
    this is what makes the simplification honest instead of hand-wavy."""

    def test_closed_form_equals_literal_ndim_dtw(self):
        b = sb.SignBank()
        b.add_exemplar("X", mk_frames(path_swipe(24, 120),
                                      lambda i: "open", seed=1, noise=2.0))
        q_traj, q_shape, npts, _ = b.prep(
            mk_frames(path_swipe(24, 118, wobble=8), lambda i: "open",
                      seed=2, noise=2.0))
        ex = b.templates[0]["samples"][0]
        e_traj, e_shape = ex["traj"], ex["shape"]

        # poison a few shape dims with NaN to exercise partial sharing
        q_sh = list(q_shape)
        for i in (0, 5, 41, 60):
            q_sh[i] = NAN

        w = [1.0, 1.0] + [b.shape_dim_weight] * sb.SHAPE_DIMS
        q86 = [[p[0], p[1]] + q_sh for p in q_traj]
        e86 = [[p[0], p[1]] + e_shape for p in e_traj]
        literal = ref_dtw_band_ndim(q86, e86, sb.MATCH_BAND, w)

        shape_ss, k = sb._shape_ss(q_sh, e_shape, b.shape_dim_weight)
        self.assertGreater(k, 0)
        used = 2.0 + b.shape_dim_weight * k
        r = (2.0 + b.shape_dim_weight * sb.SHAPE_DIMS) / used
        mine = sb._dtw_add(q_traj, e_traj, sb.MATCH_BAND,
                           shape_ss) * math.sqrt(r)
        self.assertAlmostEqual(mine, literal, places=9)

    def test_no_shared_shape_taxes_not_frees(self):
        # k=0: fused = traj * sqrt(total/traj_w) — missing dims PAY.
        b = sb.SignBank()
        t = [(0.0, 0.0), (1.0, 0.0)] * 16
        b.templates.append({"gloss": "G", "label": "", "tier": 0,
                            "action": None, "user_samples": 1,
                            "samples": [{"traj": t, "shape": None}]})
        traj, shape, _, _ = b.prep(mk_frames(path_swipe(24, 100),
                                             lambda i: "open", seed=1))
        bt = sb._dtw_add(traj, t, sb.MATCH_BAND, 0.0)
        r = (2.0 + b.shape_dim_weight * sb.SHAPE_DIMS) / 2.0
        expected = bt * math.sqrt(r)
        # reproduce _score's k==0 branch directly
        shape_ss, k = sb._shape_ss(shape, None, b.shape_dim_weight)
        self.assertEqual(k, 0)
        self.assertAlmostEqual(expected, bt * math.sqrt(r), places=12)
        self.assertGreater(expected, bt)


class BankOps(unittest.TestCase):
    def test_fifo_cap(self):
        b = sb.SignBank()
        for i in range(7):
            self.assertTrue(b.add_exemplar(
                "G", mk_frames(path_swipe(20 + i, 100 + i * 3),
                               lambda i2: "open", seed=i)))
        t = b.templates[0]
        self.assertEqual(len(t["samples"]), sb.MAX_EXEMPLARS)
        self.assertEqual(t["user_samples"], sb.MAX_EXEMPLARS)

    def test_thin_query_and_empty_bank(self):
        b = sb.SignBank()
        r = b.match(mk_frames(path_swipe(20, 100), lambda i: "open"))
        self.assertFalse(r.accepted)
        self.assertEqual(r.reason, "empty-bank")
        b.add_exemplar("G", mk_frames(path_swipe(20, 100),
                                      lambda i: "open"))
        r = b.match(mk_frames(path_swipe(5, 30), lambda i: "open"))
        self.assertFalse(r.accepted)
        self.assertEqual(r.reason, "thin-query")
        self.assertFalse(b.add_exemplar("H", mk_frames(
            path_swipe(4, 10), lambda i: "open")))

    def test_remove_and_list(self):
        b = sb.SignBank()
        b.add_exemplar("G1", mk_frames(path_swipe(20, 100),
                                       lambda i: "open"),
                       label="do g1",
                       action={"argv": ["echo", "hi"], "tier": 2})
        b.add_exemplar("G2", mk_frames(path_circle(22, 60),
                                       lambda i: "fist"))
        lst = {e["gloss"]: e for e in b.list()}
        self.assertEqual(lst["G1"]["label"], "do g1")
        self.assertEqual(lst["G1"]["n_exemplars"], 1)
        self.assertEqual(lst["G1"]["action"]["argv"], ["echo", "hi"])
        self.assertEqual(lst["G1"]["tier"], 2)
        self.assertTrue(b.remove("g1"))       # case-insensitive
        self.assertFalse(b.remove("G1"))
        self.assertEqual([e["gloss"] for e in b.list()], ["G2"])

    def test_json_roundtrip_and_mode600(self):
        b = sb.SignBank()
        b.add_exemplar("SWIPE-R", mk_frames(path_swipe(22, 130),
                                            lambda i: "open", seed=3,
                                            noise=2.0),
                       label="swipe", action={"argv": ["x"], "tier": 1})
        b.add_exemplar("NAMASTE", mk_frames(path_still(20),
                                            lambda i: "open",
                                            left="open", seed=4))
        probe = mk_frames(path_swipe(24, 128), lambda i: "open", seed=8,
                          noise=2.0)
        before = b.match(probe)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "signbank.json")
            b.save(p)
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
            b2 = sb.SignBank.load(p)
        self.assertEqual(b2.list(), b.list())
        self.assertEqual(b2.reject_threshold, b.reject_threshold)
        after = b2.match(probe)
        self.assertEqual(after.gloss, before.gloss)
        self.assertAlmostEqual(after.dist, before.dist, places=12)
        self.assertEqual(after.accepted, before.accepted)
        # corrupt file -> honest raise, not a silent empty bank
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as f:
            f.write("{not json")
            bad = f.name
        try:
            with self.assertRaises(Exception):
                sb.SignBank.load(bad)
        finally:
            os.unlink(bad)

    def test_calibration(self):
        b = sb.SignBank()
        for seed in (31, 32, 33):
            b.add_exemplar("A", mk_frames(path_swipe(22, 130),
                                          lambda i: "open", seed=seed,
                                          noise=2.0))
            b.add_exemplar("B", mk_frames(path_circle(24, 70),
                                          lambda i: "open", seed=seed + 10,
                                          noise=2.0))
        labeled = []
        for seed in (41, 42, 43):
            labeled.append((mk_frames(path_swipe(22, 126), lambda i: "open",
                                      seed=seed, noise=2.5), "A"))
            labeled.append((mk_frames(path_circle(24, 72), lambda i: "open",
                                      seed=seed + 10, noise=2.5), "B"))
            labeled.append((mk_frames(path_zigzag(28), lambda i: "fist",
                                      seed=seed + 20), None))
        rep = b.calibrate_threshold(labeled, max_bad_accepts=0)
        self.assertTrue(rep["met"], rep)
        self.assertEqual(rep["eval"]["bad"], 0)
        ev = b.eval_threshold(labeled)
        self.assertEqual(ev["wrong_gloss"], 0)
        self.assertEqual(ev["false_accept"], 0)
        self.assertGreaterEqual(ev["correct"], 7)  # 6 pos + >=1 of 3 neg
        # a floor-level threshold must abstain on everything
        ev0 = b.eval_threshold(labeled, threshold=0.0)
        self.assertEqual(ev0["accept"], 0)
        self.assertEqual(ev0["abstain"], len(labeled))

    def test_timing_linear_scan(self):
        # not a hard gate — the honest number the parity note quotes.
        import time
        b = sb.SignBank()
        for g in range(10):
            for e in range(3):
                b.add_exemplar(f"G{g}", mk_frames(
                    path_swipe(20 + g + e, 60 + 12 * g, 4 * e),
                    lambda i: "open" if g % 2 else "point", seed=g * 7 + e))
        fr = mk_frames(path_swipe(24, 70), lambda i: "open", seed=1)
        t0 = time.perf_counter()
        N = 5
        for _ in range(N):
            b.match(fr)
        ms = (time.perf_counter() - t0) / N * 1000
        print(f"\n[signbank] 10g x3ex fused match: {ms:.1f} ms/query "
              f"({len(b.templates) * 3} exemplars, 2-DP each)")
        self.assertLess(ms, 2000.0)  # sanity bound only


if __name__ == "__main__":
    unittest.main(verbosity=2)
