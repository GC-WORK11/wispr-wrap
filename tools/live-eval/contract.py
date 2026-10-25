"""contract.py — pure-Python port of the Sign-Flow teach-bank + endpointer
contract, stdlib only.

This is the harness's LOCAL COPY OF THE CONTRACT. It ports the semantics of
`crates/alive-sign/src/matcher.rs` (resample-32, banded DTW band=4 with the
proofs/dtw-fix band edge, weighted NaN-aware point distance, FIFO cap 5
exemplars, >=8 raw points, best_d reject) and
`crates/alive-temporal/src/lib.rs` (Idle/Signing/Pausing still-hold
endpointer, motion_energy, eos_windowed, jitter debounce, fluent dip leg,
max_pause cancel) onto plain lists. When `server/signbank.py` (W1) lands the
harness prefers it (see engine.py); this module stays as the parity
reference — the same role `dtw_full` plays for `dtw_w` in Rust.

Coordinate conventions: points are lists of floats, len == bank.channels.
A 2-channel bank carries [x, y] hand-centroid points (the product default,
PRD section 4: Pixel space is position-invariant and needs no body frame).
Frames in the harness are 2x21 keypoints (two MediaPipe-style hands);
`hand_centroid` / `handshape` mirror the Rust gates (score >= 0.4, >= 6
confident keys, measurable palm) on ONE hand's 21 points.

Units note: motion_energy runs on RAW keypoint coordinates (the Rust app
feeds RTMW pixel coords; a still pose is rescued by the pixel-motion
`mscore` squash the phone-keypoint path does not have — see BENCH.md).
"""

import math

# ---------------------------------------------------------------------------
# Constants (matcher.rs / alive-temporal defaults the app ships).
# ---------------------------------------------------------------------------

RESAMPLE_N = 32            # matcher.rs RESAMPLE_N
MATCH_BAND = 4             # matcher.rs MATCH_BAND
MIN_POINTS = 8             # add_sample / best refuse below this raw count
MAX_EXEMPLARS = 5          # per-gloss FIFO cap
SCORE_GATE = 0.4           # per-keypoint confidence gate (app SCORE_GATE)
MIN_KEYS = 6               # min confident keys per hand (app MIN_KEYS)
SHAPE_N = 19               # handshape descriptor width

# Endpointer defaults — EndpointConfig::default() verbatim.
EP_TENTATIVE_MS = 350
EP_COMMIT_MS = 700
EP_MIN_SEGMENT_MS = 250
EP_BOUNDARY_P = 0.85
EP_LOW_MOTION = 0.02
EP_MAX_PAUSE_MS = 2000
EP_MOTION_CONFIRM_GAP_MS = 300
# eos_windowed parameters the app uses (main.rs EOS_*).
EOS_WINDOW_MS = 450
EOS_MIN_SAMPLES = 4


# ---------------------------------------------------------------------------
# Hand geometry helpers (matcher.rs hand_centroid / handshape).
# ---------------------------------------------------------------------------

# COCO-WholeBody / MediaPipe hand layout: 0 wrist; 1-4 thumb cmc..tip;
# 5-8 index; 9-12 middle; 13-16 ring; 17-20 pinky.
FINGER_JOINTS = ((1, 2, 3, 4), (5, 6, 7, 8), (9, 10, 11, 12),
                 (13, 14, 15, 16), (17, 18, 19, 20))
TIPS = (4, 8, 12, 16, 20)


def hand_centroid(points, scores, base=0, min_score=SCORE_GATE,
                  min_keys=MIN_KEYS):
    """Score-weighted centroid of one 21-point hand. None when too few
    confident keys — same gate as matcher.rs hand_centroid."""
    sx = sy = sw = 0.0
    n = 0
    for i in range(base, base + 21):
        s = scores[i] if i < len(scores) else 0.0
        if s < min_score:
            continue
        p = points[i]
        sx += p[0] * s
        sy += p[1] * s
        sw += s
        n += 1
    if n < min_keys or sw <= 0.0:
        return None
    return (sx / sw, sy / sw)


def _d2(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _cos_angle(a, b, c):
    ux, uy = a[0] - b[0], a[1] - b[1]
    vx, vy = c[0] - b[0], c[1] - b[1]
    nu = math.hypot(ux, uy)
    nv = math.hypot(vx, vy)
    if nu < 1e-6 or nv < 1e-6:
        return float("nan")
    return (ux * vx + uy * vy) / (nu * nv)


def handshape(points, scores, base=0, min_score=SCORE_GATE,
              min_keys=MIN_KEYS):
    """19-dim handshape descriptor for the hand rooted at `base`
    (matcher.rs handshape). Wrist origin, palm-width units; NaN cells for
    unobserved joints — missing evidence is never zero-filled."""
    if len(points) < base + 21 or len(scores) < base + 21:
        return None
    n_conf = sum(1 for i in range(base, base + 21)
                 if scores[i] >= min_score)
    if n_conf < min_keys:
        return None

    def ok(k):
        return scores[base + k] >= min_score

    def p(k):
        return points[base + k]

    if not ok(0) or not ok(9):
        return None
    pw = _d2(p(0), p(9))
    if pw < 1e-3:
        return None
    nan = float("nan")
    d = [nan] * SHAPE_N
    # ext_* — per-finger extension: mean of -cos over the two flexion joints.
    for f, j in enumerate(FINGER_JOINTS):
        if all(ok(k) for k in j):
            d[f] = (-_cos_angle(p(j[0]), p(j[1]), p(j[2]))
                    - _cos_angle(p(j[1]), p(j[2]), p(j[3]))) / 2.0
    # spr_* — adjacent fingertip gaps in palm widths.
    for i in range(4):
        if ok(TIPS[i]) and ok(TIPS[i + 1]):
            d[5 + i] = _d2(p(TIPS[i]), p(TIPS[i + 1])) / pw
    # tipw_* — fingertip to wrist distance in palm widths.
    for f, t in enumerate(TIPS):
        if ok(t):
            d[9 + f] = _d2(p(t), p(0)) / pw
    # ori_* — palm direction unit vector (kept orientation-sensitive).
    d[14] = (p(9)[0] - p(0)[0]) / pw
    d[15] = (p(9)[1] - p(0)[1]) / pw
    # roll_* — palm roll axis unit vector.
    if ok(5) and ok(17):
        rx, ry = p(17)[0] - p(5)[0], p(17)[1] - p(5)[1]
        nrm = math.hypot(rx, ry)
        if nrm > 1e-6:
            d[16] = rx / nrm
            d[17] = ry / nrm
    # aperture — mean confident tip->wrist distance (>=3 tips).
    ap = 0.0
    ap_n = 0
    for t in TIPS:
        if ok(t):
            ap += _d2(p(t), p(0)) / pw
            ap_n += 1
    if ap_n >= 3:
        d[18] = ap / ap_n
    return d


def median_descriptor(rows, dims):
    """Upper-median per dim over the rows where it is finite — the
    clip-level shape aggregate the fused banks were calibrated on
    (matcher.rs median_descriptor). Returns (vec|None, n_filled,
    n_all_nan); per-position 0.0 fill matches the eval's fill_nan rule."""
    if not rows:
        return None, 0, 0
    out = []
    n_filled = n_all_nan = 0
    for d in dims:
        vals = [r[d] for r in rows if d < len(r) and math.isfinite(r[d])]
        if not vals:
            n_all_nan += 1
            n_filled += len(rows)
            out.append(0.0)
            continue
        vals.sort()
        n_filled += sum(1 for r in rows
                        if not (d < len(r) and math.isfinite(r[d])))
        out.append(vals[len(vals) // 2])
    return out, n_filled, n_all_nan


# ---------------------------------------------------------------------------
# Resample / normalize (matcher.rs).
# ---------------------------------------------------------------------------

def _at(p, i):
    return p[i] if i < len(p) else float("nan")


def resample(pts, n=RESAMPLE_N):
    """Arc-length resample to exactly n points (2D convenience wrapper)."""
    return [list(q) for q in resample_nd([[p[0], p[1]] for p in pts], n)]


def resample_nd(pts, n=RESAMPLE_N):
    """Arc-length resample of N-dim points to n, parameterized on the first
    two (spatial) dims; degenerate paths fall back to index resampling —
    matcher.rs resample_nd verbatim."""
    if len(pts) < 2 or n < 2:
        return [list(p) for p in pts]
    cum = [0.0] * len(pts)
    for i in range(1, len(pts)):
        dx = _at(pts[i], 0) - _at(pts[i - 1], 0)
        dy = _at(pts[i], 1) - _at(pts[i - 1], 1)
        step = math.hypot(dx, dy) if math.isfinite(dx) and math.isfinite(dy) else 0.0
        cum[i] = cum[i - 1] + step
    if cum[-1] <= 1e-9:
        return resample_time(pts, n)
    total = cum[-1]
    out = []
    j = 0
    for k in range(n):
        target = total * k / (n - 1)
        while j + 1 < len(pts) - 1 and cum[j + 1] < target:
            j += 1
        seg = max(cum[j + 1] - cum[j], 1e-9)
        t = min(max((target - cum[j]) / seg, 0.0), 1.0)
        dims = max(len(pts[j]), len(pts[j + 1]))
        out.append([_at(pts[j], d) + (_at(pts[j + 1], d) - _at(pts[j], d)) * t
                    for d in range(dims)])
    return out


def resample_time(pts, n=RESAMPLE_N):
    """Uniform-index resample (Feature space) — matcher.rs resample_time."""
    if len(pts) < 2 or n < 2:
        return [list(p) for p in pts]
    out = []
    for k in range(n):
        pos = (len(pts) - 1) * k / (n - 1)
        j = min(int(pos), len(pts) - 2)
        t = pos - j
        dims = max(len(pts[j]), len(pts[j + 1]))
        out.append([_at(pts[j], d) + (_at(pts[j + 1], d) - _at(pts[j], d)) * t
                    for d in range(dims)])
    return out


def normalize(pts):
    """Centroid origin + RMS-radius scale on 2D points (matcher.rs
    normalize). Direction preserved — a reversed swipe is a different
    sign."""
    if not pts:
        return []
    n = float(len(pts))
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    rms = math.sqrt(sum((p[0] - mx) ** 2 + (p[1] - my) ** 2 for p in pts) / n)
    s = max(rms, 1e-6)
    return [[(p[0] - mx) / s, (p[1] - my) / s] for p in pts]


def normalize_nd(pts, spatial=2):
    """Translate+scale-normalize the first `spatial` dims; other dims pass
    through (matcher.rs normalize_nd)."""
    if not pts:
        return []
    k = min(spatial, min(len(p) for p in pts))
    if k == 0:
        return [list(p) for p in pts]
    n = float(len(pts))
    mean = [sum(p[d] for p in pts) / n for d in range(k)]
    rms = math.sqrt(sum(sum((p[d] - mean[d]) ** 2 for d in range(k))
                        for p in pts) / n)
    s = max(rms, 1e-6)
    return [[(v - mean[d]) / s if d < k else v for d, v in enumerate(p)]
            for p in pts]


# ---------------------------------------------------------------------------
# Banded DTW (matcher.rs dtw_w + dtw_full reference).
# ---------------------------------------------------------------------------

def wdist(a, b, w=None):
    """Weighted point distance over dims finite in BOTH points, rescaled by
    total_w/used_w — NaN dims cost the observed-dim average, never free.
    All-finite 2D with unit weights is exactly Euclidean."""
    n = min(len(a), len(b))
    acc = used_w = total_w = 0.0
    for d in range(n):
        wd = max(w[d], 0.0) if w is not None and d < len(w) else 1.0
        total_w += wd
        x, y = a[d], b[d]
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        acc += wd * (x - y) * (x - y)
        used_w += wd
    if used_w <= 0.0:
        return float("inf") if total_w > 0.0 else 0.0
    return math.sqrt(acc * (total_w / used_w))


def dtw_w(a, b, band=MATCH_BAND, w=None):
    """Sakoe-Chiba banded DTW, normalized by max(n,m) — includes the
    proofs/dtw-fix band-edge fix: cur[lo-1] is reset to INF every row so
    the left-edge move can only come from prev[lo] or prev[lo-1]."""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return float("inf")
    band = max(band, abs(n - m) + 1, 1)
    INF = float("inf")
    prev = [INF] * (m + 1)
    cur = [INF] * (m + 1)
    prev[0] = 0.0
    for i in range(1, n + 1):
        lo = max(i - band, 1)
        hi = min(i + band, m)
        cur[lo - 1] = INF          # stale left-edge slot (the fix)
        for j in range(lo, hi + 1):
            cost = wdist(a[i - 1], b[j - 1], w)
            cur[j] = cost + min(prev[j], cur[j - 1], prev[j - 1])
        prev, cur = cur, prev
    return prev[m] / max(n, m)


def dtw_full(a, b, w=None):
    """Unbanded textbook DTW — the reference the banded loop is pinned to.
    Tests assert dtw_w >= dtw_full always."""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return float("inf")
    INF = float("inf")
    prev = [INF] * (m + 1)
    cur = [INF] * (m + 1)
    prev[0] = 0.0
    for i in range(1, n + 1):
        cur[0] = INF
        for j in range(1, m + 1):
            cost = wdist(a[i - 1], b[j - 1], w)
            cur[j] = cost + min(prev[j], cur[j - 1], prev[j - 1])
        prev, cur = cur, prev
    return prev[m] / max(n, m)


dtw = dtw_w


# ---------------------------------------------------------------------------
# TemplateBank (matcher.rs TemplateBank — the linear-scan semantics; the
# Rust retrieval index is an exactness-preserving accelerator, not part of
# the contract).
# ---------------------------------------------------------------------------

class Template:
    __slots__ = ("gloss", "samples", "user_samples")

    def __init__(self, gloss):
        self.gloss = gloss
        self.samples = []          # normalized exemplars, RESAMPLE_N pts each
        self.user_samples = 0


class TemplateBank:
    """Per-user exemplar bank. Spaces:
      pixel   — resample + centroid/RMS normalize (location-free; default)
      body    — resample only (input already in shoulder-frame units)
      feature — index-resample only (non-spatial descriptor series)
    """

    def __init__(self, reject_threshold=0.45, space="pixel", channels=2,
                 dim_weights=None, shape_dims=None):
        self.templates = []
        self.reject_threshold = reject_threshold
        self.space = space
        self.channels = max(1, channels)
        self.dim_weights = list(dim_weights or [])
        self.shape_dims = list(shape_dims or [])

    @classmethod
    def new(cls, reject_threshold=0.45):
        return cls(reject_threshold, "pixel", 2)

    @classmethod
    def new_body(cls, reject_threshold=0.3553):
        return cls(reject_threshold, "body", 2)

    def consistent(self):
        return all(all(all(len(p) == self.channels for p in s)
                       for s in t.samples) for t in self.templates)

    def _prep(self, raw):
        if self.space == "pixel":
            return normalize_nd(resample_nd(raw, RESAMPLE_N), 2)
        if self.space == "body":
            return resample_nd(raw, RESAMPLE_N)
        return resample_time(raw, RESAMPLE_N)

    def add_sample(self, gloss, raw):
        """One raw sample path; needs >= MIN_POINTS points each exactly
        `channels` wide. FIFO cap MAX_EXEMPLARS (evict front)."""
        nd = [list(p[:2]) if not isinstance(p, (list, tuple)) else list(p)
              for p in raw]
        return self.add_sample_nd(gloss, nd)

    def add_sample_nd(self, gloss, raw):
        if (len(raw) < MIN_POINTS
                or any(len(p) != self.channels for p in raw)):
            return False
        norm = self._prep(raw)
        for t in self.templates:
            if t.gloss == gloss:
                t.samples.append(norm)
                if len(t.samples) > MAX_EXEMPLARS:
                    t.samples.pop(0)
                return True
        t = Template(gloss)
        t.samples.append(norm)
        self.templates.append(t)
        return True

    def mark_taught(self, gloss):
        for t in self.templates:
            if t.gloss == gloss:
                t.user_samples = min(t.user_samples + 1, len(t.samples))
                return

    def _slot_dists(self, norm):
        return [min(dtw_w(norm, s, MATCH_BAND, self.dim_weights)
                    for s in t.samples)
                for t in self.templates]

    def scored_nd(self, raw):
        """All glosses scored (min over exemplars), sorted ascending —
        None = the same refusal best_nd encodes."""
        if (len(raw) < MIN_POINTS or not self.templates
                or any(len(p) != self.channels for p in raw)
                or not self.consistent()):
            return None
        norm = self._prep(raw)
        scored = [(t.gloss, d)
                  for t, d in zip(self.templates, self._slot_dists(norm))]
        scored.sort(key=lambda kv: kv[1])
        return scored

    def best(self, raw):
        return self.best_nd(raw)

    def best_nd(self, raw):
        s = self.scored_nd(raw)
        if not s:
            return None
        second = s[1][1] if len(s) > 1 else float("inf")
        return s[0][0], s[0][1], second

    def rank(self, raw, k):
        return self.rank_nd(raw, k)

    def rank_nd(self, raw, k):
        s = self.scored_nd(raw)
        if not s:
            return []
        return [(g, 1.0 / (1.0 + d), d) for g, d in s[:max(1, k)]]

    def dists_nd(self, raw, w):
        """Per-gloss min-exemplar distance under an explicit dim-weight
        vector — the masked channel scan behind the traj-anchor veto."""
        if (len(raw) < MIN_POINTS or not self.templates
                or any(len(p) != self.channels for p in raw)
                or not self.consistent()):
            return None
        norm = self._prep(raw)
        return [(t.gloss,
                 min(dtw_w(norm, s, MATCH_BAND, w) for s in t.samples))
                for t in self.templates]

    def recognize(self, raw):
        """(gloss, score) or None — rejects best_d > reject_threshold."""
        r = self.best(raw)
        if r is None:
            return None
        gloss, best_d, _ = r
        if best_d > self.reject_threshold:
            return None
        return gloss, 1.0 / (1.0 + best_d)

    def traj_channel_agrees(self, q, hit):
        """The traj-anchor veto (main.rs traj_channel_agrees): on fused
        (>2ch non-feature) banks the trajectory dims alone must argmin the
        winner. 2-ch banks pass vacuously."""
        if self.channels <= 2 or self.space == "feature":
            return True
        w_t = [1.0 if d < 2 else 0.0 for d in range(self.channels)]
        dists = self.dists_nd(q, w_t)
        if not dists:
            return False
        return min(dists, key=lambda kv: kv[1])[0] == hit

    def glosses(self):
        return {t.gloss: len(t.samples) for t in self.templates}

    # -- persistence: the templates-*.json shape ---------------------------
    def to_json_obj(self):
        return {
            "templates": [
                {"gloss": t.gloss,
                 "samples": t.samples,
                 **({"user_samples": t.user_samples} if t.user_samples else {})}
                for t in self.templates
            ],
            "reject_threshold": self.reject_threshold,
            "space": self.space,
            "channels": self.channels,
            "dim_weights": self.dim_weights,
            "shape_dims": self.shape_dims,
        }

    @classmethod
    def from_json_obj(cls, o):
        bank = cls(o.get("reject_threshold", 0.45),
                   o.get("space", "pixel"),
                   o.get("channels", 2),
                   o.get("dim_weights") or [],
                   o.get("shape_dims") or [])
        for t in o.get("templates", []):
            tt = Template(t["gloss"])
            tt.samples = [[[float(v) for v in p] for p in s]
                          for s in t.get("samples", [])]
            tt.user_samples = t.get("user_samples", 0)
            bank.templates.append(tt)
        return bank


# ---------------------------------------------------------------------------
# Temporal contract (alive-temporal): motion energy, eos window, Endpointer.
# ---------------------------------------------------------------------------

def motion_energy(prev, cur):
    """Mean absolute per-coordinate displacement. Length mismatch or
    empty = 0 (alive-temporal verbatim)."""
    if not prev or len(prev) != len(cur):
        return 0.0
    return sum(abs(a - b) for a, b in zip(prev, cur)) / len(prev)


def eos_windowed(entries, now_ms, window_ms=EOS_WINDOW_MS,
                 min_samples=EOS_MIN_SAMPLES, floor=EP_LOW_MOTION):
    """Fraction of trailing `window_ms` (at_ms, motion) samples below
    `floor`, extended back to >= min_samples — rate-independent stillness
    evidence (alive-temporal eos_windowed verbatim)."""
    if not entries:
        return 0.0
    lo = now_ms - window_ms
    n = still = 0
    for t, m in reversed(entries):
        if n >= min_samples and t < lo:
            break
        n += 1
        if m < floor:
            still += 1
    return still / n


class EndpointConfig:
    def __init__(self, tentative_pause_ms=EP_TENTATIVE_MS,
                 commit_pause_ms=EP_COMMIT_MS,
                 min_segment_ms=EP_MIN_SEGMENT_MS,
                 boundary_p=EP_BOUNDARY_P,
                 low_motion=EP_LOW_MOTION,
                 max_pause_ms=EP_MAX_PAUSE_MS,
                 motion_confirm_gap_ms=EP_MOTION_CONFIRM_GAP_MS,
                 early_commit_pause_ms=0,
                 fluent_dip_ms=0, fluent_dip_cap=0.0,
                 fluent_peak_min=0.10, commit_on_drop=False):
        self.tentative_pause_ms = tentative_pause_ms
        self.commit_pause_ms = commit_pause_ms
        self.min_segment_ms = min_segment_ms
        self.boundary_p = boundary_p
        self.low_motion = low_motion
        self.max_pause_ms = max_pause_ms
        self.motion_confirm_gap_ms = motion_confirm_gap_ms
        self.early_commit_pause_ms = early_commit_pause_ms
        self.fluent_dip_ms = fluent_dip_ms
        self.fluent_dip_cap = fluent_dip_cap
        self.fluent_peak_min = fluent_peak_min
        self.commit_on_drop = commit_on_drop


# Event tags — EndpointEvent in snake_case like the Rust serde tag.
TENTATIVE = "tentative"
COMMIT = "commit"
CANCELLED = "cancelled"


class Endpointer:
    """Streaming segmentation over landmark frames — the alive-temporal
    state machine, verbatim: Idle/Signing/Pausing, still-hold commit,
    jitter debounce, fluent velocity-dip leg, drop cancel."""

    def __init__(self, cfg=None):
        self.cfg = cfg or EndpointConfig()
        self.reset()
        self.prev_ms = 0
        self.last_motion_ms = 0
        self.seg_start = 0

    def reset(self):
        self.state = "idle"
        self.active_ms = 0
        self.tentative_fired = False
        self.pending_move_ms = None
        self.dip_since = None
        self.dip_n = 0
        self.dip_still_n = 0
        self.seg_peak = 0.0
        self.last_dip_onset = None

    def segment_start(self):
        return self.seg_start if self.state != "idle" else None

    def _fluent_cap(self):
        return self.cfg.fluent_dip_cap if self.cfg.fluent_dip_cap > 0.0 \
            else self.cfg.low_motion

    def _check_fluent_dip(self, at_ms):
        if self.cfg.fluent_dip_ms == 0 or self.dip_since is None:
            return None
        if at_ms - self.dip_since < self.cfg.fluent_dip_ms:
            return None
        if self.dip_n < 3 or self.active_ms < self.cfg.min_segment_ms:
            return None
        if self.seg_peak < self.cfg.fluent_peak_min:
            return None
        ev = {"event": COMMIT, "start_ms": self.seg_start,
              "end_ms": self.dip_since,
              "eos_p": self.dip_still_n / max(self.dip_n, 1),
              "fluent": True}
        self.reset()
        return ev

    def _check_pause(self, at_ms, eos_p, early_ok):
        pause = at_ms - self.last_motion_ms
        if pause >= self.cfg.max_pause_ms:
            self.reset()
            return {"event": CANCELLED, "at_ms": at_ms}
        if self.active_ms < self.cfg.min_segment_ms:
            return None
        ev = self._check_fluent_dip(at_ms)
        if ev:
            return ev
        if not self.tentative_fired and pause >= self.cfg.tentative_pause_ms:
            self.tentative_fired = True
            return {"event": TENTATIVE, "start_ms": self.seg_start,
                    "end_ms": at_ms}
        floor = (min(self.cfg.early_commit_pause_ms, self.cfg.commit_pause_ms)
                 if early_ok and self.cfg.early_commit_pause_ms > 0
                 else self.cfg.commit_pause_ms)
        if pause >= floor and eos_p >= self.cfg.boundary_p:
            ev = {"event": COMMIT, "start_ms": self.seg_start,
                  "end_ms": at_ms, "eos_p": eos_p, "fluent": False}
            self.reset()
            return ev
        return None

    def _drop_commit_ok(self, at_ms, eos_p, early_ok):
        if self.state != "pausing" or not early_ok:
            return False
        pause = at_ms - self.last_motion_ms
        if pause >= self.cfg.max_pause_ms:
            return False
        floor = (min(self.cfg.early_commit_pause_ms, self.cfg.commit_pause_ms)
                 if self.cfg.early_commit_pause_ms > 0
                 else self.cfg.commit_pause_ms)
        return pause >= floor and eos_p >= self.cfg.boundary_p

    def flush(self, at_ms, eos_p):
        """End-of-source: an open segment gets its one final vote."""
        if self.state == "idle":
            return None
        ev = ({"event": COMMIT, "start_ms": self.seg_start,
               "end_ms": at_ms, "eos_p": eos_p, "fluent": False}
              if self.active_ms >= self.cfg.min_segment_ms
              else {"event": CANCELLED, "at_ms": at_ms})
        self.reset()
        return ev

    def push(self, at_ms, motion, eos_p, present):
        return self.push_hint(at_ms, motion, eos_p, present, False)

    def push_hint(self, at_ms, motion, eos_p, present, early_ok):
        """One frame. Returns at most one event dict. Gaps in at_ms ok."""
        if not present:
            if self.state != "idle":
                if (self.cfg.commit_on_drop
                        and self._drop_commit_ok(at_ms, eos_p, early_ok)):
                    ev = {"event": COMMIT, "start_ms": self.seg_start,
                          "end_ms": at_ms, "eos_p": eos_p, "fluent": False}
                    self.reset()
                    return ev
                self.reset()
                return {"event": CANCELLED, "at_ms": at_ms}
            return None
        dt = at_ms - self.prev_ms if self.prev_ms else 0
        had_prev = self.prev_ms != 0
        self.prev_ms = at_ms
        moving = motion >= self.cfg.low_motion

        # Fluent dip tracking (segment-open states).
        if self.state != "idle":
            if motion <= self._fluent_cap():
                if self.dip_since is None:
                    self.dip_since = at_ms
                    self.last_dip_onset = at_ms
                    self.dip_n = 0
                    self.dip_still_n = 0
                self.dip_n += 1
                if motion < self.cfg.low_motion:
                    self.dip_still_n += 1
            else:
                self.dip_since = None
                self.dip_n = 0
                self.dip_still_n = 0
            if moving and motion > self.seg_peak:
                self.seg_peak = motion

        out = None
        if self.state == "idle":
            if moving:
                self.state = "signing"
                self.seg_start = at_ms
                self.last_motion_ms = at_ms
                self.active_ms = min(dt, 250) if had_prev else 0
                self.tentative_fired = False
                self.seg_peak = motion
        elif self.state == "signing":
            if moving:
                self.last_motion_ms = at_ms
                self.active_ms += min(dt, 250)
            else:
                self.state = "pausing"
                out = self._check_pause(at_ms, eos_p, early_ok)
        else:  # pausing
            if moving:
                gap_ok = (self.cfg.motion_confirm_gap_ms == 0 or
                          (self.pending_move_ms is not None and
                           at_ms - self.pending_move_ms
                           <= self.cfg.motion_confirm_gap_ms))
                if gap_ok:
                    self.state = "signing"
                    self.last_motion_ms = at_ms
                    self.active_ms += min(dt, 250)
                    self.pending_move_ms = None
                else:
                    self.pending_move_ms = at_ms
            else:
                out = self._check_pause(at_ms, eos_p, early_ok)
        if out is not None:
            return out
        return self._check_fluent_dip(at_ms)
