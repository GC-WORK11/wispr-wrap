#!/usr/bin/env python3
"""run_eval_sb.py — merged-product eval on the SHIPPED stack.

run_eval.py drives the contract reference (alive-temporal Endpointer +
traj-only bank on centroid paths). THIS harness drives what wfd.py
actually runs in production:

    synth kp42 frames -> {"t_ms","points":[{x,y,score} x42]} wire dicts
      -> server/endpointer.Endpointer.feed()
      -> {"type":"segment","frames":[wire ...]}  (NO flush — the product
         has none; a sign exists only when a segment closes in-stream)
      -> server/signbank.SignBank add_exemplar / match (fused traj+shape
         DTW, veto or consensus rule, serialized reject threshold)
      -> commands.tsv-style route (mock spawn — journal only)

Four arms (the PRD's four numbers):
  teach         per-user taught-sign recall at N = 5/10/20 signs x3 takes.
  false-accept  T seconds of non-sign motion -> routed fires/hour, at the
                shipped 0.6 threshold AND the bank-calibrated one.
  latency       segment-close -> match -> route-decision wall ms on THIS
                box (detect lag reported separately, in virtual ms).
  load          real wfd.py static serve: cold/warm fetch of the FULL
                shell asset list incl. the 34MB vendor payload.

Modes:
  shipped  Endpointer() defaults (enter_v=1.5, exit_v=0.7 — tuned for a
           sensor whose still EMA sits under ~0.7 palm-widths/s).
  kp       calibrated for THIS synthetic sensor: enter_v = still-EMA p99
           x1.5, exit_v = still-EMA p95 x1.2, pre_roll=8 — the same
           per-sensor calibration methodology run_eval.calibrate_floor
           used for the old contract floor.

Determinism: every clip is a pure function of --seed. Wall-clock arms
(latency, load) are real measurements on this box.

Usage:
  python3 tools/live-eval/run_eval_sb.py --arm all --seed 7
Proof JSONs land in tools/live-eval/proofs/sb_*.json.
"""

import argparse
import json
import math
import os
import platform
import random
import statistics
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))
import synth        # noqa: E402
import endpointer   # noqa: E402  (server/endpointer.py — the shipped one)
import signbank     # noqa: E402  (server/signbank.py — the shipped one)

PROOFS = os.path.join(HERE, "proofs")

# Vocabularies. VOCAB10 = the PRD's ten motion-distinct control signs
# (PUSH excluded — out-and-back depth barely projects to 2D). VOCAB20
# adds ten more motion-distinct shapes (synth.py extended vocab) for the
# N=20 sweep; deliberate near-neighbors INF/EIGHT and DIAMOND/SQUARE stay
# in to stress the wrong-sign rate.
VOCAB10 = ["SWIPE-R", "SWIPE-L", "SWIPE-DN", "SWIPE-UP", "CIRCLE-CW",
           "CIRCLE-CCW", "ZIGZAG", "SHAKE", "V-DRAW", "HOOK"]
VOCAB20 = VOCAB10 + ["INF", "EIGHT", "DIAMOND", "SQUARE", "SPIRAL",
                     "M-DRAW", "Z-DRAW", "U-DRAW", "TRIANGLE", "STAR5"]
SWEEP = {"5": VOCAB10[:5], "10": VOCAB10, "20": VOCAB20}

# commands.tsv contract (mock): gloss -> (label, argv). The spawn itself
# is a journal append — routing means "row exists and argv non-empty".
COMMANDS_TSV = """\
SWIPE-R	Next track	xdotool key Right
SWIPE-L	Prev track	xdotool key Left
SWIPE-DN	Scroll down	xdotool key Page_Down
SWIPE-UP	Scroll up	xdotool key Page_Up
CIRCLE-CW	Volume up	xdotool key XF86AudioRaiseVolume
CIRCLE-CCW	Volume down	xdotool key XF86AudioLowerVolume
ZIGZAG	Open browser	google-chrome --new-window
SHAKE	Undo	xdotool key ctrl+z
V-DRAW	Screenshot	gnome-screenshot
HOOK	Lock screen	xdotool key super+l
"""


def commands_tsv(vocab):
    """Base table + a mock row per extra gloss so routing is real."""
    tsv = COMMANDS_TSV
    known = {l.split("\t", 1)[0] for l in tsv.splitlines() if l.strip()}
    for g in vocab:
        if g not in known:
            tsv += f"{g}\t{g.title()} action\twf-mock {g}\n"
    return tsv


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = max(1, math.ceil(q / 100 * len(xs)))
    return xs[min(k - 1, len(xs) - 1)]


def mean(xs):
    return statistics.fmean(xs) if xs else None


class Commands:
    def __init__(self, tsv_text):
        self.rows = {}
        for line in tsv_text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t", 2)
            if len(parts) == 3 and parts[2].split():
                self.rows[parts[0].strip().upper()] = \
                    (parts[1].strip(), parts[2].split())
        self.journal = []

    def spawn(self, gloss):
        """Mock spawn: a routed action 'fires' — the journal is the
        whole side effect."""
        row = self.rows.get(gloss.strip().upper())
        if row is None:
            return None
        label, argv = row
        entry = {"gloss": gloss, "label": label, "argv": argv}
        self.journal.append(entry)
        return entry


# ---------------------------------------------------------------------
# Wire bridge: synth frames -> the kp42 wire dicts wfd.py POSTs.
# points[0:21] = left (synth hand_b), [21:42] = right (synth hand_a,
# the dominant signer hand). Absent hand = 21 score-0 points — the same
# encoding _norm_points emits for a dropped hand.
# ---------------------------------------------------------------------

_ABSENT = [{"x": 0.0, "y": 0.0, "score": 0.0}] * 21


def to_wire(fr):
    pts = []
    for hand in (fr["hand_b"], fr["hand_a"]):
        if hand is None:
            pts.extend(_ABSENT)
        else:
            pts.extend({"x": float(p[0]), "y": float(p[1]),
                        "score": float(p[2])} for p in hand)
    return {"t_ms": fr["t_ms"], "points": pts}


def feed_clip(frames, ep):
    """Feed one clip; bucket events exactly like wfd.live_kp does."""
    segs, modes, drops = [], [], []
    for fr in frames:
        for ev in ep.feed(to_wire(fr)):
            if ev["type"] == "segment":
                segs.append(ev)
            elif ev["type"] == "mode":
                modes.append(ev)
            else:
                drops.append(ev)
    return segs, modes, drops


def feed_stream(frames, ep, on_segment, on_mode=None):
    """Continuous feed (the false-accept arm — no clip resets)."""
    drops = 0
    for fr in frames:
        for ev in ep.feed(to_wire(fr)):
            if ev["type"] == "segment":
                on_segment(ev)
            elif ev["type"] == "mode":
                if on_mode:
                    on_mode(ev)
            else:
                drops += 1
    return drops


# ---------------------------------------------------------------------
# Sensor calibration — same methodology as run_eval.calibrate_floor:
# measure what a still hand actually produces on THIS keypoint sensor,
# set the hysteresis above it. Deterministic (seeded still stream).
# ---------------------------------------------------------------------

def calibrate_ep(seed, fps, kp_noise):
    """Still-stream EMA -> (enter_v, exit_v, stats). The shipped
    enter_v=1.5/exit_v=0.7 assume still EMA < ~0.7 palm-widths/s; this
    sensor's still EMA sits ~6 (jitter on wrist/palm/spread terms), so
    shipped defaults never close a sub-3s segment AND open on stills."""
    ep = endpointer.Endpointer()
    emas = []
    still = synth._still_frames(random.Random(seed), 0, 10000, fps,
                                noise=kp_noise)
    for i, fr in enumerate(still):
        ep.feed(to_wire(fr))
        if i >= 10:                      # EMA warmup
            emas.append(ep._ema or 0.0)
    p95, p99 = pct(emas, 95), pct(emas, 99)
    mx = max(emas)
    enter_v = max(1.5, p99 * 1.5)
    exit_v = max(0.7, p95 * 1.2)
    stats = {"still_ema_p50": round(pct(emas, 50), 3),
             "still_ema_p95": round(p95, 3),
             "still_ema_p99": round(p99, 3),
             "still_ema_max": round(mx, 3)}
    return enter_v, exit_v, stats


def modes_for(enter_v, exit_v):
    return {
        # "shipped" runs CONTINUOUSLY (the real deployment model): the
        # daemon's Endpointer has no per-clip reset or flush — a segment
        # exists only when a 3.0s window force-closes, so per-clip feeds
        # would score it vacuously at zero. See *_cont helpers.
        "shipped": {"enter_v": 1.5, "exit_v": 0.7},
        "kp": {"enter_v": enter_v, "exit_v": exit_v, "pre_roll": 8},
    }


def _offset_frames(frames, t0):
    """Retimestamp a clip so it continues a shared stream clock."""
    return [dict(fr, t_ms=fr["t_ms"] + t0) for fr in frames]


def teach_bank_cont(vocab, takes, args, epkw):
    """Shipped-mode teach under the REAL regime: one Endpointer across
    the whole session (wfd never resets it between takes). Every emitted
    segment becomes an exemplar, capped at `takes` — mirror of
    _on_segment's got counter, including the garbage blobs a
    miscalibrated segmenter emits."""
    bank = signbank.SignBank()
    per_gloss = {}
    for vi, g in enumerate(vocab):
        ep = endpointer.Endpointer(**epkw)
        clock = 0
        got, seg_n = 0, 0
        for k in range(takes):
            frames, _m = synth.perform(
                g, seed=args.seed * 1000 + vi * 100 + k,
                fps=args.fps, kp_noise=args.kp_noise)
            wframes = _offset_frames(frames, clock)
            for fr in wframes:
                for ev in ep.feed(to_wire(fr)):
                    if ev["type"] == "segment":
                        seg_n += 1
                        if got < takes and bank.add_exemplar(
                                g, ev["frames"], label=g):
                            got += 1
            clock = wframes[-1]["t_ms"] + 1
        per_gloss[g] = {"exemplars": got, "segments": seg_n}
    return bank, per_gloss


def eval_stream_clips(vocab, per_sign, seed_base, args, epkw):
    """Feed ALL eval clips through ONE endpointer on a shared clock
    (the deployed regime). Returns (segments, windows, mode_events):
    windows = [(start_ms, end_ms, gloss)] ground-truth sign spans."""
    ep = endpointer.Endpointer(**epkw)
    segments, windows, mode_events, drops = [], [], [], 0
    clock = 0
    for vi, g in enumerate(vocab):
        for k in range(per_sign):
            frames, meta = synth.perform(
                g, seed=seed_base + vi * 100 + k,
                fps=args.fps, kp_noise=args.kp_noise)
            wframes = _offset_frames(frames, clock)
            for fr in wframes:
                for ev in ep.feed(to_wire(fr)):
                    if ev["type"] == "segment":
                        segments.append(ev)
                    elif ev["type"] == "mode":
                        mode_events.append(ev)
                    else:
                        drops += 1
            windows.append((clock + meta["sign_start_ms"],
                            clock + meta["sign_end_ms"], g, meta))
            clock = wframes[-1]["t_ms"] + 1
    return segments, windows, mode_events, drops


def attribute(segments, windows):
    """Assign each segment to the EARLIEST ground-truth sign window it
    overlaps (blob segments may span several — first wins). Returns
    per-window verdicts + unattributed segments (idle-time decisions)."""
    wn = sorted(windows)
    per_window = [{"window": w, "segments": []} for w in wn]
    unattributed = []
    for s in segments:
        s0 = s["frames"][0]["t_ms"]
        s1 = s["t_ms"]
        hit = None
        for i, w in enumerate(wn):
            if s0 <= w[1] and s1 >= w[0]:
                hit = i
                break
        if hit is None:
            unattributed.append(s)
        else:
            per_window[hit]["segments"].append(s)
    return per_window, unattributed


# ---------------------------------------------------------------------
# Bank helpers — thresholds: shipped 0.6 and calibrate_threshold() on a
# labeled calibration set (positive performances + negative idle
# segments, disjoint seeds from the test set).
# ---------------------------------------------------------------------

def teach_bank(vocab, takes, args, epkw):
    """Teach `vocab` x `takes` performances; each emitted segment ->
    add_exemplar until the take count is met (mirrors _on_segment's
    teach branch: segments ARE the takes, capped at `takes`)."""
    bank = signbank.SignBank()
    per_gloss = {}
    for vi, g in enumerate(vocab):
        got, seg_n = 0, 0
        for k in range(takes):
            frames, _meta = synth.perform(
                g, seed=args.seed * 1000 + vi * 100 + k,
                fps=args.fps, kp_noise=args.kp_noise)
            segs, _m, _d = feed_clip(frames, endpointer.Endpointer(**epkw))
            seg_n += len(segs)
            for s in segs:
                if got >= takes:
                    break
                if bank.add_exemplar(g, s["frames"], label=g):
                    got += 1
        per_gloss[g] = {"exemplars": got, "segments": seg_n}
    return bank, per_gloss


def labeled_cal_set(vocab, per_sign, args, epkw, cont=False):
    """(wire_frames, expected|None) for calibrate_threshold: cal-seed
    performances + cal-seed idle segments. cont=True = the continuous
    deployment regime (segments attributed to their sign window)."""
    labeled = []
    if cont:
        segs, windows, _m, _d = eval_stream_clips(
            vocab, per_sign, args.seed * 5000, args, epkw)
        pw, unattr = attribute(segs, windows)
        for w in pw:
            if w["segments"]:
                labeled.append((w["segments"][0]["frames"],
                                w["window"][2]))
        labeled += [(f, None) for f in (s["frames"] for s in unattr)]
    else:
        for vi, g in enumerate(vocab):
            for k in range(per_sign):
                frames, _m = synth.perform(
                    g, seed=args.seed * 5000 + vi * 100 + k,
                    fps=args.fps, kp_noise=args.kp_noise)
                segs, _mm, _d = feed_clip(
                    frames, endpointer.Endpointer(**epkw))
                if segs:
                    labeled.append((segs[0]["frames"], g))
    return labeled


def idle_negatives(args, epkw, seconds):
    """Segments from a cal-seed idle stream, all expected=None."""
    neg = []
    frames = synth.idle_stream(args.seed + 31337, seconds,
                               fps=args.fps, noise=args.kp_noise)
    feed_stream(frames, endpointer.Endpointer(**epkw),
                lambda ev: neg.append(ev["frames"]))
    return [(f, None) for f in neg]


# ---------------------------------------------------------------------
# Arm A — per-user taught-sign recall (N = 5/10/20).
# ---------------------------------------------------------------------

def arm_teach(args, ep_modes, cal_seconds):
    out = {"arm": "teach_recall", "seed": args.seed, "fps": args.fps,
           "kp_noise": args.kp_noise, "takes": args.takes,
           "heldout": args.heldout, "engine": "signbank+endpointer",
           "sweep": {}}
    for n_label, vocab in SWEEP.items():
        n_out = {"vocab": vocab, "modes": {}}
        for mode, epkw in ep_modes.items():
            cont = (mode == "shipped")
            if cont:
                bank, per_gloss = teach_bank_cont(
                    vocab, args.takes, args, epkw)
            else:
                bank, per_gloss = teach_bank(vocab, args.takes, args, epkw)
            # calibrate a per-bank threshold on a labeled cal set
            # (cal-seed positives + cal-seed idle negatives).
            cal = labeled_cal_set(vocab, args.takes, args, epkw, cont=cont)
            cal += idle_negatives(args, epkw, cal_seconds)
            calib = bank.calibrate_threshold(cal, max_bad_accepts=0)
            t_cal = calib["threshold"]
            cmds = Commands(commands_tsv(vocab))
            variants = {"veto@0.6": (0.6, False),
                        "veto@cal": (t_cal, False),
                        "cons@cal": (t_cal, True)}
            res = {}
            for vname, (thr, cons) in variants.items():
                bank.reject_threshold = thr
                per_eval, n_unattr = [], 0
                if cont:
                    segs_all, windows, _m, _d = eval_stream_clips(
                        vocab, args.heldout, args.seed * 7000, args, epkw)
                    pw, unattr = attribute(segs_all, windows)
                    n_unattr = len(unattr)
                    eval_units = [(w["window"][2], w["window"][3],
                                   w["segments"]) for w in pw]
                else:
                    eval_units = []
                    for vi, g in enumerate(vocab):
                        for k in range(args.heldout):
                            frames, meta = synth.perform(
                                g, seed=args.seed * 7000 + vi * 100 + k,
                                fps=args.fps, kp_noise=args.kp_noise)
                            segs, mds, drp = feed_clip(
                                frames, endpointer.Endpointer(**epkw))
                            eval_units.append((g, meta, segs))
                for g, meta, segs in eval_units:
                    cmds.journal.clear()
                    rec = {"gloss": g, "segments": len(segs), "fired": 0}
                    if segs:
                        r = bank.match(segs[0]["frames"], consensus=cons)
                        rec.update(dist=(round(r.dist, 4)
                                         if math.isfinite(r.dist)
                                         else None),
                                   reason=r.reason,
                                   traj_gloss=r.traj_gloss,
                                   shape_gloss=r.shape_gloss,
                                   nearest=r.gloss)
                        if r.accepted and r.gloss:
                            fired = cmds.spawn(r.gloss)
                            rec.update(commit=True, hit=r.gloss,
                                       correct=(r.gloss == g),
                                       fired=1 if fired else 0)
                        else:
                            rec.update(commit=False)
                        # extra attributed segments are spurious
                        # additional decisions on the same sign window
                        for extra in segs[1:]:
                            r2 = bank.match(extra["frames"],
                                            consensus=cons)
                            if r2.accepted and r2.gloss:
                                cmds.spawn(r2.gloss)
                                rec["fired"] += 1
                                rec["extra_fires"] = \
                                    rec.get("extra_fires", 0) + 1
                    else:
                        rec["no_segment"] = True
                    per_eval.append(rec)
                # unattributed segments (cont mode: blobs that covered no
                # sign window) are spurious decisions too — match+fire them
                unattr_fires = 0
                if cont:
                    for s in unattr:
                        ru = bank.match(s["frames"], consensus=cons)
                        if ru.accepted and ru.gloss:
                            cmds.spawn(ru.gloss)
                            unattr_fires += 1
                total = len(per_eval)
                committed = [r for r in per_eval if r.get("commit")]
                correct = [r for r in committed if r.get("correct")]
                wrong = [r for r in committed if not r.get("correct")]
                confusion = {}
                for r in wrong:
                    confusion[f"{r['gloss']}->{r['hit']}"] = \
                        confusion.get(f"{r['gloss']}->{r['hit']}", 0) + 1
                ds = [r["dist"] for r in per_eval
                      if r.get("dist") is not None]
                res[vname] = {
                    "threshold": thr, "total_eval": total,
                    "answered": len(committed), "correct": len(correct),
                    "wrong": len(wrong),
                    "no_segment": sum(1 for r in per_eval
                                      if r.get("no_segment")),
                    "rejected": sum(1 for r in per_eval
                                    if not r.get("commit")
                                    and not r.get("no_segment")),
                    "extra_fires": sum(r.get("extra_fires", 0)
                                       for r in per_eval),
                    "unattributed_segments": n_unattr,
                    "unattributed_fires": unattr_fires,
                    "routed_fires": sum(r["fired"] for r in per_eval)
                    + unattr_fires,
                    "answer_rate": (len(committed) / total
                                    if total else None),
                    "precision_when_answered": (len(correct) / len(committed)
                                              if committed else None),
                    "recall_correct_over_all": (len(correct) / total
                                                if total else None),
                    "wrong_sign_rate": (len(wrong) / total
                                        if total else None),
                    "dist_p50": pct(ds, 50), "dist_p95": pct(ds, 95),
                    "confusion": confusion,
                    "per_eval": per_eval,
                }
            n_out["modes"][mode] = {
                "ep": epkw, "regime": ("continuous" if cont else "per-clip"),
                "taught": per_gloss,
                "exemplars_total": sum(p["exemplars"]
                                       for p in per_gloss.values()),
                "segments_in_teach": sum(p["segments"]
                                         for p in per_gloss.values()),
                "calibration": {"n_labeled": len(cal),
                                "threshold": t_cal,
                                "met": calib.get("met"),
                                "eval": calib.get("eval")},
                "variants": res,
            }
            bank.reject_threshold = 0.6
        out["sweep"][n_label] = n_out
    return out


# ---------------------------------------------------------------------
# Arm B — false accepts on non-sign motion (continuous stream).
# ---------------------------------------------------------------------

def arm_false_accept(args, ep_modes, cal_seconds):
    vocab = VOCAB10
    out = {"arm": "false_accept", "seed": args.seed, "fps": args.fps,
           "kp_noise": args.kp_noise, "vocab": vocab, "modes": {}}
    # the measured stream is a DIFFERENT seed from the calibration stream
    frames = synth.idle_stream(args.seed + 99991, args.idle_seconds,
                               fps=args.fps, noise=args.kp_noise)
    sim_s = frames[-1]["t_ms"] / 1000.0
    for mode, epkw in ep_modes.items():
        cont = (mode == "shipped")
        if cont:
            bank, per_gloss = teach_bank_cont(vocab, args.takes, args, epkw)
        else:
            bank, per_gloss = teach_bank(vocab, args.takes, args, epkw)
        cal = labeled_cal_set(vocab, args.takes, args, epkw, cont=cont)
        cal += idle_negatives(args, epkw, cal_seconds)
        t_cal = bank.calibrate_threshold(cal, max_bad_accepts=0)["threshold"]
        cmds = Commands(commands_tsv(vocab))
        ep = endpointer.Endpointer(**epkw)
        seg_matches, mode_events = [], []
        t0 = time.perf_counter()

        def on_seg(ev):
            r = bank.match(ev["frames"])          # shipped rule (veto)
            rc = bank.match(ev["frames"], consensus=True)
            # accept = (dist <= thr) AND channel-rule ok. We store the
            # threshold-independent part so every variant's fires are
            # derived from ONE honest match per segment.
            seg_matches.append({"t_ms": ev["t_ms"], "how": ev["how"],
                                "dur_s": round(ev["dur_s"], 2),
                                "gloss": r.gloss, "dist": r.dist,
                                "veto_ok": r.reason in ("ok", "threshold"),
                                "cons_ok": rc.reason in ("ok", "threshold")})

        drops = feed_stream(frames, ep, on_seg,
                            on_mode=lambda ev: mode_events.append(ev))
        wall = time.perf_counter() - t0
        hrs = sim_s / 3600.0
        var = {}
        for vname, key, thr in (("veto@0.6", "veto_ok", 0.6),
                                ("veto@cal", "veto_ok", t_cal),
                                ("cons@cal", "cons_ok", t_cal)):
            fired = [s for s in seg_matches
                     if s[key] and s["dist"] <= thr and s["gloss"]]
            routed = [s for s in fired if cmds.spawn(s["gloss"])]
            by_hit = {}
            for s in fired:
                by_hit[s["gloss"]] = by_hit.get(s["gloss"], 0) + 1
            var[vname] = {
                "threshold": thr,
                "accepted_fires": len(fired),
                "routed_fires": len(routed),
                "fires_per_hour": round(len(fired) / hrs, 2),
                "by_hit": by_hit,
                "fired": [{"t_ms": s["t_ms"], "hit": s["gloss"],
                           "dist": round(s["dist"], 4)}
                          for s in fired],
            }
        out["modes"][mode] = {
            "ep": epkw, "sim_seconds": round(sim_s, 1),
            "frames": len(frames),
            "exemplars_total": sum(p["exemplars"]
                                   for p in per_gloss.values()),
            "segments": len(seg_matches),
            "segment_drops": drops,
            "mode_events": len(mode_events),
            "mode_event_names": sorted({m["name"] for m in mode_events}),
            "cal_threshold": t_cal,
            "variants": var,
            "feed_wall_s": round(wall, 2),
        }
    return out


# ---------------------------------------------------------------------
# Arm C — sign-end -> match+route latency on THIS box.
# ---------------------------------------------------------------------

def arm_latency(args, ep_modes):
    vocab = VOCAB10
    out = {"arm": "latency", "seed": args.seed, "fps": args.fps,
           "kp_noise": args.kp_noise, "vocab": vocab,
           "box": box_specs(), "modes": {},
           "not_measured": [
               "keypoints->daemon LAN POST (~3-10ms per PRD)",
               "real action spawn / xdotool (~10-40ms per PRD)",
               "camera frame -> keypoints (browser MediaPipe)"]}
    cmds = Commands(commands_tsv(vocab))
    for mode, epkw in ep_modes.items():
        cont = (mode == "shipped")
        if cont:
            bank, _pg = teach_bank_cont(vocab, args.takes, args, epkw)
        else:
            bank, _pg = teach_bank(vocab, args.takes, args, epkw)
        bank.reject_threshold = 0.6
        rows = []
        if cont:
            segs_all, windows, _m, _d = eval_stream_clips(
                vocab, args.heldout, args.seed * 7000, args, epkw)
            pw, _unattr = attribute(segs_all, windows)
            for w in pw:
                g, meta = w["window"][2], w["window"][3]
                for s in w["segments"]:
                    detect = s["t_ms"] - w["window"][1]
                    t0 = time.perf_counter_ns()
                    r = bank.match(s["frames"])
                    t1 = time.perf_counter_ns()
                    fired = cmds.spawn(r.gloss) if r.accepted else None
                    t2 = time.perf_counter_ns()
                    rows.append({"gloss": g, "how": s["how"],
                                 "detect_lag_ms": detect,
                                 "match_ms": (t1 - t0) / 1e6,
                                 "route_ms": (t2 - t1) / 1e6,
                                 "decide_ms": (t2 - t0) / 1e6,
                                 "accepted": bool(r.accepted),
                                 "fired": bool(fired)})
        else:
            for vi, g in enumerate(vocab):
                for k in range(args.heldout):
                    frames, meta = synth.perform(
                        g, seed=args.seed * 7000 + vi * 100 + k,
                        fps=args.fps, kp_noise=args.kp_noise)
                    segs, _m, _d = feed_clip(
                        frames, endpointer.Endpointer(**epkw))
                    for s in segs:
                        detect = s["t_ms"] - meta["sign_end_ms"]
                        t0 = time.perf_counter_ns()
                        r = bank.match(s["frames"])
                        t1 = time.perf_counter_ns()
                        fired = cmds.spawn(r.gloss) if r.accepted else None
                        t2 = time.perf_counter_ns()
                        rows.append({"gloss": g, "how": s["how"],
                                     "detect_lag_ms": detect,
                                     "match_ms": (t1 - t0) / 1e6,
                                     "route_ms": (t2 - t1) / 1e6,
                                     "decide_ms": (t2 - t0) / 1e6,
                                     "accepted": bool(r.accepted),
                                     "fired": bool(fired)})
        det = [r["detect_lag_ms"] for r in rows]
        dec = [r["decide_ms"] for r in rows]
        mch = [r["match_ms"] for r in rows]
        e2e = [r["detect_lag_ms"] + r["decide_ms"] for r in rows]
        out["modes"][mode] = {
            "ep": epkw, "segments": len(rows),
            "accepted": sum(1 for r in rows if r["accepted"]),
            "detect_lag_ms": {"p50": pct(det, 50), "p95": pct(det, 95),
                              "mean": round(mean(det), 1) if det else None},
            "decide_ms": {"p50": (round(pct(dec, 50), 3) if dec else None),
                          "p95": (round(pct(dec, 95), 3) if dec else None),
                          "max": round(max(dec), 3) if dec else None},
            "match_only_ms": {"p50": (round(pct(mch, 50), 3) if mch else None),
                              "p95": (round(pct(mch, 95), 3) if mch else None)},
            "sign_end_to_decide_ms": {
                "p50": round(pct(e2e, 50), 1) if e2e else None,
                "p95": round(pct(e2e, 95), 1) if e2e else None},
            "per_segment": rows,
        }
    return out


def box_specs():
    cpu = "unknown"
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    return {"uname": platform.platform(), "cpu": cpu,
            "cores": os.cpu_count(), "python": sys.version.split()[0]}


# ---------------------------------------------------------------------
# Arm D — shell load through the REAL wfd.py static server.
# ---------------------------------------------------------------------

def shell_assets():
    """The asset list a phone actually pulls: every script/link/href in
    index.html + the sw.js precache lists (SHELL + VENDOR)."""
    assets = set()
    idx = os.path.join(ROOT, "web", "index.html")
    import re as _re
    try:
        html = open(idx).read()
        for m in _re.finditer(r'(?:src|href)="([^"#]+)"', html):
            u = m.group(1)
            if u.startswith(("http", "//", "data:", "mailto:")):
                continue
            assets.add("/" + u.lstrip("/"))
    except OSError:
        pass
    sw = os.path.join(ROOT, "web", "sw.js")
    try:
        txt = open(sw).read()
        for m in _re.finditer(r'"(/[^"]*)"', txt):
            assets.add(m.group(1))
    except OSError:
        pass
    assets.add("/")
    return sorted(a for a in assets if not a.endswith("/icon-180.png"))


def arm_load(args, _ep_modes):
    """Spawn the shipped daemon and fetch the whole shell the way a
    phone browser would — loopback, so this measures the SERVER path;
    what a LAN phone adds (RTT + wifi throughput) is stated in BENCH,
    not measured here."""
    import socket
    out = {"arm": "load", "engine": "real wfd.py", "box": box_specs(),
           "modes": {}}
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = dict(os.environ, WF_PORT=str(port))
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "server", "wfd.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.perf_counter() + 15
        up = False
        while time.perf_counter() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=1) as r:
                    up = r.status == 200
                    break
            except Exception:
                time.sleep(0.05)
        if not up:
            out["error"] = "wfd.py did not come up in 15s"
            return out
        out["daemon_start_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        assets = shell_assets()
        hdrs = {"Connection": "close"}

        def fetch(path):
            t = time.perf_counter()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}", headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read()
                    meta = {"status": r.status, "bytes": len(body),
                            "cache_control": r.headers.get("Cache-Control")}
            except Exception as e:
                return (time.perf_counter() - t) * 1000, \
                    {"status": None, "bytes": 0, "error": repr(e)}
            return (time.perf_counter() - t) * 1000, meta

        cold, resp_meta, sizes = {}, {}, {}
        for a in assets:
            ms, meta = fetch(a)          # first-ever fetch = cold
            cold[a] = round(ms, 2)
            resp_meta[a] = meta
            sizes[a] = meta.get("bytes", 0)
        warm = {}
        for a in assets:
            reps = args.load_reps if sizes.get(a, 0) < 1e6 else \
                max(3, args.load_reps // 5)
            xs = [fetch(a)[0] for _ in range(reps)]
            warm[a] = {"p50": round(pct(xs, 50), 2),
                       "p95": round(pct(xs, 95), 2), "reps": reps}
        # a shell load = sequential fresh fetches like first paint
        xs = []
        for _ in range(max(3, args.load_reps // 5)):
            xs.append(sum(fetch(a)[0] for a in assets))
        out["modes"]["loopback"] = {
            "n_assets": len(assets), "assets": assets,
            "total_bytes": sum(sizes.values()),
            "asset_bytes": sizes,
            "cold_fetch_ms": cold,
            "cold_total_ms": round(sum(cold.values()), 2),
            "warm_fetch_ms": warm,
            "shell_total_ms": {"p50": round(pct(xs, 50), 2),
                               "p95": round(pct(xs, 95), 2)},
            "service_worker_present": os.path.exists(
                os.path.join(ROOT, "web", "sw.js")),
            "cache_headers_present": any(
                resp_meta[a].get("cache_control") for a in assets),
            "fetch_errors": {a: resp_meta[a].get("error")
                             for a in assets
                             if resp_meta[a].get("error")
                             or resp_meta[a].get("status") != 200},
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    return out


# ---------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", default="all",
                    choices=["all", "teach", "false-accept", "latency", "load"])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--kp-noise", type=float, default=1.2,
                    help="per-keypoint jitter sigma in px (MediaPipe-scale)")
    ap.add_argument("--takes", type=int, default=3)
    ap.add_argument("--heldout", type=int, default=4)
    ap.add_argument("--idle-seconds", type=int, default=600)
    ap.add_argument("--cal-seconds", type=int, default=90,
                    help="calibration idle stream (disjoint seed)")
    ap.add_argument("--load-reps", type=int, default=15)
    ap.add_argument("--out", default=PROOFS)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    enter_v, exit_v, ema_stats = calibrate_ep(args.seed + 1, args.fps,
                                            args.kp_noise)
    ep_modes = modes_for(enter_v, exit_v)
    meta = {"tool": "tools/live-eval/run_eval_sb.py", "seed": args.seed,
            "engine": "server/endpointer+server/signbank (shipped stack)",
            "ep_calibrated": {"enter_v": round(enter_v, 2),
                              "exit_v": round(exit_v, 2)},
            "ep_stats": ema_stats,
            "python": sys.version.split()[0],
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    print(f"[live-eval-sb] seed={args.seed} "
          f"cal: enter_v={enter_v:.1f} exit_v={exit_v:.1f} "
          f"(still EMA p95 {ema_stats['still_ema_p95']}, "
          f"p99 {ema_stats['still_ema_p99']})")

    arms = (["teach", "false-accept", "latency", "load"]
            if args.arm == "all" else [args.arm])
    t_all = time.perf_counter()
    for arm in arms:
        fn = {"teach": arm_teach, "false-accept": arm_false_accept,
              "latency": arm_latency, "load": arm_load}[arm]
        if arm in ("teach", "false-accept"):
            res = fn(args, ep_modes, args.cal_seconds)
        else:
            res = fn(args, ep_modes)
        res["meta"] = meta
        path = os.path.join(args.out, f"sb_{arm.replace('-', '_')}.json")
        with open(path, "w") as f:
            json.dump(res, f, indent=1)
        print(f"[live-eval-sb] wrote {path}")
        # stdout digest
        if arm == "teach":
            for n, nd in res["sweep"].items():
                for mode, r in nd["modes"].items():
                    for v, vres in r["variants"].items():
                        print(f"  [N={n} {mode} {v}] ans {vres['answered']}/"
                              f"{vres['total_eval']} cor {vres['correct']} "
                              f"wrg {vres['wrong']} noseg {vres['no_segment']} "
                              f"ar={vres['answer_rate']:.3f} "
                              f"prec={vres['precision_when_answered']} "
                              f"wr={vres['wrong_sign_rate']:.3f}")
        elif arm == "false-accept":
            for mode, r in res["modes"].items():
                for v, vres in r["variants"].items():
                    print(f"  [{mode} {v}] segs {r['segments']} "
                          f"fires {vres['accepted_fires']} "
                          f"({vres['fires_per_hour']}/hr) "
                          f"mode_ev {r['mode_events']}")
        elif arm == "latency":
            for mode, r in res["modes"].items():
                print(f"  [{mode}] segs {r['segments']} detect p50/p95 "
                      f"{r['detect_lag_ms']['p50']}/{r['detect_lag_ms']['p95']}ms "
                      f"decide p50/p95 {r['decide_ms']['p50']}/"
                      f"{r['decide_ms']['p95']}ms")
        elif arm == "load":
            m = res["modes"]["loopback"]
            print(f"  [load] {m['n_assets']} assets "
                  f"{m['total_bytes']/1e6:.1f}MB cold "
                  f"{m['cold_total_ms']}ms shell p50/p95 "
                  f"{m['shell_total_ms']}")
    print(f"[live-eval-sb] total wall {time.perf_counter()-t_all:.1f}s")


if __name__ == "__main__":
    main()
