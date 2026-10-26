#!/usr/bin/env python3
"""refmatch — reference DTW template bank, ported line-for-line from
sign-flow's crates/alive-sign/src/matcher.rs (the engine the merged
product's pure-Python signbank must agree with).

This is the INDEPENDENT re-implementation used by the e2e lane to check
server/signbank.py: identical trajectories in, identical decisions out.
It is deliberately dumb — the verbatim linear scan, no retrieval index —
because the index in matcher.rs is pinned to this exact scan anyway
(proofs/sign-rag), so porting the scan ports the semantics.

Ported semantics (matcher.rs):
  * RESAMPLE_N = 32, MATCH_BAND = 4.
  * prep per BankSpace:
      Pixel   -> normalize_nd(resample_nd(raw, 32), spatial=2)   (position-invariant)
      Body    -> resample_nd(raw, 32)                            (location carries id)
      Feature -> resample_time(raw, 32)                          (uniform index resample)
  * resample_nd: arc-length on the FIRST TWO dims only; non-finite steps
    count 0; arc ~0 -> uniform-index fallback (resample_time).
  * normalize_nd: centroid origin, RMS radius (clamped >= 1e-6), applied
    to the first `spatial` dims only.
  * wdist: weighted Euclidean over dims finite in BOTH points, rescaled
    by total_w / used_w; zero shared finite dims -> inf (never free).
  * dtw_w: Sakoe-Chiba banded DP, band = max(band, |n-m|+1, 1), cost
    normalized by max(n,m). Equivalent to the band-edge-fixed Rust
    ping-pong (cur[lo-1] = INF restores textbook semantics — the fixed
    version IS a clean banded matrix, so a clean matrix is what this is).
  * scored: per gloss, min over its exemplars; stable sort by distance
    (Rust sort_by is stable; NaN compares Equal -> keeps template order).
  * best: None when raw < 8 points, bank empty, channel mismatch, or any
    stored point's width != channels ("consistent" check).
  * recognize: best_d > reject_threshold -> None (reject); else
    (gloss, 1/(1+best_d)). Margin (second_d) is reported, not gated.

Known honest deltas vs the Rust build: this port computes in f64 while
matcher.rs uses f32 — distances agree to ~1e-7 relative, which can only
flip a decision at an exact tie. That is a documented limit of the
parity test, not a hidden one.
"""
import json
import math
import os
from functools import cmp_to_key

RESAMPLE_N = 32
MATCH_BAND = 4
MIN_POINTS = 8
MAX_EXEMPLARS = 5
DEFAULT_REJECT = 0.45

INF = float("inf")
NAN = float("nan")


def _at(p, i):
    """matcher.rs Pt::at — out-of-range dim reads as NaN."""
    return p[i] if 0 <= i < len(p) else NAN


def _is_finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def resample_nd(pts, n=RESAMPLE_N):
    """Arc-length resample to n points parameterized on dims 0,1.
    Degenerate arcs fall back to uniform-index resample (resample_time)."""
    if len(pts) < 2 or n < 2:
        return [list(p) for p in pts]
    cum = [0.0] * len(pts)
    for i in range(1, len(pts)):
        dx = _at(pts[i], 0) - _at(pts[i - 1], 0)
        dy = _at(pts[i], 1) - _at(pts[i - 1], 1)
        step = math.sqrt(dx * dx + dy * dy) if (_is_finite(dx) and _is_finite(dy)) else 0.0
        cum[i] = cum[i - 1] + step
    total = cum[-1]
    if total <= 1e-9:
        return resample_time(pts, n)
    out = []
    j = 0
    last = len(pts) - 1
    for k in range(n):
        target = total * k / (n - 1)
        while j + 1 < last and cum[j + 1] < target:
            j += 1
        seg = max(cum[j + 1] - cum[j], 1e-9)
        t = min(max((target - cum[j]) / seg, 0.0), 1.0)
        dims = max(len(pts[j]), len(pts[j + 1]))
        out.append([_at(pts[j], d) + (_at(pts[j + 1], d) - _at(pts[j], d)) * t
                    for d in range(dims)])
    return out


def resample_time(pts, n=RESAMPLE_N):
    """Uniform-index resample (Feature space, and the degenerate-arc fallback)."""
    if len(pts) < 2 or n < 2:
        return [list(p) for p in pts]
    out = []
    last = len(pts) - 1
    for k in range(n):
        pos = last * k / (n - 1)
        j = min(int(math.floor(pos)), last - 1)
        t = pos - j
        dims = max(len(pts[j]), len(pts[j + 1]))
        out.append([_at(pts[j], d) + (_at(pts[j + 1], d) - _at(pts[j], d)) * t
                    for d in range(dims)])
    return out


def normalize_nd(pts, spatial=2):
    """Centroid origin + RMS-radius scale on the first `spatial` dims."""
    if not pts:
        return []
    k = min(spatial, min(len(p) for p in pts))
    if k == 0:
        return [list(p) for p in pts]
    n = float(len(pts))
    mean = [0.0] * k
    for p in pts:
        for d in range(k):
            mean[d] += p[d]
    mean = [m / n for m in mean]
    rms = math.sqrt(sum(sum((p[d] - mean[d]) ** 2 for d in range(k)) for p in pts) / n)
    # Rust f32::max(NaN, x) returns x — mirror it for the degenerate case.
    s = 1e-6 if not math.isfinite(rms) or rms < 1e-6 else rms
    return [[(v - mean[d]) / s if d < k else v for d, v in enumerate(p)] for p in pts]


def wdist(a, b, w=()):
    """Weighted point distance over dims finite in BOTH; unshared dims
    cost the observed-dim average (total_w / used_w rescale)."""
    n = min(len(a), len(b))
    acc = used_w = total_w = 0.0
    for d in range(n):
        wd = max(w[d] if d < len(w) else 1.0, 0.0)
        total_w += wd
        x, y = a[d], b[d]
        if not (_is_finite(x) and _is_finite(y)):
            continue
        acc += wd * (x - y) * (x - y)
        used_w += wd
    if used_w <= 0.0:
        return INF if total_w > 0.0 else 0.0
    return math.sqrt(acc * (total_w / used_w))


def dtw_w(a, b, band=MATCH_BAND, w=()):
    """Sakoe-Chiba banded DTW, mean cost per aligned step (path length
    max(n,m)). Textbook banded matrix — bit-equivalent to the
    band-edge-fixed Rust ping-pong; dtw_w >= dtw_full always."""
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
        cur[lo - 1] = INF  # band-edge fix: out-of-band stays INF
        for j in range(lo, hi + 1):
            cur[j] = wdist(a[i - 1], b[j - 1], w) + min(prev[j], cur[j - 1], prev[j - 1])
        # cells of `cur` outside [lo, hi] are stale — same as the Rust
        # ping-pong; the next row only reads inside its own window, so the
        # stale tail can never convict a path (verified vs dtw_full).
        prev, cur = cur, prev
    return prev[m] / max(n, m)


def dtw_full(a, b, w=()):
    """Unbounded textbook DTW — the independent O(n*m) reference used to
    sanity-check the banded path (equal whenever an optimal path stays
    inside the window)."""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return INF
    prev = [INF] * (m + 1)
    cur = [INF] * (m + 1)
    prev[0] = 0.0
    for i in range(1, n + 1):
        cur[0] = INF
        for j in range(1, m + 1):
            cur[j] = wdist(a[i - 1], b[j - 1], w) + min(prev[j], cur[j - 1], prev[j - 1])
        prev, cur = cur, prev
    return prev[m] / max(n, m)


class Decision:
    """Normalized match outcome shared by the reference bank and the
    signbank adapter: gloss=None is a REFUSAL (short input / empty bank /
    channel mismatch); gloss set + accepted=False is a threshold REJECT.
    The two are different failures and stay distinguishable."""

    __slots__ = ("gloss", "dist", "second", "accepted", "score", "refused",
                 "extra")

    def __init__(self, gloss, dist, second, accepted, score, refused=None,
                 extra=None):
        self.gloss = gloss
        self.dist = dist
        self.second = second
        self.accepted = accepted
        self.score = score
        self.refused = refused
        self.extra = extra or {}

    def __repr__(self):
        return (f"Decision(gloss={self.gloss!r} d={self.dist:.4f} "
                f"second={self.second:.4f} accepted={self.accepted} refused={self.refused})")


class RefBank:
    """The matcher.rs TemplateBank, verbatim semantics, linear scan only.
    `templates` keep insertion order — ties sort stable, earliest wins."""

    def __init__(self, reject_threshold=DEFAULT_REJECT, space="Pixel",
                 channels=2, dim_weights=None, shape_dims=None):
        self.templates = []          # [{"gloss": str, "samples": [[[float]]], "user_samples": int}]
        self.reject_threshold = reject_threshold
        self.space = space
        self.channels = max(1, channels)
        self.dim_weights = list(dim_weights or [])
        self.shape_dims = list(shape_dims or [])

    # -- prep ----------------------------------------------------------
    def prep_nd(self, raw):
        if self.space == "Pixel":
            return normalize_nd(resample_nd(raw, RESAMPLE_N), 2)
        if self.space == "Body":
            return resample_nd(raw, RESAMPLE_N)
        return resample_time(raw, RESAMPLE_N)  # Feature

    def consistent(self):
        return all(len(p) == self.channels
                   for t in self.templates for s in t["samples"] for p in s)

    # -- mutation ------------------------------------------------------
    def add(self, gloss, raw, user=True):
        """add_sample_nd: >=8 raw points, every point exactly `channels`
        wide, FIFO cap at MAX_EXEMPLARS. Returns bool (refusal is False,
        never a silent pad)."""
        raw = [list(map(float, p)) for p in raw]
        if len(raw) < MIN_POINTS or any(len(p) != self.channels for p in raw):
            return False
        norm = self.prep_nd(raw)
        for t in self.templates:
            if t["gloss"] == gloss:
                t["samples"].append(norm)
                if len(t["samples"]) > MAX_EXEMPLARS:
                    t["samples"].pop(0)
                if user:
                    t["user_samples"] = min(t["user_samples"] + 1, len(t["samples"]))
                return True
        self.templates.append({"gloss": gloss, "samples": [norm],
                               "user_samples": 1 if user else 0})
        return True

    def remove_gloss(self, gloss):
        self.templates = [t for t in self.templates if t["gloss"] != gloss]

    # -- query ---------------------------------------------------------
    def scored(self, raw):
        """[(gloss, d)] sorted ascending, stable on ties; None = refusal."""
        if (len(raw) < MIN_POINTS or not self.templates
                or any(len(p) != self.channels for p in raw)
                or not self.consistent()):
            return None
        norm = self.prep_nd(raw)
        scored = [(t["gloss"],
                   min((dtw_w(norm, s, MATCH_BAND, self.dim_weights)
                        for s in t["samples"]), default=INF))
                  for t in self.templates]
        # Rust: sort_by(|a,b| a.1.partial_cmp(&b.1).unwrap_or(Equal)) —
        # stable; NaN.partial_cmp(_) = None -> Equal against ANY value
        # (INF orders normally — only NaN is special). A NaN-distance
        # template therefore keeps its insertion position and can win the
        # argmin; faithful, and only reachable via hand-edited banks.
        def cmp(x, y):
            a_, b_ = x[1], y[1]
            if math.isnan(a_) or math.isnan(b_):
                return 0
            return -1 if a_ < b_ else (1 if a_ > b_ else 0)
        scored.sort(key=cmp_to_key(cmp))
        return scored

    def best(self, raw):
        s = self.scored(raw)
        if s is None:
            return None
        return s[0][0], s[0][1], (s[1][1] if len(s) > 1 else INF)

    def recognize(self, raw):
        b = self.best(raw)
        if b is None:
            return None
        gloss, best_d, _ = b
        if best_d > self.reject_threshold:
            return None
        return gloss, 1.0 / (1.0 + best_d)

    def match(self, raw):
        """The merged-product contract shape: always returns a Decision;
        a refusal has gloss=None + refused reason, a threshold reject has
        gloss set + accepted=False."""
        raw = [list(map(float, p)) for p in raw]
        s = self.scored(raw)
        if s is None:
            why = ("short" if len(raw) < MIN_POINTS else
                   "empty-bank" if not self.templates else
                   "channels" if any(len(p) != self.channels for p in raw) else
                   "inconsistent-bank")
            return Decision(None, INF, INF, False, 0.0, refused=why)
        gloss, best_d = s[0]
        second = s[1][1] if len(s) > 1 else INF
        ok = best_d <= self.reject_threshold
        # argmin candidate is reported even when the threshold rejects it
        return Decision(gloss, best_d, second, ok,
                        1.0 / (1.0 + best_d), refused=None)

    # -- serde: the Rust bank JSON layout ------------------------------
    def to_json_dict(self):
        return {"templates": [{"gloss": t["gloss"], "samples": t["samples"],
                               "user_samples": t["user_samples"]}
                              for t in self.templates],
                "reject_threshold": self.reject_threshold,
                "space": self.space,
                "channels": self.channels,
                "dim_weights": self.dim_weights,
                "shape_dims": self.shape_dims}

    @classmethod
    def from_json_dict(cls, d):
        # Tolerant loader: the e2e lane shares one config dir with wfd.py,
        # whose teach-saves rewrite signbank.json in the impl layout —
        # samples as {"traj": [[x,y]..], "shape": [..]} dicts — and a
        # list-shaped bare-templates file is legal on the impl side too.
        # A ref engine only reads the trajectory channel either way.
        if isinstance(d, list):
            d = {"templates": d}
        b = cls(reject_threshold=d.get("reject_threshold", DEFAULT_REJECT),
                space=d.get("space", "Pixel"),
                channels=d.get("channels", 2),
                dim_weights=d.get("dim_weights") or [],
                shape_dims=d.get("shape_dims") or [])
        def _points(s):
            if isinstance(s, dict):
                s = s.get("traj") or []
            out = []
            for p in s or []:
                try:
                    out.append([float(p[0]), float(p[1])])
                except (TypeError, ValueError, IndexError, KeyError):
                    continue
            return out
        for t in d.get("templates", []):
            if not isinstance(t, dict):
                continue
            b.templates.append({"gloss": t.get("gloss") or t.get("name"),
                                "samples": [_points(s)
                                            for s in t.get("samples") or []],
                                "user_samples": t.get("user_samples", 0)})
        return b

    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls.from_json_dict(json.load(f))

    def save(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.to_json_dict(), f)


# ---------------------------------------------------------------------------
# kp42 frame -> dominant-hand centroid path.
#
# Mirrors server/signbank.py's frame ingestion (_frame_hands/_hand_obs):
# score-weighted centroid of confident keys (>= min_score, need min_keys),
# dominant hand = the one observed in MORE frames (tie -> right block).
# The reference shares this rule so "identical inputs" in the parity test
# means identical frames, honestly derived on both sides. The still-collapse
# gate is an impl-side documented extension (still_frac) that we adopt ONLY
# as an optional input-prep parity step — flagged, never hidden.
# ---------------------------------------------------------------------------

def _kp_pt(p):
    if isinstance(p, dict):
        s = p.get("score", 1.0)
        return (float(p.get("x", 0.0)), float(p.get("y", 0.0)),
                1.0 if s is None else float(s))
    return (float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 1.0)


def hand_obs(pts, min_score=0.5, min_keys=8):
    """(centroid, bbox_scale) for one 21-pt hand block, or (None, 0)."""
    conf = [p for p in pts if p[2] >= min_score]
    if len(conf) < min_keys:
        return None, 0.0
    sw = sum(p[2] for p in conf)
    if sw <= 0.0:
        return None, 0.0
    cx = sum(p[0] * p[2] for p in conf) / sw
    cy = sum(p[1] * p[2] for p in conf) / sw
    xs = [p[0] for p in conf]
    ys = [p[1] for p in conf]
    bs = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
    return (cx, cy), bs


def dominant_centroid_path(frames, min_score=0.5, min_keys=8,
                           still_frac=None):
    """kp42 frames -> raw centroid trajectory [(x,y), ...] of the dominant
    hand + the hand's mean bbox size (for the optional still gate).
    Returns (path, hand_px). points[0:21]=left, [21:42]=right (W1 wire);
    21-pt frames read as one hand via frame["hand"] (default right)."""
    cents = [[], []]
    scales = [[], []]
    for fr in frames or []:
        pts = [_kp_pt(p) for p in (fr.get("points") or [])]
        if len(pts) == 21:
            idx = 1 if str(fr.get("hand", "right")).lower() == "right" else 0
            hands = [None, None]
            hands[idx] = pts
        else:
            hands = [pts[0:21], pts[21:42]]
        for h in range(2):
            c, bs = hand_obs(hands[h], min_score, min_keys) if hands[h] \
                else (None, 0.0)
            if c is not None:
                cents[h].append(c)
                scales[h].append(bs)
    dom = 1 if len(cents[1]) >= len(cents[0]) else 0
    raw = list(cents[dom])
    hand_px = (sum(scales[dom]) / len(scales[dom])) if scales[dom] else 0.0
    if still_frac is not None and len(raw) >= 2:
        # ADOPTED impl-side gate (documented deviation in signbank.prep):
        # a path whose RMS radius is a small fraction of hand size is a
        # hold, not a motion — collapse before resample. Kept optional so
        # the reference can run either semantics; parity runs enable it
        # with the impl's own still_frac value.
        mx = sum(p[0] for p in raw) / len(raw)
        my = sum(p[1] for p in raw) / len(raw)
        rms = math.sqrt(sum((p[0] - mx) ** 2 + (p[1] - my) ** 2
                            for p in raw) / len(raw))
        if rms < max(2.0, still_frac * hand_px):
            raw = [(mx, my)] * len(raw)
    return raw, hand_px


def bank_from_raw_exemplars(exemplars, reject_threshold=DEFAULT_REJECT, space="Pixel"):
    """Build a bank from RAW (un-normalized) trajectories:
    {gloss: [[(x,y), ...], ...]}."""
    b = RefBank(reject_threshold=reject_threshold, space=space)
    for gloss, raws in exemplars.items():
        for r in raws:
            b.add(gloss, r, user=True)
    return b


if __name__ == "__main__":
    # self-check: a taught circle should beat a line on a circle query,
    # and a random walk should reject. Mirrors matcher.rs's own tests.
    import random
    rng = random.Random(7)
    circle = [(0.5 + 0.4 * math.cos(2 * math.pi * i / 20),
               0.5 + 0.4 * math.sin(2 * math.pi * i / 20)) for i in range(21)]
    line = [(i / 20.0, 0.5) for i in range(21)]
    bank = bank_from_raw_exemplars({"CIRCLE": [circle], "LINE": [line]})
    q = [(x + rng.uniform(-0.01, 0.01), y + rng.uniform(-0.01, 0.01)) for x, y in circle]
    print("circle query ->", bank.match(q))
    squig = [(rng.random(), rng.random()) for _ in range(25)]
    print("squiggle     ->", bank.match(squig))
    print("short input  ->", bank.match(q[:5]))
