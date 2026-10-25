#!/usr/bin/env python3
"""signbank2 - calibrated, open-set, per-user sign bank (engine v2) for the merged product.

Drop-in for server/signbank.py (same SignBank API the daemon uses: add_exemplar / match / list / set_action /
remove / save / load / reject_threshold) with a different recognizer inside. Why it exists: measured on REAL
keypoints, the v1 engine accepts 82 % of never-taught signs and types the wrong sign 11.6 % of the time at its
shipped threshold (tools/text/py_engine, proofs/kiro-night/text/py-engine). v2 ports the design that holds up on
real signers in the Rust spotter (crates/alive-teach), minus everything that needs the body:

  * per-frame features at 15 Hz: palm position (relative to the sign's own mean position, in hand-size units),
    velocity, and a 14-d hand-shape descriptor in which hand ORIENTATION carries the weight and finger extension /
    finger gaps are down-weighted (they are the noisy part on real hands);
  * the sign is cut out of the take (still frames the signer holds before / after the movement are trimmed);
  * banded symmetric DTW over those feature frames (the same cost for teach-time calibration and live matching);
  * a threshold PER SIGN, learned from the user's own takes: leave-one-out distances plus drift-perturbed probes
    (a take as it would look on another day), so a sign taught once stays recognisable;
  * open-set gates: distance under the sign's own threshold, margin against the nearest other sign and against
    the user's recorded everyday movement (the none class), enough hand in view, enough movement (a held pose
    is not a sign);
  * teach-time conflict check (`check`): "GOVINDA looks like NAMASTE - re-record" before the sign is saved.

Input is the kp42 wire frame of v1: {"t_ms", "points":[{x,y,score} x42]} (slot 0..20 = left hand, 21..41 = right,
absent hand = zeros), x/y NORMALIZED to [0,1] by image width / height. Optional "aspect" (width / height) per
frame or session makes x and y isotropic; without it 16:9 is assumed. Needs numpy; the daemon falls back to
signbank.py when it is missing.

Reported numbers: proofs/kiro-night/text/py-engine/*.md. Every constant below is either copied from the Rust
spotter (same name) or marked TUNED with the dev-signer evidence in the log.
"""
import json
import math
import os
import random
import tempfile

import numpy as np

GRID_HZ = 15.0
GRID_MS = 1000.0 / GRID_HZ
CONF = 0.3                      # keypoint score that counts as seen
HAND_KP = 21
SHAPE = 14
HAND = 6 + SHAPE                # per hand: pos_ok, x, y, vx, vy, shape_ok, shape[SHAPE]
DIM = 2 * HAND
O_POS_OK, O_X, O_Y, O_VX, O_VY, O_SHAPE_OK, O_SHAPE = 0, 1, 2, 3, 4, 5, 6
VEL_SCALE = 0.12
W_POS, W_VEL, W_SHAPE = 0.7, 0.8, 0.6
P_HAND, P_SHAPE = 0.9, 0.12
SHAPE_GAINS = (0.1, 0.3, 1.0)   # finger extension, finger gaps, orientation
BAND = 0.3
TAU_A, TAU_B, TAU_MIN, TAU_MAX, TAU_ONE = 1.5, 0.04, 0.10, 0.85, 0.30
TAU_SCALE = 0.70
MARGIN, MARGIN_NONE = 1.5, 1.5
MIN_LEN, MAX_TPL = 5, 45
TRIM_STILL = 0.10
MIN_HAND_FRAC = 0.6
MIN_MOTION = 0.5
PROBES, PROBE_Q, PROBE_HEAD = 4, 0.9, 1.1
SEP_CONFLICT, SEP_SIMILAR = 1.6, 2.4
MAX_EXEMPLARS = 5
MAX_NONE = 40
UNIT_PALMS = 3.5                # one position unit = this many palm lengths (~ a shoulder width)
W_LOC = 0.0                     # weight of the sign's mean on-screen position (0 = position-invariant)
QS1 = 0.0                       # extra still-trim levels tried on the QUERY (best alignment wins); 0 = off
QS2 = 0.0
DEFAULT_ASPECT = 16.0 / 9.0
N_BODY = 11                     # COCO upper-body joints that ride along on the laptop-camera wire ("body")
SH_L, SH_R = 5, 6               # shoulders in that block
BODY_WRIST = (9, 10)            # body wrists (left, right), used when a hand block has no wrist
REST_Y = 1.75                   # a wrist this many shoulder widths below the shoulder line is at rest
BIG = 1.0e6
DEFAULT_PATH = os.path.expanduser("~/.config/wf/signbank.json")
FORMAT = "wf-signbank/2"

_ANCHORS = (0, 5, 9, 13, 17)
_NEED = (0, 4, 5, 8, 9, 12, 16, 17, 20)
_TIPS = (4, 8, 12, 16, 20)
_GAPS = ((4, 8), (8, 12), (12, 16), (16, 20))


# ---------------------------------------------------------------------------------------------------------------
# wire frames -> 15 Hz grid rows
# ---------------------------------------------------------------------------------------------------------------

def _parse(frames, aspect):
    """frames -> (P[n,53,3] x/y/score as a kp53 row: body 0..10, left hand 11..31, right hand 32..52, T[n] ms,
    aspect, has_body). `body` (11 x [x,y,score]) rides along on the laptop-camera wire; the phone sends none."""
    n = len(frames)
    P = np.zeros((n, N_BODY + 2 * HAND_KP, 3))
    T = np.zeros(n)
    asp = aspect
    has_body = 0
    for i, fr in enumerate(frames):
        T[i] = float(fr.get("t_ms") or 0)
        a = fr.get("aspect")
        if not a and fr.get("w") and fr.get("h"):
            try:
                a = float(fr["w"]) / float(fr["h"])
            except (TypeError, ValueError, ZeroDivisionError):
                a = None
        if a:
            try:
                asp = float(a)
            except (TypeError, ValueError):
                pass
        body = fr.get("body")
        if body and len(body) >= N_BODY:
            ok_any = 0
            for k in range(N_BODY):
                q = body[k]
                if isinstance(q, dict):
                    P[i, k] = (q.get("x", 0.0), q.get("y", 0.0), q.get("score", 1.0))
                else:
                    P[i, k] = (q[0], q[1], q[2] if len(q) > 2 else 1.0)
                ok_any += P[i, k, 2] >= CONF
            has_body += 1 if ok_any >= 2 else 0
        raw = fr.get("points") or []
        if len(raw) == HAND_KP:
            slot0 = 0 if str(fr.get("hand", "right")).lower() == "left" else 1
            groups = [(slot0, raw)]
        elif len(raw) >= 2 * HAND_KP:
            groups = [(0, raw[:HAND_KP]), (1, raw[HAND_KP:2 * HAND_KP])]
        else:
            continue
        for slot, pts in groups:
            base = N_BODY + slot * HAND_KP
            for k, p in enumerate(pts):
                if isinstance(p, dict):
                    sc = p.get("score", 1.0)
                    P[i, base + k] = (p.get("x", 0.0), p.get("y", 0.0), 1.0 if sc is None else sc)
                else:
                    P[i, base + k] = (p[0], p[1], p[2] if len(p) > 2 else 1.0)
    if n >= 2 and T[-1] <= T[0]:
        T = np.arange(n) * GRID_MS          # no usable timestamps: assume the grid rate
    asp = asp if asp and asp > 0.2 else DEFAULT_ASPECT
    if n and float(P[:, N_BODY:, :2].max()) > 2.0:
        asp = 1.0                           # pixel coordinates (isotropic), not normalized [0,1]
    return P, T, asp, bool(n) and has_body >= 0.6 * n


def _to_grid(P, T):
    """Resample irregular camera frames onto the 15 Hz grid the templates live on (interpolate joints seen in both
    neighbours, else take the nearer one; a gap of more than 400 ms yields empty rows)."""
    n = len(T)
    if n < 2:
        return P
    K = int((T[-1] - T[0]) / GRID_MS) + 1
    ticks = T[0] + np.arange(K) * GRID_MS
    idx = np.clip(np.searchsorted(T, ticks, side="right") - 1, 0, n - 1)
    j = np.minimum(idx + 1, n - 1)
    dt = T[j] - T[idx]
    w = np.where(dt > 0, (ticks - T[idx]) / np.where(dt > 0, dt, 1.0), 0.0).clip(0.0, 1.0)
    A, B = P[idx], P[j]
    okA, okB = A[..., 2] >= CONF, B[..., 2] >= CONF
    w3 = w[:, None, None]
    lerp = A + (B - A) * w3
    lerp[..., 2] = np.minimum(A[..., 2], B[..., 2])
    near = np.where(w3 < 0.5, A, B)
    out = np.where((okA & okB)[..., None], lerp, near)
    gap = dt > 400.0
    if gap.any():
        out[gap & ((T[j] - ticks) >= GRID_MS)] = 0.0
    return out


# ---------------------------------------------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------------------------------------------

def _shape_desc(xy, ok, gains):
    """xy[K,21,2] (aspect-corrected), ok[K,21] -> (desc[K,SHAPE], valid[K]) - Rust shape_desc, vectorised."""
    ge, gg, go = gains
    K = xy.shape[0]
    valid = ok[:, list(_NEED)].all(axis=1) & (ok.sum(axis=1) >= 15)

    def d(a, b):
        return np.hypot(xy[:, a, 0] - xy[:, b, 0], xy[:, a, 1] - xy[:, b, 1])

    s = d(0, 9)
    valid &= s > 1e-3
    ss = np.where(s > 1e-3, s, 1.0)
    f = np.zeros((K, SHAPE))
    for i, tip in enumerate(_TIPS):
        f[:, i] = ge * (d(tip, 0) / ss - 1.5) / 0.5
    for i, (a, b) in enumerate(_GAPS):
        f[:, 5 + i] = gg * (d(a, b) / ss - 0.6) / 0.4
    ux = (xy[:, 9, 0] - xy[:, 0, 0]) / ss
    uy = (xy[:, 9, 1] - xy[:, 0, 1]) / ss
    f[:, 9] = go * ux
    f[:, 10] = go * uy
    vl = np.maximum(d(5, 17), 1e-4)
    f[:, 11] = go * (xy[:, 17, 0] - xy[:, 5, 0]) / vl
    f[:, 12] = go * (xy[:, 17, 1] - xy[:, 5, 1]) / vl
    il = np.maximum(d(5, 8), 1e-4)
    f[:, 13] = go * (((xy[:, 8, 0] - xy[:, 5, 0]) / il) * ux + ((xy[:, 8, 1] - xy[:, 5, 1]) / il) * uy)
    f[~valid] = 0.0
    return f, valid


def featurize(G, aspect, gains=None, body=False):
    """Grid rows [K,53,3] -> (feat[K,DIM], seen[K], active[K]) or None.
    hands-only (phone camera): positions are the palm centroid in hand-size units (the caller makes them relative to
    the sign's own mean); active == seen.
    body (laptop camera): positions are the wrist relative to the shoulders (centre and width, smoothed), exactly the
    Rust features; active == a hand above the rest line."""
    gains = SHAPE_GAINS if gains is None else gains
    K = G.shape[0]
    hands = np.stack([G[:, N_BODY:N_BODY + HAND_KP], G[:, N_BODY + HAND_KP:N_BODY + 2 * HAND_KP]], axis=1)
    xy = hands[..., :2].copy()
    xy[..., 0] *= aspect
    ok = hands[..., 2] >= CONF
    feat = np.zeros((K, DIM))
    if body:
        bxy = G[:, :N_BODY, :2].copy()
        bxy[..., 0] *= aspect
        bok = G[:, :N_BODY, 2] >= CONF
        center, width = None, 0.45
        posu = np.zeros((K, 2, 2))
        pos_ok = np.zeros((K, 2), dtype=bool)
        for k in range(K):
            if bok[k, SH_L] and bok[k, SH_R]:
                l, r = bxy[k, SH_L], bxy[k, SH_R]
                c = (l + r) * 0.5
                w = math.hypot(l[0] - r[0], l[1] - r[1])
                if w > 0.02:
                    if center is None:
                        center, width = c, w
                    else:
                        center = center + 0.3 * (c - center)
                        width += 0.3 * (w - width)
            cc = center if center is not None else np.array([0.5 * aspect, 0.55])
            ww = max(width, 0.02)
            for h in range(2):
                if ok[k, h, 0]:
                    wr = xy[k, h, 0]
                elif bok[k, BODY_WRIST[h]]:
                    wr = bxy[k, BODY_WRIST[h]]
                else:
                    continue
                posu[k, h] = (wr - cc) / ww
                pos_ok[k, h] = True
    else:
        anch = list(_ANCHORS)
        aok = ok[:, :, anch]
        cnt = aok.sum(axis=2)
        pos_ok = cnt >= 3
        pos = (xy[:, :, anch, :] * aok[..., None]).sum(axis=2) / np.maximum(cnt, 1)[..., None]
        palm = np.hypot(xy[:, :, 9, 0] - xy[:, :, 0, 0], xy[:, :, 9, 1] - xy[:, :, 0, 1])
        pv = palm[ok[:, :, 0] & ok[:, :, 9]]
        if pv.size == 0 or not pos_ok.any():
            return None
        u = max(float(np.median(pv)), 1e-4) * UNIT_PALMS
        posu = pos / u
    if not pos_ok.any():
        return None
    prev = [[None, None], [None, None]]
    for h in range(2):
        o = h * HAND
        for k in range(K):
            p = posu[k, h] if pos_ok[k, h] else None
            if p is not None:
                feat[k, o + O_POS_OK] = 1.0
                feat[k, o + O_X], feat[k, o + O_Y] = p
                p1, p2 = prev[h]
                if p2 is not None and p1 is not None:
                    v = ((p[0] - p2[0]) * 7.5, (p[1] - p2[1]) * 7.5)
                elif p1 is not None:
                    v = ((p[0] - p1[0]) * 15.0, (p[1] - p1[1]) * 15.0)
                else:
                    v = (0.0, 0.0)
                feat[k, o + O_VX] = max(-3.0, min(3.0, v[0] * VEL_SCALE))
                feat[k, o + O_VY] = max(-3.0, min(3.0, v[1] * VEL_SCALE))
            prev[h] = [p, prev[h][0]]
        sd, sv = _shape_desc(xy[:, h], ok[:, h], gains)
        feat[:, o + O_SHAPE_OK] = sv
        feat[:, o + O_SHAPE:o + O_SHAPE + SHAPE] = sd
    seen = pos_ok.any(axis=1)
    if body:
        active = np.zeros(K, dtype=bool)
        for h in range(2):
            active |= pos_ok[:, h] & (posu[:, h, 1] < REST_Y)
    else:
        active = seen
    return feat, seen, active


def _speed(feat):
    """Per-frame hand speed (largest of the two hands) - the Rust hand_speed."""
    sp = np.zeros(feat.shape[0])
    for h in range(2):
        o = h * HAND
        s = np.hypot(feat[:, o + O_VX], feat[:, o + O_VY]) * (feat[:, o + O_POS_OK] > 0.5)
        sp = np.maximum(sp, s)
    return sp


def cut(feat, seen, still=None, active=None):
    """Find the sign inside a take: the longest run of frames with a hand in view (gaps of up to 3 frames
    bridged), still-frame trim at both ends, padded by one seen frame. -> (start, end, issue)."""
    still = TRIM_STILL if still is None else still
    active = seen if active is None else active
    n = feat.shape[0]
    runs, i = [], 0
    while i < n:
        if not active[i]:
            i += 1
            continue
        s = e = i
        gap, j = 0, i + 1
        while j < n:
            if active[j]:
                e, gap = j, 0
            else:
                gap += 1
                if gap > 3:
                    break
            j += 1
        runs.append((s, e))
        i = e + 1
    if not runs:
        return 0, 0, "no hands in view - sign inside the camera box"
    s, e = max(runs, key=lambda r: r[1] - r[0])
    if still > 0 and e > s + MIN_LEN:
        sp = _speed(feat)
        peak = float(sp[s:e + 1].max())
        if peak > 0:
            thr = still * peak
            while s + MIN_LEN < e and sp[s + 1] < thr:
                s += 1
            while e > s + MIN_LEN and sp[e] < thr:
                e -= 1
    if s > 0 and seen[s - 1]:
        s -= 1
    if e + 1 < n and seen[e + 1]:
        e += 1
    ln = e - s + 1
    frac = float(seen[s:e + 1].mean())
    if ln < MIN_LEN:
        return s, e, "too short - sign a little slower"
    if ln > MAX_TPL:
        return s, e, "too long - sign it once, then lower your hands"
    if frac < 0.6:
        return s, e, "hands left the camera view"
    return s, e, None


def _finish(feat, seen, s, e, body):
    f = feat[s:e + 1].copy()
    if body:
        ref = np.zeros(2)                  # positions are already shoulder-relative
    else:
        # position relative to the sign's own mean position (all hand-frames), so where it is signed stops mattering
        pts = [(f[k, h * HAND + O_X], f[k, h * HAND + O_Y]) for k in range(len(f)) for h in range(2)
               if f[k, h * HAND + O_POS_OK] > 0.5]
        ref = np.mean(np.array(pts), axis=0) if pts else np.zeros(2)
        for h in range(2):
            o = h * HAND
            m = f[:, o + O_POS_OK] > 0.5
            f[m, o + O_X] -= ref[0]
            f[m, o + O_Y] -= ref[1]
    return {"feat": f, "ref": ref, "motion": float(_speed(f).sum()), "hand_frac": float(seen[s:e + 1].mean()),
            "body": bool(body)}


def sign_features(frames, aspect=DEFAULT_ASPECT, gains=None, long_ok=False, stills=None, mode=None):
    """frames -> dict(feat[n,DIM], ref[2], motion, hand_frac, body) of the CUT sign, or {"issue": text}.
    mode: None = decide from the frames (body joints present -> "body"), or force "hands" / "body".
    long_ok=True lets a query run past MAX_TPL (the endpointer caps segments at 3 s anyway).
    stills: extra still-trim levels; the result then also carries "variants" (the same sign cut tighter)."""
    gains = SHAPE_GAINS if gains is None else gains
    P, T, asp, has_body = _parse(frames, aspect)
    if len(frames) < 2:
        return {"issue": "too short - sign a little slower"}
    body = has_body if mode is None else (mode == "body")
    if body and not has_body:
        return {"issue": "no body reference in this recording (this bank was taught with the laptop camera)"}
    G = _to_grid(P, T)
    r = featurize(G, asp, gains, body)
    if r is None:
        return {"issue": "no hands in view - sign inside the camera box"}
    feat, seen, active = r
    s, e, issue = cut(feat, seen, active=active)
    if issue and not (long_ok and issue.startswith("too long")):
        return {"issue": issue}
    out = _finish(feat, seen, s, e, body)
    if stills:
        seen_se = {(s, e)}
        out["variants"] = [out]
        for v in stills:
            s2, e2, iss2 = cut(feat, seen, still=v, active=active)
            if (s2, e2) in seen_se or (iss2 and not iss2.startswith("too long")):
                continue
            seen_se.add((s2, e2))
            out["variants"].append(_finish(feat, seen, s2, e2, body))
    return out


# ---------------------------------------------------------------------------------------------------------------
# vectorised banded DTW (one query against a stack of templates)
# ---------------------------------------------------------------------------------------------------------------

def _stack(templates):
    M = max(len(t) for t in templates)
    S = np.zeros((len(templates), M, DIM))
    L = np.zeros(len(templates), dtype=int)
    for i, t in enumerate(templates):
        S[i, :len(t)] = t
        L[i] = len(t)
    return S, L


def _frame_costs(q, S, L, dloc2=None, w_loc=0.0):
    """Pair costs [T,n,M] between query frames q[n,DIM] and every template frame, BIG outside each template's
    length and band. The cost is the Rust fdist (weighted Euclid with fixed penalties for a missing hand/shape)."""
    T, M, _ = S.shape
    n = q.shape[0]
    acc = np.zeros((T, n, M))
    for h in range(2):
        o = h * HAND
        qp = q[:, o + O_POS_OK] > 0.5
        tp = S[:, :, o + O_POS_OK] > 0.5
        both = qp[None, :, None] & tp[:, None, :]
        one = qp[None, :, None] ^ tp[:, None, :]
        dx = q[None, :, None, o + O_X] - S[:, None, :, o + O_X]
        dy = q[None, :, None, o + O_Y] - S[:, None, :, o + O_Y]
        dvx = q[None, :, None, o + O_VX] - S[:, None, :, o + O_VX]
        dvy = q[None, :, None, o + O_VY] - S[:, None, :, o + O_VY]
        c = W_POS * (dx * dx + dy * dy) + W_VEL * (dvx * dvx + dvy * dvy)
        qs = q[:, o + O_SHAPE_OK] > 0.5
        ts = S[:, :, o + O_SHAPE_OK] > 0.5
        qa = q[:, o + O_SHAPE:o + O_SHAPE + SHAPE]
        ta = S[:, :, o + O_SHAPE:o + O_SHAPE + SHAPE]
        sq = (qa * qa).sum(axis=1)[None, :, None] + (ta * ta).sum(axis=2)[:, None, :] \
            - 2.0 * np.einsum("nk,tmk->tnm", qa, ta)
        sboth = qs[None, :, None] & ts[:, None, :]
        sone = qs[None, :, None] ^ ts[:, None, :]
        c = c + np.where(sboth, W_SHAPE * np.maximum(sq, 0.0) / SHAPE, np.where(sone, P_SHAPE, 0.0))
        acc += np.where(both, c, np.where(one, P_HAND, 0.0))
    if w_loc and dloc2 is not None:
        acc += (w_loc * dloc2)[:, None, None]
    cost = np.sqrt(acc)
    # band mask: |j - round(i*(m-1)/(n-1))| <= b, b = max(ceil(BAND*max(n,m)), 2); outside / padding -> BIG
    ii = np.arange(n)[None, :, None]
    jj = np.arange(M)[None, None, :]
    m_ = L[:, None, None]
    jc = np.rint(ii * (m_ - 1) / max(n - 1, 1))
    b = np.maximum(np.ceil(BAND * np.maximum(n, L)), 2)[:, None, None]  # BAND read at call time
    allowed = (np.abs(jj - jc) <= b) & (jj < m_)
    return np.where(allowed, cost, BIG)


def dtw_many(q, templates, dloc2=None, w_loc=0.0):
    """Symmetric DTW (diagonal 2, steps 1) distance of query q[n,DIM] to each template, normalised by n+m so it is
    the path-weighted mean frame cost. One numpy pass over all templates (min-plus prefix scan per row)."""
    if not templates:
        return np.zeros(0)
    S, L = _stack(templates)
    C = _frame_costs(q, S, L, dloc2, w_loc)
    T, n, M = C.shape
    prev = None
    for i in range(n):
        c = C[:, i, :]
        cs = np.cumsum(c, axis=1)
        a = np.full((T, M), 2.0 * BIG)
        if i == 0:
            a[:, 0] = 2.0 * c[:, 0]
        else:
            a[:, 0] = prev[:, 0] + c[:, 0]
            a[:, 1:] = np.minimum(prev[:, :-1] + 2.0 * c[:, 1:], prev[:, 1:] + c[:, 1:])
        prev = cs + np.minimum.accumulate(a - cs, axis=1)
    d = prev[np.arange(T), L - 1] / (n + L)
    return np.where(d < BIG / 2, d, np.inf)


# ---------------------------------------------------------------------------------------------------------------
# calibration (teach time)
# ---------------------------------------------------------------------------------------------------------------

def drift_probe(t, rng):
    """A take as it might look on another day: a little faster or slower, shifted, bigger or smaller, hands a little
    more or less closed (Rust drift_probe)."""
    n = len(t)
    if n < 2:
        return t.copy()
    m = max(4, int(round(n * rng.uniform(0.87, 1.15))))
    out = np.empty((m, DIM))
    for k in range(m):
        x = k * (n - 1) / max(m - 1, 1)
        i = min(int(math.floor(x)), n - 1)
        j = min(i + 1, n - 1)
        w = x - i
        f = (t[i] if w < 0.5 else t[j]).copy()
        for h in range(2):
            o = h * HAND
            if t[i, o + O_POS_OK] > 0.5 and t[j, o + O_POS_OK] > 0.5:
                for c in (O_X, O_Y, O_VX, O_VY):
                    f[o + c] = t[i, o + c] * (1 - w) + t[j, o + c] * w
        out[k] = f
    speed = n / m
    for h in range(2):
        o = h * HAND
        m_ok = out[:, o + O_POS_OK] > 0.5
        if not m_ok.any():
            continue
        mx, my = out[m_ok, o + O_X].mean(), out[m_ok, o + O_Y].mean()
        dx, dy = rng.gauss(0, 1) * 0.08, rng.gauss(0, 1) * 0.08
        a = 1.0 + rng.gauss(0, 1) * 0.07
        out[m_ok, o + O_X] = mx + (out[m_ok, o + O_X] - mx) * a + dx
        out[m_ok, o + O_Y] = my + (out[m_ok, o + O_Y] - my) * a + dy
        out[m_ok, o + O_VX] *= a * speed
        out[m_ok, o + O_VY] *= a * speed
        s_ok = out[:, o + O_SHAPE_OK] > 0.5
        if s_ok.any():
            out[s_ok, o + O_SHAPE:o + O_SHAPE + SHAPE] += np.array(
                [[rng.gauss(0, 1) * 0.08 for _ in range(SHAPE)] for _ in range(int(s_ok.sum()))])
    return out


def calibrate(takes):
    """takes: list of feature arrays of ONE sign -> dict(tau, spread, consistency, loo)."""
    k = len(takes)
    if k < 2:
        return {"tau": TAU_ONE, "spread": TAU_ONE / TAU_A, "consistency": 0.0, "loo": []}
    loo = []
    for i in range(k):
        others = [takes[j] for j in range(k) if j != i]
        loo.append(float(dtw_many(takes[i], others).min()))
    mx = max(loo)
    spread = sum(loo) / k
    tau = TAU_A * mx + TAU_B
    if k >= 3:
        seed = hash((k, round(float(takes[0][:, O_X].sum()), 4), round(float(takes[-1][:, HAND + O_X].sum()), 4))) & 0xFFFFFFFF
        rng = random.Random(seed)
        probe = []
        for i in range(k):
            others = [takes[j] for j in range(k) if j != i]
            for _ in range(PROBES):
                d = float(dtw_many(drift_probe(takes[i], rng), others).min())
                if math.isfinite(d):
                    probe.append(d)
        if probe:
            probe.sort()
            q = probe[int(round((len(probe) - 1) * PROBE_Q))]
            tau = max(tau, q * PROBE_HEAD)
    tau = min(max(tau, TAU_MIN), TAU_MAX)
    return {"tau": tau, "spread": spread, "consistency": min(max(1.0 - spread / tau, 0.0), 1.0), "loo": loo}


def none_windows(feat, seen):
    """Windows of everyday movement a 'fidget for 8 seconds' recording yields (hand in view at least half the time)."""
    out = []
    for w in (15, 24):
        s = 0
        while s + w <= len(feat):
            if int(seen[s:s + w].sum()) * 2 >= w:
                out.append(feat[s:s + w].copy())
            s += 8
    return out


# ---------------------------------------------------------------------------------------------------------------
# the bank
# ---------------------------------------------------------------------------------------------------------------

class MatchResult:
    """What match() returns. gloss / dist / accepted / second_gloss / margin / score / label / action / tier / reason
    are the v1 contract. `dist` is the distance as a multiple of the sign's OWN live threshold (accept needs
    dist <= 1.0, so reject_threshold == 1.0); `margin` is how many times farther the nearest rival is than the
    winner (accept needs >= 1.5); `raw` is the raw DTW distance."""
    __slots__ = ("gloss", "dist", "accepted", "second_gloss", "margin", "score", "label", "action", "tier",
                 "reason", "raw", "rival_none")

    def __init__(self, gloss=None, dist=float("inf"), accepted=False, second_gloss=None, margin=float("inf"),
                 score=0.0, label="", action=None, tier=0, reason="", raw=float("inf"), rival_none=False):
        self.gloss, self.dist, self.accepted, self.second_gloss = gloss, dist, accepted, second_gloss
        self.margin, self.score, self.label, self.action, self.tier = margin, score, label, action, tier
        self.reason, self.raw, self.rival_none = reason, raw, rival_none

    def __repr__(self):
        return (f"MatchResult(gloss={self.gloss!r}, dist={self.dist:.3f}, accepted={self.accepted}, "
                f"second={self.second_gloss!r}, margin={self.margin:.2f}, reason={self.reason!r})")


class SignBank:
    """Per-user sign bank, engine v2. API-compatible with signbank.SignBank."""

    def __init__(self, tau_scale=TAU_SCALE, margin=MARGIN, margin_none=MARGIN_NONE, min_motion=MIN_MOTION,
                 min_hand_frac=MIN_HAND_FRAC, gains=None, w_loc=None):
        self.tau_scale, self.margin, self.margin_none = float(tau_scale), float(margin), float(margin_none)
        self.min_motion, self.min_hand_frac = float(min_motion), float(min_hand_frac)
        self.gains = tuple(SHAPE_GAINS if gains is None else gains)
        self.w_loc = float(W_LOC if w_loc is None else w_loc)
        self.aspect = DEFAULT_ASPECT
        self.mode = None        # "hands" (phone camera) or "body" (laptop camera); fixed by the first sign taught
        self.templates = []     # [{gloss,label,tier,action,user_samples,samples:[{feat,ref,motion}],tau,spread,consistency}]
        self.none = []          # [feat arrays]
        self.path = DEFAULT_PATH
        self._stack = None

    # accept is "dist <= 1.0" in units of each sign's own threshold
    reject_threshold = 1.0

    # -- bank ops ----------------------------------------------------------------------------------------------

    def _find(self, gloss):
        for t in self.templates:
            if t["gloss"] == gloss:
                return t
        for t in self.templates:
            if t["gloss"].lower() == gloss.lower():
                return t
        return None

    def _feats(self, frames, long_ok=False):
        stills = [v for v in (QS1, QS2) if v > 0] if long_ok else None
        return sign_features(frames, self.aspect, self.gains, long_ok, stills, self.mode)

    def add_exemplar(self, gloss, frames, label="", action=None):
        """Teach one performance. False when the take is unusable (see last_issue)."""
        gloss = str(gloss).strip()
        self.last_issue = None
        if not gloss:
            return False
        sf = self._feats(frames)
        if "issue" in sf:
            self.last_issue = sf["issue"]
            return False
        if self.mode is None:
            self.mode = "body" if sf.get("body") else "hands"
        t = self._find(gloss)
        if t is None:
            t = {"gloss": gloss, "label": label or "", "tier": 0, "action": None, "user_samples": 0, "samples": [],
                 "tau": TAU_ONE, "spread": TAU_ONE / TAU_A, "consistency": 0.0}
            self.templates.append(t)
        t["samples"].append({"feat": sf["feat"], "ref": sf["ref"], "motion": sf["motion"]})
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
        t.update(calibrate([s["feat"] for s in t["samples"]]))
        self._stack = None
        return True

    def add_none(self, frames):
        """A recording of the user's everyday movement (scratching, waving, reaching): the none class that a sign
        must beat by the margin. Returns the number of windows kept."""
        P, T, asp, has_body = _parse(frames, self.aspect)
        if len(frames) < 2:
            return 0
        body = has_body if self.mode is None else (self.mode == "body")
        if body and not has_body:
            return 0
        r = featurize(_to_grid(P, T), asp, self.gains, body)
        if r is None:
            return 0
        feat, seen, _active = r
        if not body:
            # position relative to this recording's mean, like a sign
            pts = [(feat[k, h * HAND + O_X], feat[k, h * HAND + O_Y]) for k in range(len(feat)) for h in range(2)
                   if feat[k, h * HAND + O_POS_OK] > 0.5]
            if not pts:
                return 0
            ref = np.mean(np.array(pts), axis=0)
            for h in range(2):
                o = h * HAND
                m = feat[:, o + O_POS_OK] > 0.5
                feat[m, o + O_X] -= ref[0]
                feat[m, o + O_Y] -= ref[1]
        if self.mode is None:
            self.mode = "body" if body else "hands"
        w = none_windows(feat, seen)
        self.none.extend(w)
        if len(self.none) > MAX_NONE:
            self.none = self.none[-MAX_NONE:]
        self._stack = None
        return len(w)

    def clear_none(self):
        self.none = []
        self._stack = None

    def set_action(self, gloss, label=None, action=None, tier=None):
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
        if not self.templates:
            self.mode = None
        self._stack = None
        return True

    def list(self):
        return [{"gloss": t["gloss"], "label": t.get("label", ""), "n_exemplars": len(t["samples"]),
                 "action": t.get("action"), "tier": t.get("tier", 0),
                 "tau": round(t.get("tau", 0.0), 4), "consistency": round(t.get("consistency", 0.0), 3)}
                for t in self.templates]

    def check(self, gloss):
        """Teach-time conflict check: the closest OTHER sign (or the none class) and how far it is relative to this
        sign's own spread. level: ok | similar | conflict. -> dict or None."""
        t = self._find(gloss)
        if t is None:
            return None
        mine = [s["feat"] for s in t["samples"]]
        best = None
        others = [(o["gloss"], [s["feat"] for s in o["samples"]], False) for o in self.templates if o is not t]
        if self.none:
            others.append(("everyday movement", self.none, True))
        for name, tk, is_none in others:
            d = min(float(dtw_many(a, tk).min()) for a in mine)
            if not math.isfinite(d):
                continue
            sep = d / max(t.get("spread", 0.02), 0.02)
            if best is None or sep < best["separation"]:
                level = "conflict" if sep < SEP_CONFLICT else "similar" if sep < SEP_SIMILAR else "ok"
                best = {"other": name, "distance": d, "separation": sep, "level": level, "none": is_none}
        return best

    # -- matching ----------------------------------------------------------------------------------------------

    def _all(self):
        """Stacked templates of every class, cached: (feats, class index per template, class names, is_none)."""
        if self._stack is None:
            feats, cls, names, none, tpl_ref = [], [], [], [], []
            for t in self.templates:
                ci = len(names)
                names.append(t["gloss"])
                none.append(False)
                for s in t["samples"]:
                    feats.append(s["feat"])
                    cls.append(ci)
                    tpl_ref.append(s["ref"])
            if self.none:
                ci = len(names)
                names.append("everyday movement")
                none.append(True)
                for w in self.none:
                    feats.append(w)
                    cls.append(ci)
                    tpl_ref.append(np.zeros(2))
            self._stack = (feats, np.array(cls, dtype=int), names, none, np.array(tpl_ref) if tpl_ref else np.zeros((0, 2)))
        return self._stack

    def _score(self, frames):
        """Raw scoring without the accept gate; a dict (or None when the bank is empty / the query unusable)."""
        if not self.templates:
            return None
        sf = self._feats(frames, long_ok=True)
        if "issue" in sf:
            return {"issue": sf["issue"]}
        feats, cls, names, none, refs = self._all()
        q = sf["feat"]
        d = None
        for v in sf.get("variants") or [sf]:
            dloc2 = ((refs - v["ref"][None, :]) ** 2).sum(axis=1) if self.w_loc else None
            dv = dtw_many(v["feat"], feats, dloc2, self.w_loc)
            d = dv if d is None else np.minimum(d, dv)
        nc = len(names)
        cd = np.full(nc, np.inf)
        for ci in range(nc):
            m = cls == ci
            if m.any():
                cd[ci] = d[m].min()
        sign_idx = [i for i in range(nc) if not none[i]]
        r = {i: cd[i] / (self.templates[i]["tau"] * self.tau_scale) for i in sign_idx}
        win = min(sign_idx, key=lambda i: r[i])
        rival_sign = [i for i in sign_idx if i != win]
        rs_i = min(rival_sign, key=lambda i: cd[i]) if rival_sign else None
        none_i = next((i for i in range(nc) if none[i]), None)
        raw_win = min(sign_idx, key=lambda i: cd[i])
        dw = float(cd[win])
        rs = float(cd[rs_i]) if rs_i is not None else float("inf")
        rn = float(cd[none_i]) if none_i is not None else float("inf")
        ratio = lambda x: min(x / max(dw, 1e-6), 99.0) if math.isfinite(x) else 99.0
        # the same motion measure the sign's takes were measured with
        motion_min = min(s["motion"] for s in self.templates[win]["samples"])
        return {"gloss": names[win], "gloss_raw": names[raw_win], "raw_min": float(cd[raw_win]), "raw": dw, "r": float(r[win]), "tau": self.templates[win]["tau"],
                "second_gloss": names[rs_i] if rs_i is not None else None, "rival_d": rs,
                "m_sign": ratio(rs), "m_none": ratio(rn), "none_d": rn,
                "hand_frac": sf["hand_frac"], "motion": sf["motion"], "motion_min": motion_min,
                "r_second": float(r[rs_i]) if rs_i is not None else float("inf"), "n": len(q)}

    def decide(self, sc):
        """Apply the open-set gates to a raw score. -> (accepted, reason)."""
        if sc is None:
            return False, "empty-bank"
        if "issue" in sc:
            return False, "thin-query"
        if sc["r"] > 1.0:
            return False, "threshold"
        if sc["hand_frac"] < self.min_hand_frac:
            return False, "hands-out-of-view"
        if sc["m_sign"] < self.margin:
            return False, "too-close"
        if sc["m_none"] < self.margin_none:
            return False, "everyday-movement"
        if self.min_motion > 0 and sc["motion"] < self.min_motion * sc["motion_min"]:
            return False, "held-pose"
        return True, "ok"

    def match(self, frames, consensus=False):
        sc = self._score(frames)
        if sc is None:
            return MatchResult(reason="empty-bank")
        if "issue" in sc:
            return MatchResult(reason="thin-query")
        ok, reason = self.decide(sc)
        t = self._find(sc["gloss"])
        r = sc["r"]
        rival_is_none = sc["m_none"] < sc["m_sign"]
        return MatchResult(gloss=sc["gloss"], dist=r, accepted=ok, second_gloss=sc["second_gloss"],
                           margin=min(sc["m_sign"], sc["m_none"]), score=2.0 ** (-(r * r)),
                           label=t.get("label", "") if t else "", action=t.get("action") if t else None,
                           tier=t.get("tier", 0) if t else 0, reason=reason, raw=sc["raw"], rival_none=rival_is_none)

    # -- v1 calibration API: thresholds are learned per sign at teach time, nothing to sweep ------------------

    def calibrate_threshold(self, labeled=None, max_bad_accepts=0):
        return {"threshold": self.reject_threshold, "eval": {}, "met": True}

    @staticmethod
    def confidence(dist):
        return 2.0 ** (-(dist * dist))

    # -- persistence -------------------------------------------------------------------------------------------

    def to_dict(self):
        def a(x, nd=4):
            return np.round(np.asarray(x, dtype=float), nd).tolist()
        return {
            "format": FORMAT, "engine": "python-dtw2", "grid_hz": GRID_HZ, "dim": DIM,
            "mode": self.mode, "aspect": self.aspect, "tau_scale": self.tau_scale, "margin": self.margin,
            "margin_none": self.margin_none, "min_motion": self.min_motion, "min_hand_frac": self.min_hand_frac,
            "gains": list(self.gains), "w_loc": self.w_loc,
            "templates": [{"gloss": t["gloss"], "label": t.get("label", ""), "tier": t.get("tier", 0),
                           "action": t.get("action"), "user_samples": t.get("user_samples", 0),
                           "tau": t["tau"], "spread": t["spread"], "consistency": t["consistency"],
                           "samples": [{"feat": a(s["feat"]), "ref": a(s["ref"]), "motion": s["motion"]}
                                       for s in t["samples"]]} for t in self.templates],
            "none": [a(w) for w in self.none],
        }

    @classmethod
    def from_dict(cls, d):
        if isinstance(d, list) or not isinstance(d, dict) or "templates" not in d:
            raise ValueError("not a signbank file")
        if d.get("format") != FORMAT:
            raise ValueError("signbank file is the v1 format - teach the signs again (v2 stores 15 Hz feature frames)")
        b = cls(tau_scale=d.get("tau_scale", TAU_SCALE), margin=d.get("margin", MARGIN),
                margin_none=d.get("margin_none", MARGIN_NONE), min_motion=d.get("min_motion", MIN_MOTION),
                min_hand_frac=d.get("min_hand_frac", MIN_HAND_FRAC), gains=d.get("gains", SHAPE_GAINS),
                w_loc=d.get("w_loc", W_LOC))
        b.aspect = float(d.get("aspect", DEFAULT_ASPECT))
        b.mode = d.get("mode") if d.get("mode") in ("hands", "body") else None
        for t in d.get("templates") or []:
            gloss = str(t.get("gloss") or "")
            if not gloss:
                continue
            samples = []
            for s in t.get("samples") or []:
                f = np.array(s["feat"], dtype=float)
                if f.ndim == 2 and f.shape[1] == DIM:
                    samples.append({"feat": f, "ref": np.array(s.get("ref", [0, 0]), dtype=float),
                                    "motion": float(s.get("motion", 0.0))})
            b.templates.append({"gloss": gloss, "label": t.get("label", ""), "tier": int(t.get("tier", 0) or 0),
                                "action": t.get("action"), "user_samples": int(t.get("user_samples", 0) or 0),
                                "tau": float(t.get("tau", TAU_ONE)), "spread": float(t.get("spread", 0.1)),
                                "consistency": float(t.get("consistency", 0.0)), "samples": samples})
        b.none = [np.array(w, dtype=float) for w in d.get("none") or []]
        return b

    def save(self, path=None):
        path = path or self.path or DEFAULT_PATH
        dname = os.path.dirname(path)
        if dname:
            os.makedirs(dname, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dname or ".", prefix=".signbank.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.to_dict(), f)
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
        path = path or DEFAULT_PATH
        if not os.path.exists(path):
            b = cls()
            b.path = path
            return b
        with open(path) as f:
            b = cls.from_dict(json.load(f))
        b.path = path
        return b
