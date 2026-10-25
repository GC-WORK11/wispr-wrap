#!/usr/bin/env python3
"""signbank — per-user teach-by-example sign bank for the merged product.

Pure-stdlib port of the Sign-Flow DTW template bank
(sign-flow: crates/alive-sign/src/matcher.rs) onto browser keypoint frames.
This is engine (A) "PURE-PYTHON DTW bank" in the merged-product PRD: zero
deps, works anywhere the daemon runs.

=====================================================================
PARITY NOTE — what is EXACT vs SIMPLIFIED vs matcher.rs (read me first)
=====================================================================

EXACT (same math, port line-for-line):
  * resample32: uniform arc-length resample to 32 points, incl. edge
    cases (degenerate arc -> first point repeated; <2 pts passthrough).
    Speed-invariant by design: fast vs slow signing of the same sign
    lands on the same parameterization. `t_ms` is deliberately unused.
  * normalize: centroid-origin translate + RMS-radius scale on the WHOLE
    resampled path (Pixel space). Translation + scale invariant;
    rotation/direction SENSITIVE (left swipe != right swipe).
  * dtw_banded: Sakoe-Chiba DP, band = max(4, |n-m|+1, 1), cell cost =
    weighted Euclidean over shared-finite dims, result / max(n, m) —
    "mean cost per aligned step". Includes the proofs/dtw-fix band-edge
    fix (cur[lo-1] = INF every row); pinned to a full-matrix reference
    DP in tests, like Rust pins dtw_w to dtw_full.
  * wdist NaN semantics: dims not finite in BOTH points are never free —
    the cost is rescaled by total_w/used_w (missing dims pay the observed
    dims' average). No shared finite dims -> INF. Ported verbatim.
  * Fused channel shape evidence: matcher.rs's calibrated operating
    point broadcasts ONE clip-level median descriptor over every frame,
    making the shape contribution a per-cell CONSTANT. We therefore
    compute it in closed form per exemplar pair —
    cost(i,j) = sqrt((traj_ss + shape_ss) * total_w/used_w) — which is
    algebraically identical to running the 86-dim wdist inside the DP
    (verified in tests against a literal N-dim DP), ~40x cheaper.
  * Gloss distance = min over exemplars; winner/second from a full
    linear scan (Rust's retrieval index is provably identical to the
    linear scan — we just don't need it at <=30 glosses; see below).
  * accept rule: best_d <= reject_threshold AND traj-anchor veto.
    score = 1/(1+d). margin = second - best, reported, never gated.
  * Traj-anchor veto = traj_channel_agrees (main.rs ~8912): on banks
    with a real second channel, the trajectory dims alone must argmin
    the winner; shape may break ties inside a traj cluster but may not
    pull a gloss the motion rejects. We apply it unconditionally — when
    nothing carries shape evidence the fused and traj rankings are
    identical, so it passes vacuously, same as Rust's channels<=2 case.
  * FIFO cap 5 exemplars/gloss, >=8 usable frames to accept a sample.
  * Clip-shape fill: a never-observed dim gets median_descriptor's own
    0.0 fill ("same fill the eval persisted") — consequence: an absent
    HAND fills 0.0 rather than NaN, so hand-PRESENCE is measured
    evidence (a one-hand sign can't tie a two-hand sign on the observed
    hand alone). Follows Rust; noted because it changes semantics vs a
    naive NaN-everywhere treatment.

SIMPLIFIED / DIFFERENT (honest list, not hidden):
  * Shape descriptor is the SPEC's lighter version, not matcher's:
    per frame each hand's 21 MediaPipe pts are normalized by the hand
    bounding-box (center / max-dim) and flattened -> 42 dims per hand,
    both hands -> 84 dims; unobserved JOINTS stay NaN at frame level
    (median ignores them — taxed, never free), a never-observed HAND
    takes the eval's 0.0 fill (see EXACT list above). matcher.rs uses
    an engineered 19-dim descriptor (per-finger
    extension cosines, tip spreads, tip-wrist reach, palm orientation,
    roll, aperture) on wrist+palm-width units over the DOMINANT hand
    only. Ours is cruder — bbox normalization blurs palm-absolute
    orientation and mixes it with finger curl — but needs no joint
    semantics and covers two-hand signs (namaste) that a dominant-hand
    descriptor cannot see. Per-dim weight 0.1 over <=84 dims ~matches
    the total channel weight of the calibrated w=[1,1,0.5x19] point.
  * Pixel space only. matcher.rs also has Body (shoulder-frame units —
    location carries meaning) and Feature (shape-only). kp42 wire has
    no shoulders, so Body is unimplementable here; that is a real loss
    for location-dependent signs, not a hiding.
  * Two-hand shape is an EXTENSION beyond both spec (21 pts) and
    matcher (dominant hand): required by the product's namaste sign.
    The trajectory channel stays single dominant hand, as in Rust.
  * No Envelope/QBoxes retrieval index, no capped-DTW early abandon,
    no ANN cap. Linear scan: identical results, more work. Measured
    ~3.3ms/match on a 10-gloss x3 bank (tests/test_signbank.py prints
    it) — fine at segment end (PRD budget), and it does NOT scale to
    the Rust side's 300+ gloss banks. If banks grow, port the index,
    don't approximate the scan.
  * Threshold default 0.6 mirrors the sign_eval fused operating point
    (T=0.6 at w=[1,1,0.5x19] on kp53 body banks). kp42 browser
    keypoints are a different sensor — treat 0.6 as a STARTING point
    and recalibrate with calibrate_threshold() on real captures. The
    threshold is serialized per bank, never a hidden constant.
  * No endpointer, no CTC, no act layer — segmentation is wfd's lane;
    this file is only the bank. No Strict consensus default: shipped
    Rust rule is traj-veto; ALIVE_FUSION=consensus (both channels must
    argmin) is exposed as match(consensus=True), off by default.
  * TWO deliberate deviations beyond "simplified", both serialized
    bank params (veto_eps, still_frac), both forced by real input:
      - veto_eps (0.05): matcher's veto is STRICT argmin. Same-traj/
        different-shape glosses tie the traj channel within noise
        (~0.001-0.01) — strict argmin vetoes the shape winner on
        template-order coin flips, silently breaking the exact case the
        fused bank exists for. Our winner passes when its traj distance
        is within veto_eps of the argmin's. Weaker than strict argmin
        by exactly veto_eps; genuinely different motions (~0.5+) still
        veto. Flagged for the Rust port-back.
      - still_frac (0.08): matcher relies on arc~0 collapsing a still
        path; camera jitter is never arc-0, so a held hand would
        RMS-normalize into meaningless unit-RMS noise that drowns the
        shape channel. Raw RMS radius < max(2px, still_frac * mean
        hand-bbox-size) collapses to its centroid BEFORE resampling.
        Trade-off: a deliberate micro-motion under the gate is a hold.
  * Honest known weakness measured in tests: bbox-normalized raw
    flatten is a BLURRIER shape channel than the engineered descriptor
    — synthetic open-vs-fist median-shape differs by ~0.17 rms/dim
    (fused separation ~0.35, vs mirrored-traj ~1.0+). It decides
    correctly (tests: same-traj open/fist, two-hand namaste) but the
    margin is thinner — calibrate thresholds on REAL captures, and if
    confusion appears, port the engineered descriptor rather than
    loosening the threshold.

WIRE FORMAT (in): frames = [{"t_ms": int, "points": [{"x","y","score"} x42]}]
  points[0:21] = left hand (MediaPipe order), points[21:42] = right;
  absent hand = all scores 0. A 21-pt frame is read as one hand given
  by optional frame["hand"] ("left"|"right", default "right").

BANK FILE: ~/.config/wf/signbank.json, mode 600, atomic write.
"""

import json
import math
import os
import tempfile

RESAMPLE_N = 32          # matcher.rs RESAMPLE_N
MATCH_BAND = 4           # matcher.rs MATCH_BAND
MIN_POINTS = 8           # matcher.rs add_sample_nd / best_nd floor
MAX_EXEMPLARS = 5        # matcher.rs FIFO cap
HAND_KP = 21             # MediaPipe landmarks per hand
N_HANDS = 2
SHAPE_DIMS = HAND_KP * 2 * N_HANDS   # 84
DEFAULT_PATH = os.path.expanduser("~/.config/wf/signbank.json")
INF = float("inf")
NAN = float("nan")


# ---------------------------------------------------------------------
# Exact ports: resample / normalize / banded DTW (matcher.rs semantics)
# ---------------------------------------------------------------------

def _resample(pts, n):
    """Arc-length resample to exactly n points — port of matcher::resample."""
    if len(pts) < 2 or n < 2:
        return list(pts)
    cum = [0.0] * len(pts)
    for i in range(1, len(pts)):
        dx = pts[i][0] - pts[i - 1][0]
        dy = pts[i][1] - pts[i - 1][1]
        cum[i] = cum[i - 1] + math.sqrt(dx * dx + dy * dy)
    total = cum[-1]
    if total <= 1e-9:
        return [tuple(pts[0])] * n
    out = []
    j = 0
    for k in range(n):
        target = total * k / (n - 1)
        while j + 1 < len(pts) - 1 and cum[j + 1] < target:
            j += 1
        seg = max(cum[j + 1] - cum[j], 1e-9)
        t = min(max((target - cum[j]) / seg, 0.0), 1.0)
        out.append((pts[j][0] + (pts[j + 1][0] - pts[j][0]) * t,
                    pts[j][1] + (pts[j + 1][1] - pts[j][1]) * t))
    return out


def resample32(traj):
    """Public per spec: arc-length resample a [(x,y)...] path to 32 points."""
    return _resample([tuple(p) for p in traj], RESAMPLE_N)


def _normalize(pts):
    """Centroid origin + RMS-radius scale — port of matcher::normalize.
    Direction preserved; where/how big you sign stops mattering."""
    if not pts:
        return []
    n = float(len(pts))
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    rms = math.sqrt(sum((p[0] - mx) ** 2 + (p[1] - my) ** 2 for p in pts) / n)
    s = max(rms, 1e-6)
    return [((p[0] - mx) / s, (p[1] - my) / s) for p in pts]


def _dtw_add(a, b, band, add):
    """Banded DTW where every cell cost is sqrt(dx^2+dy^2+add).

    add=0 is matcher::dtw_w on 2-dim points verbatim (incl. the
    band-edge cur[lo-1]=INF fix and the /max(n,m) normalization).
    add>0 folds a per-pair constant channel (broadcast clip shape)
    into the SAME DP — identical math to an N-dim fused point whose
    non-spatial dims are constant across frames.
    """
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return INF
    band = max(band, abs(n - m) + 1, 1)
    prev = [INF] * (m + 1)
    cur = [INF] * (m + 1)
    prev[0] = 0.0
    sqrt = math.sqrt
    for i in range(1, n + 1):
        lo = i - band
        if lo < 1:
            lo = 1
        hi = i + band
        if hi > m:
            hi = m
        cur[lo - 1] = INF  # stale left-edge slot: out-of-band stays INF
        ai = a[i - 1]
        ax, ay = ai[0], ai[1]
        for j in range(lo, hi + 1):
            bj = b[j - 1]
            dx = ax - bj[0]
            dy = ay - bj[1]
            best = prev[j]
            c = cur[j - 1]
            if c < best:
                best = c
            c = prev[j - 1]
            if c < best:
                best = c
            cur[j] = sqrt(dx * dx + dy * dy + add) + best
        prev, cur = cur, prev
    return prev[m] / max(n, m)


def dtw_banded(a, b, band=MATCH_BAND):
    """Public per spec: banded Sakoe-Chiba DTW on [(x,y)...] paths."""
    return _dtw_add(a, b, band, 0.0)


def _shape_ss(a, b, w):
    """wdist^2 restricted to the shape dims of two clip vectors:
    returns (weighted sum of squares over shared-finite dims, n_shared).
    A None vector reads as all-NaN (matcher: absent evidence, taxed)."""
    if a is None or b is None:
        return 0.0, 0
    ss = 0.0
    k = 0
    for i in range(min(len(a), len(b))):
        x, y = a[i], b[i]
        if math.isfinite(x) and math.isfinite(y):
            d = x - y
            ss += w * d * d
            k += 1
    return ss, k


# ---------------------------------------------------------------------
# Frame ingestion: kp42 wire -> dominant-hand centroid traj + shapes
# ---------------------------------------------------------------------

def _pt(p):
    """Tolerant point parse: {x,y,score} dict or [x,y,score] list."""
    if isinstance(p, dict):
        s = p.get("score", 1.0)
        return (float(p.get("x", 0.0)), float(p.get("y", 0.0)),
                1.0 if s is None else float(s))
    x = float(p[0]) if len(p) > 0 else 0.0
    y = float(p[1]) if len(p) > 1 else 0.0
    s = float(p[2]) if len(p) > 2 else 1.0
    return (x, y, s)


def _hand_obs(pts, min_score, min_keys):
    """One hand's (centroid, shape42, bbox_scale) for a frame —
    (None, None, 0.0) when the hand fails the gate.

    centroid: score-weighted mean of confident keypoints — port of
    matcher::hand_centroid (same min_score/min_keys gate).
    shape42: 21 pts normalized by the confident-point bounding box
    (center / max-dim); unconfident joints stay NaN — never zeroed.
    """
    conf = [(i, p) for i, p in enumerate(pts) if p[2] >= min_score]
    if len(conf) < min_keys:
        return None, None, 0.0
    sw = sum(p[2] for _, p in conf)
    if sw <= 0.0:
        return None, None, 0.0
    cx = sum(p[0] * p[2] for _, p in conf) / sw
    cy = sum(p[1] * p[2] for _, p in conf) / sw
    xs = [p[0] for _, p in conf]
    ys = [p[1] for _, p in conf]
    bx = (min(xs) + max(xs)) * 0.5
    by = (min(ys) + max(ys)) * 0.5
    bs = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
    shape = [NAN] * (HAND_KP * 2)
    for i, p in conf:
        shape[2 * i] = (p[0] - bx) / bs
        shape[2 * i + 1] = (p[1] - by) / bs
    return (cx, cy), shape, bs


def _frame_hands(frame, min_score, min_keys):
    """Split a wire frame into [(centroid|None, shape42|None, scale)] x2."""
    raw = frame.get("points") or []
    pts = [_pt(p) for p in raw]
    if len(pts) == HAND_KP:  # single-hand stream -> frame["hand"] decides
        idx = 1 if str(frame.get("hand", "right")).lower() == "right" else 0
        hands = [None, None]
        hands[idx] = pts
    else:
        hands = [pts[0:HAND_KP], pts[HAND_KP:2 * HAND_KP]]
    out = []
    for h in hands:
        if h is None or len(h) < HAND_KP:
            out.append((None, None, 0.0))
        else:
            out.append(_hand_obs(h, min_score, min_keys))
    return out


def _upper_median(rows, dims):
    """Clip-level shape aggregate — port of matcher::median_descriptor.
    Per dim: upper median of finite values; never-observed dims fill 0.0
    (the same fill the eval persisted — queries and banked exemplars are
    built by one rule). Returns (vector|None, n_all_nan)."""
    if not rows:
        return None, 0
    out = []
    all_nan = 0
    for d in range(dims):
        vals = sorted(r[d] for r in rows if math.isfinite(r[d]))
        if vals:
            out.append(vals[len(vals) // 2])
        else:
            out.append(0.0)
            all_nan += 1
    return out, all_nan


# ---------------------------------------------------------------------
# The bank
# ---------------------------------------------------------------------

class MatchResult:
    """What match() returns. Contract fields: gloss, dist, accepted,
    second_gloss, margin (= second - best). Extra read-only fields are
    for the UI strip and logs: score, label, action, tier, reason,
    traj_gloss (the motion channel's own argmin — veto transparency)."""
    __slots__ = ("gloss", "dist", "accepted", "second_gloss", "margin",
                 "score", "label", "action", "tier", "reason",
                 "traj_gloss", "shape_gloss")

    def __init__(self, gloss=None, dist=INF, accepted=False,
                 second_gloss=None, margin=INF, score=0.0, label="",
                 action=None, tier=0, reason="", traj_gloss=None,
                 shape_gloss=None):
        self.gloss = gloss
        self.dist = dist
        self.accepted = accepted
        self.second_gloss = second_gloss
        self.margin = margin
        self.score = score
        self.label = label
        self.action = action
        self.tier = tier
        self.reason = reason
        self.traj_gloss = traj_gloss
        self.shape_gloss = shape_gloss

    def __repr__(self):
        return (f"MatchResult(gloss={self.gloss!r}, dist={self.dist:.4f}, "
                f"accepted={self.accepted}, second={self.second_gloss!r}, "
                f"margin={self.margin:.4f}, reason={self.reason!r})")


class SignBank:
    """Per-user sign bank. The contract the other lanes code against.

      bank = SignBank.load()                 # ~/.config/wf/signbank.json
      bank.add_exemplar("OPEN-BROWSER", frames, label="open browser",
                        action={"argv": ["google-chrome"], "tier": 0})
      r = bank.match(frames)                 # -> MatchResult
      bank.save()
    """

    def __init__(self, reject_threshold=0.6, min_score=0.5, min_keys=8,
                 shape_dim_weight=0.1, veto_eps=0.05, still_frac=0.08,
                 space="pixel"):
        self.reject_threshold = float(reject_threshold)
        self.min_score = float(min_score)
        self.min_keys = int(min_keys)
        # per-dim weight of each of the 84 shape channels; ~matches the
        # total shape weight of the calibrated w=[1,1,0.5x19] Rust point.
        self.shape_dim_weight = float(shape_dim_weight)
        # Tie band for the traj-anchor veto — a deliberate DEVIATION
        # from matcher's strict argmin: glosses taught on the SAME
        # trajectory with different handshapes tie the traj channel
        # within noise (~0.001-0.01); a strict argmin would veto the
        # shape winner on template-order coin flips, breaking the exact
        # case the fused bank exists for. veto passes when the winner's
        # traj distance is within veto_eps of the traj channel's argmin
        # — ties resolve by shape, genuinely different motions still
        # veto. Serialized, calibratable, never hidden.
        self.veto_eps = float(veto_eps)
        # Still-hold radius gate as a fraction of the hand's own bbox
        # size (see prep's STILLNESS GATE comment).
        self.still_frac = float(still_frac)
        self.space = space          # only "pixel" exists here (no shoulders)
        self.templates = []         # [{gloss,label,tier,action,user_samples,samples:[{traj,shape}]}]
        self.path = DEFAULT_PATH

    # -- prep --------------------------------------------------------

    def prep(self, frames):
        """frames -> (traj32 normalized, shape84|None, n_centroid_pts, dom).

        The single rule both add_exemplar and match build evidence by.
        traj: dominant-hand score-weighted centroid path, resampled to
        32 then RMS-normalized (matcher Pixel prep, exact).
        shape84: [left42 | right42] clip medians — per-hand upper
        median of the per-frame bbox-normalized 21-pt flattens; a hand
        never observed gets median_descriptor's 0.0 fill, so
        hand-PRESENCE itself is measured evidence.
        """
        cents = [[], []]
        rows = [[], []]
        scales = [[], []]
        for fr in frames or []:
            obs = _frame_hands(fr, self.min_score, self.min_keys)
            for h in range(N_HANDS):
                c, s, bs = obs[h]
                if c is not None:
                    cents[h].append(c)
                    scales[h].append(bs)
                if s is not None:
                    rows[h].append(s)
        # dominant hand = the one observed in more frames (matcher:
        # "hand with more ticks wins"); tie -> right hand.
        dom = 1 if len(cents[1]) >= len(cents[0]) else 0
        raw = cents[dom]
        npts = len(raw)
        if npts >= 2:
            # STILLNESS GATE (extension beyond matcher.rs — documented):
            # matcher lets arc~0 collapse naturally, but real camera
            # jitter on a held hand is NOT arc-0 — RMS-normalizing it
            # would amplify sensor noise into a meaningless unit-RMS
            # wiggle and drown the shape channel (the only evidence a
            # still sign has). A path whose raw RMS radius is a small
            # fraction of the hand's own size is a hold, not a motion:
            # collapse it to its centroid BEFORE resampling so it lands
            # on the same all-zero normalized point as any other hold.
            mx = sum(p[0] for p in raw) / npts
            my = sum(p[1] for p in raw) / npts
            rms = math.sqrt(sum((p[0] - mx) ** 2 + (p[1] - my) ** 2
                                for p in raw) / npts)
            hand_px = (sum(scales[dom]) / len(scales[dom])) \
                if scales[dom] else 0.0
            if rms < max(2.0, self.still_frac * hand_px):
                raw = [(mx, my)] * npts
        traj = _normalize(_resample(raw, RESAMPLE_N))
        # Per-hand clip shape = upper median of observed dims; a hand
        # never observed gets the 0.0 fill — matcher::median_descriptor's
        # own rule ("same fill the eval persisted"), which makes
        # hand-PRESENCE itself measurable evidence (a one-hand sign vs a
        # two-hand sign must not tie on the observed hand alone).
        lv, _ = _upper_median(rows[0], HAND_KP * 2) if rows[0] else (None, 0)
        rv, _ = _upper_median(rows[1], HAND_KP * 2) if rows[1] else (None, 0)
        if lv is None and rv is None:
            shape = None
        else:
            shape = (lv if lv is not None else [0.0] * (HAND_KP * 2)) + \
                    (rv if rv is not None else [0.0] * (HAND_KP * 2))
        return traj, shape, npts, dom

    # -- bank ops ----------------------------------------------------

    def _find(self, gloss):
        for t in self.templates:
            if t["gloss"] == gloss:
                return t
        for t in self.templates:  # case-insensitive fallback, like Rust
            if t["gloss"].lower() == gloss.lower():
                return t
        return None

    def add_exemplar(self, gloss, frames, label="", action=None):
        """Teach one performance of `gloss`. False when <8 usable frames
        (matcher's >=8 raw point floor) or no hand ever observed.
        >MAX_EXEMPLARS evicts the oldest (FIFO, matcher semantics)."""
        gloss = str(gloss).strip()
        if not gloss:
            return False
        traj, shape, npts, _dom = self.prep(frames)
        if npts < MIN_POINTS or not traj:
            return False
        t = self._find(gloss)
        if t is None:
            t = {"gloss": gloss, "label": label or "", "tier": 0,
                 "action": None, "user_samples": 0, "samples": []}
            self.templates.append(t)
        t["samples"].append({"traj": [[p[0], p[1]] for p in traj],
                             "shape": shape})
        t["user_samples"] = t.get("user_samples", 0) + 1
        while len(t["samples"]) > MAX_EXEMPLARS:
            t["samples"].pop(0)
        t["user_samples"] = min(t["user_samples"], len(t["samples"]))
        if label:
            t["label"] = label
        if action is not None:
            t["action"] = action
            if isinstance(action, dict) and "tier" in action:
                try:
                    t["tier"] = int(action["tier"])
                except (TypeError, ValueError):
                    pass
        return True

    def set_action(self, gloss, label=None, action=None, tier=None):
        """Rebind label/action/tier without adding an exemplar."""
        t = self._find(gloss)
        if t is None:
            return False
        if label is not None:
            t["label"] = label
        if action is not None:
            t["action"] = action
        if tier is not None:
            t["tier"] = int(tier)
        elif isinstance(action, dict) and "tier" in action:
            t["tier"] = int(action["tier"])
        return True

    def remove(self, gloss):
        t = self._find(gloss)
        if t is None:
            return False
        self.templates.remove(t)
        return True

    def list(self):
        return [{"gloss": t["gloss"], "label": t.get("label", ""),
                 "n_exemplars": len(t["samples"]),
                 "action": t.get("action"), "tier": t.get("tier", 0)}
                for t in self.templates]

    # -- matching ----------------------------------------------------

    def _score(self, frames):
        """Scoring without the accept gate. None = the refusal cases
        (thin query, empty bank) matcher encodes as Option::None.
        Else a dict: best/second (gloss,dist) on the FUSED channel plus
        each channel's own argmin and the winner's traj distance (the
        veto needs the tie band, not just the argmin name)."""
        traj, shape, npts, _dom = self.prep(frames)
        if npts < MIN_POINTS or not traj or not self.templates:
            return None
        w_s = self.shape_dim_weight
        w_traj = 2.0
        total_w = w_traj + w_s * SHAPE_DIMS
        traj_d = []
        fused_d = []
        shape_d = []
        for t in self.templates:
            bt = bf = bs = INF
            for s in t["samples"]:
                d_t = _dtw_add(traj, s["traj"], MATCH_BAND, 0.0)
                if d_t < bt:
                    bt = d_t
                shape_ss, k = _shape_ss(shape, s.get("shape"), w_s)
                if k > 0:
                    # shared-finite shape dims exist on both sides:
                    # fused cell = sqrt((traj_ss + shape_ss) * R), R const
                    used = w_traj + w_s * k
                    r = total_w / used
                    d_f = _dtw_add(traj, s["traj"], MATCH_BAND,
                                   shape_ss) * math.sqrt(r)
                    d_s = math.sqrt(shape_ss * (w_s * SHAPE_DIMS) /
                                    (w_s * k))
                else:
                    # no shared shape evidence: wdist rescale collapses
                    # to a uniform tax on the traj channel — the DP
                    # scales linearly, so no second pass is needed.
                    d_f = d_t * math.sqrt(total_w / w_traj)
                    d_s = INF
                if d_f < bf:
                    bf = d_f
                if d_s < bs:
                    bs = d_s
            traj_d.append(bt)
            fused_d.append(bf)
            shape_d.append(bs)
        order = sorted(range(len(self.templates)),
                       key=lambda i: fused_d[i])
        bi = order[0]
        t_min = min(range(len(traj_d)), key=lambda i: traj_d[i])
        s_min = min(range(len(shape_d)), key=lambda i: shape_d[i])
        return {"gloss": self.templates[bi]["gloss"], "dist": fused_d[bi],
                "second_gloss": (self.templates[order[1]]["gloss"]
                                 if len(order) > 1 else None),
                "second": fused_d[order[1]] if len(order) > 1 else INF,
                "traj_gloss": self.templates[t_min]["gloss"],
                "traj_d": traj_d[t_min], "traj_d_winner": traj_d[bi],
                "shape_gloss": self.templates[s_min]["gloss"]}

    def match(self, frames, consensus=False):
        """Score a performed segment. accepted = best_d <= threshold AND
        traj-veto (and full channel consensus when consensus=True).
        gloss/dist/second/margin are always populated when the bank and
        query are valid — rejected results still report the read."""
        sc = self._score(frames)
        if sc is None:
            reason = "empty-bank" if not self.templates else "thin-query"
            return MatchResult(reason=reason)
        gloss = sc["gloss"]
        best_d, second_d = sc["dist"], sc["second"]
        # traj-anchor veto: the winner's own traj distance must sit
        # within veto_eps of the traj channel's argmin (see __init__).
        veto_ok = sc["traj_d_winner"] <= sc["traj_d"] + self.veto_eps
        cons_ok = (not consensus) or \
            (sc["traj_gloss"] == gloss and sc["shape_gloss"] == gloss)
        accepted = (best_d <= self.reject_threshold) and veto_ok and cons_ok
        if best_d > self.reject_threshold:
            reason = "threshold"
        elif not veto_ok:
            reason = "veto"
        elif not cons_ok:
            reason = "consensus"
        else:
            reason = "ok"
        t = self._find(gloss)
        margin = (second_d - best_d) if math.isfinite(best_d) else INF
        return MatchResult(gloss=gloss, dist=best_d, accepted=accepted,
                           second_gloss=sc["second_gloss"],
                           margin=margin,
                           score=1.0 / (1.0 + best_d),
                           label=t.get("label", "") if t else "",
                           action=t.get("action") if t else None,
                           tier=t.get("tier", 0) if t else 0,
                           reason=reason, traj_gloss=sc["traj_gloss"],
                           shape_gloss=sc["shape_gloss"])

    # -- calibration -------------------------------------------------

    def eval_threshold(self, labeled, threshold=None):
        """Score a labeled set at `threshold` (default: current).
        labeled = [(frames, expected_gloss_or_None)]. Returns honest
        counts: a wrong accept and a false accept are DIFFERENT sins —
        reported apart, never merged."""
        t = self.reject_threshold if threshold is None else float(threshold)
        out = {"threshold": t, "n": 0, "accept": 0, "correct": 0,
               "wrong_gloss": 0, "false_accept": 0, "abstain": 0,
               "vetoed": 0, "per_sample": []}
        for frames, expected in labeled:
            sc = self._score(frames)
            out["n"] += 1
            if sc is None:
                out["abstain"] += 1
                ok = expected is None
                out["correct"] += 1 if ok else 0
                out["per_sample"].append({"expected": expected,
                                          "got": None, "ok": ok})
                continue
            gloss, d = sc["gloss"], sc["dist"]
            veto_ok = sc["traj_d_winner"] <= sc["traj_d"] + self.veto_eps
            if not veto_ok:
                out["vetoed"] += 1
            got = gloss if (d <= t and veto_ok) else None
            ok = got == expected
            if got is not None:
                out["accept"] += 1
                if expected is None:
                    out["false_accept"] += 1
                elif got != expected:
                    out["wrong_gloss"] += 1
            else:
                out["abstain"] += 1
            out["correct"] += 1 if ok else 0
            out["per_sample"].append({"expected": expected, "got": got,
                                      "dist": d, "ok": ok})
        n = max(out["n"], 1)
        out["accuracy"] = out["correct"] / n
        out["recall"] = out["accept"] / n
        return out

    def calibrate_threshold(self, labeled, max_bad_accepts=0):
        """Sweep candidate thresholds on labeled data, pick + install the
        best honest one. Objective: minimize bad accepts (wrong gloss +
        false accept) first — under max_bad_accepts they are failures —
        then maximize recall. Ties break toward the midpoint with the
        larger margin to the next observed distance. Sets and returns
        {"threshold", "eval"} — caller decides whether to save()."""
        scored = [(self._score(f), e) for f, e in labeled]
        dists = sorted({round(s[0]["dist"], 9) for s in scored
                        if s[0] is not None and math.isfinite(s[0]["dist"])})
        cands = [dists[0] / 2.0] if dists else [self.reject_threshold]
        cands += [(a + b) / 2.0 for a, b in zip(dists, dists[1:])]
        if dists:
            cands.append(dists[-1] * 1.5 + 1e-9)
        best = None
        for t in cands:
            ev = {"n": len(scored), "accept": 0, "correct": 0, "bad": 0}
            for sc, expected in scored:
                got = None
                if sc is not None and sc["dist"] <= t and \
                        sc["traj_d_winner"] <= sc["traj_d"] + self.veto_eps:
                    got = sc["gloss"]
                if got is None:
                    ev["correct"] += 1 if expected is None else 0
                else:
                    ev["accept"] += 1
                    if expected is None or got != expected:
                        ev["bad"] += 1
                    else:
                        ev["correct"] += 1
            key = (-ev["bad"], ev["correct"], -t)
            if best is None or key > best[0]:
                best = (key, t, ev)
        _key, t, ev = best
        if ev["bad"] > max_bad_accepts:
            # honest report: NO threshold meets the bar on this data
            self.reject_threshold = t
            return {"threshold": t, "eval": ev, "met": False}
        self.reject_threshold = t
        return {"threshold": t, "eval": ev, "met": True}

    @staticmethod
    def confidence(dist):
        return 1.0 / (1.0 + dist)

    # -- persistence -------------------------------------------------

    def to_dict(self):
        return {
            "format": "wf-signbank/1",
            "engine": "python-dtw",
            "space": self.space,
            "channels": 2 + SHAPE_DIMS,
            "reject_threshold": self.reject_threshold,
            "min_score": self.min_score,
            "min_keys": self.min_keys,
            "shape_dim_weight": self.shape_dim_weight,
            "veto_eps": self.veto_eps,
            "still_frac": self.still_frac,
            "templates": [
                {"gloss": t["gloss"], "label": t.get("label", ""),
                 "tier": t.get("tier", 0), "action": t.get("action"),
                 "user_samples": t.get("user_samples", 0),
                 "samples": [
                     {"traj": [[float(p[0]), float(p[1])] for p in s["traj"]],
                      # NaN is not valid JSON — None marks unobserved dims
                      "shape": (None if s.get("shape") is None else
                                [v if math.isfinite(v) else None
                                 for v in s["shape"]])}
                     for s in t["samples"]]}
                for t in self.templates],
        }

    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict) or "templates" not in d:
            raise ValueError("not a signbank file")
        b = cls(reject_threshold=d.get("reject_threshold", 0.6),
                min_score=d.get("min_score", 0.5),
                min_keys=d.get("min_keys", 8),
                shape_dim_weight=d.get("shape_dim_weight", 0.1),
                veto_eps=d.get("veto_eps", 0.05),
                still_frac=d.get("still_frac", 0.08),
                space=d.get("space", "pixel"))
        for t in d.get("templates") or []:
            samples = []
            for s in t.get("samples") or []:
                shape = s.get("shape")
                if shape is not None:
                    shape = [(float(v) if v is not None else NAN)
                             for v in shape]
                samples.append({"traj": [(float(p[0]), float(p[1]))
                                         for p in s.get("traj") or []],
                                "shape": shape})
            b.templates.append({
                "gloss": str(t.get("gloss", "")),
                "label": t.get("label", ""),
                "tier": int(t.get("tier", 0) or 0),
                "action": t.get("action"),
                "user_samples": int(t.get("user_samples", 0) or 0),
                "samples": samples})
        return b

    def save(self, path=None):
        """Atomic write, mode 600 — the bank is biometric-ish data."""
        path = path or self.path or DEFAULT_PATH
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d or ".", prefix=".signbank.",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.to_dict(), f, indent=1)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
            os.chmod(path, 0o600)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.path = path
        return path

    @classmethod
    def load(cls, path=None):
        """Load the bank; a missing file is an empty bank, a corrupt
        file raises (honest failure beats a silently empty lexicon)."""
        path = path or DEFAULT_PATH
        b = cls()
        b.path = path
        if not os.path.exists(path):
            return b
        with open(path) as f:
            loaded = cls.from_dict(json.load(f))
        loaded.path = path
        return loaded
