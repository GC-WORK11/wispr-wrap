#!/usr/bin/env python3
"""run_eval.py — merged-product eval harness.

Drives the merged product's recognition contract end to end, headless,
before the daemon's /live/kp endpoints exist:

    synthetic 42-pt keypoint frames (synth.py)
      -> hand centroids + flatten (the app's own gates)
      -> endpointer (contract.Endpointer = alive-temporal state machine)
      -> segment Commit -> dominant-hand centroid path
      -> bank match (engine.py: server/signbank.py when present, else the
         local contract port) -> commands.tsv route -> mock spawn

Four arms:
  teach        per-user teach recall — THE headline metric (PRD section 4:
               "nobody has ever measured per-user taught-bank recall").
               Teach N signs x 3 takes, replay held-out performances,
               report answer-rate / accuracy / wrong-sign rate.
  false-accept T seconds of non-sign motion -> routed-action fires/hour.
  latency      sign-end -> match+route decision, decomposed honestly:
               endpointer detect lag (virtual ms) + match/route wall ms.
  load         static-serve web/ via the REAL server/wfd.py; cold/warm
               fetch; reports the service-worker / cache-header gaps.

Determinism: every clip is a pure function of --seed. Wall-clock numbers
(latency, load) are real measurements on this box, not seeded.

Usage:
  python3 tools/live-eval/run_eval.py --arm all --seed 7
  python3 tools/live-eval/run_eval.py --arm teach --signs 10 --takes 3 --heldout 4
Proof JSONs land in tools/live-eval/proofs/.
"""

import argparse
import json
import math
import os
import random
import statistics
import subprocess
import sys
import time
import urllib.request
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import contract  # noqa: E402
import engine    # noqa: E402
import synth     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PROOFS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proofs")

# The 10 taught control signs (PRD section 4 protocol: "teach 10 control
# gestures x3 takes"). PUSH is left out — out-and-back depth barely
# projects to 2D and would fake a vocab slot.
VOCAB = ["SWIPE-R", "SWIPE-L", "SWIPE-DN", "SWIPE-UP", "CIRCLE-CW",
         "CIRCLE-CCW", "ZIGZAG", "SHAKE", "V-DRAW", "HOOK"]

# Mock command table — the commands.tsv contract (GLOSS<TAB>label<TAB>argv,
# direct spawn no shell). The spawn itself is mocked: routing means
# "the row exists and argv is non-empty".
COMMANDS_TSV = """\
# mock ~/.config/wf/commands.tsv for eval
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


def pct(xs, q):
    """Nearest-rank percentile of a sorted-able list; None on empty."""
    xs = sorted(xs)
    if not xs:
        return None
    k = max(1, math.ceil(q / 100 * len(xs)))
    return xs[min(k - 1, len(xs) - 1)]


def mean(xs):
    return statistics.fmean(xs) if xs else None


class Commands:
    """commands.tsv contract: gloss -> (label, argv); route + mock spawn."""

    def __init__(self, tsv_text):
        self.rows = {}
        for line in tsv_text.splitlines():
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            g, label, cmd = parts
            argv = cmd.split()
            if argv:
                self.rows[g.strip().upper()] = (label.strip(), argv)
        self.journal = []

    def route(self, gloss):
        row = self.rows.get(gloss.strip().upper())
        return row  # (label, argv) | None

    def spawn(self, gloss):
        """Mock spawn: a routed action 'fires' — the timestamped journal is
        the whole side effect. A routed-but-missing row is a route miss."""
        row = self.route(gloss)
        if row is None:
            return None
        label, argv = row
        entry = {"gloss": gloss, "label": label, "argv": argv}
        self.journal.append(entry)
        return entry


class Pipeline:
    """Frames -> endpointer -> commits with their segment centroid paths.

    Mirrors the app's feed (main.rs): motion_energy on the flattened
    keypoints of EVERY frame (absent frames flatten empty -> 0), eos_p
    from eos_windowed over the timestamped motion ring, trail samples only
    on present frames.
    """

    def __init__(self, ep_cfg=None, ep_floor=None):
        self.ep = contract.Endpointer(ep_cfg or contract.EndpointConfig())
        floor = ep_floor if ep_floor is not None else self.ep.cfg.low_motion
        self.eos_floor = floor
        self.recent = deque(maxlen=256)
        self.prev_flat = []
        self.trail = []          # (t_ms, hand_a centroid|None, hand_b|None)
        self.commits = []        # {event, path, kind}
        self.events = []

    def feed(self, frame):
        t = frame["t_ms"]
        flat = frame["flat"] if frame["present"] else []
        motion = contract.motion_energy(self.prev_flat, flat)
        self.prev_flat = flat
        self.recent.append((t, motion))
        eos_p = contract.eos_windowed(list(self.recent), t,
                                     floor=self.eos_floor)
        ev = self.ep.push(t, motion, eos_p, frame["present"])
        if frame["present"]:
            ha = (contract.hand_centroid(
                [p[:2] for p in frame["hand_a"]],
                [p[2] for p in frame["hand_a"]])
                if frame["hand_a"] else None)
            hb = (contract.hand_centroid(
                [p[:2] for p in frame["hand_b"]],
                [p[2] for p in frame["hand_b"]])
                if frame["hand_b"] else None)
            self.trail.append((t, ha, hb))
        self._record(ev)
        return ev

    def finish(self, at_ms):
        ev = self.ep.flush(at_ms, 1.0)
        self._record(ev, kind="flush")
        return ev

    def _record(self, ev, kind=None):
        if ev is None:
            return
        self.events.append(ev)
        if ev["event"] == contract.COMMIT:
            path = dominant_path(self.trail, ev["start_ms"], ev["end_ms"])
            k = kind or ("fluent" if ev.get("fluent") else "still_hold")
            self.commits.append({"event": ev, "path": path, "kind": k,
                                 "path_len": len(path)})


def dominant_path(trail, start_ms, end_ms):
    """main.rs dominant_hand_path: the hand seen in more frames of the
    segment supplies its centroid series (only frames where that hand's
    centroid exists)."""
    pa = [(t, p) for (t, a, b) in trail
          if start_ms <= t <= end_ms for p in [a] if p is not None]
    pb = [(t, p) for (t, a, b) in trail
          if start_ms <= t <= end_ms for p in [b] if p is not None]
    raw = pa if len(pa) >= len(pb) else pb
    return [p for _, p in raw]


def calibrate_floor(seed, fps, kp_noise):
    """The honest keypoint-path fix the shipped 0.02 floor needs: measure
    the still-motion distribution this stream actually produces, floor =
    p99 x 1.5. A deployed product would do the same calibration against
    the user's camera/keypoint jitter."""
    frames = synth._still_frames(random.Random(seed), 0, 6000, fps,
                                 wrist=(synth.WIDTH * 0.55,
                                        synth.HEIGHT * 0.6),
                                 noise=kp_noise)
    prev, ms = [], []
    prev = []
    for f in frames:
        flat = f["flat"] if f["present"] else []
        ms.append(contract.motion_energy(prev, flat))
        prev = flat
    ms = [m for m in ms if m > 0]
    p99 = pct(ms, 99) or 0.0
    return max(p99 * 1.5, 0.3), p99


def run_clip(frames, ep_cfg=None, ep_floor=None):
    """Feed one clip; return the pipeline with its commits."""
    pipe = Pipeline(ep_cfg, ep_floor)
    for fr in frames:
        pipe.feed(fr)
    pipe.finish(frames[-1]["t_ms"] + 1)
    return pipe


# ---------------------------------------------------------------------------
# Arm A — per-user teach recall (the headline metric).
# ---------------------------------------------------------------------------

def _accepted(r, reject_t):
    """Match-accept rule shared by every arm: a hit counts when its
    distance clears the bank gate. A NaN distance means a foreign
    recognize-shaped impl already applied its own gate — accepted."""
    if r is None:
        return False
    d = r[1]
    return math.isnan(d) or (math.isfinite(d) and d <= reject_t)


def arm_teach(args, eng_factory, modes):
    """Teach every vocab sign `takes` times, replay `heldout` fresh
    performances per sign, report per-mode metrics. A 'commit' is a bank
    ACCEPT (best_d <= reject_t routed to an action). Each endpointer mode
    gets its OWN bank — the taught exemplars are that mode's committed
    paths, which differ (a flush commit carries the still tail)."""
    out = {"arm": "teach_recall",
           "vocab": VOCAB, "takes": args.takes, "heldout": args.heldout,
           "seed": args.seed, "fps": args.fps, "kp_noise": args.kp_noise,
           "modes": {}}
    cmds = Commands(COMMANDS_TSV)
    # Teach clips + held-out clips are disjoint seeds — same performer
    # model (synthetic single-signer caveat: BENCH.md says what that
    # does and does not prove).
    teach_clips = {g: [synth.perform(g, seed=args.seed * 1000 + vi * 100 + k,
                                   fps=args.fps, kp_noise=args.kp_noise)
                       for k in range(args.takes)]
                   for vi, g in enumerate(VOCAB)}
    eval_clips = {g: [synth.perform(g, seed=args.seed * 7000 + vi * 100 + k,
                                    fps=args.fps, kp_noise=args.kp_noise)
                      for k in range(args.heldout)]
                  for vi, g in enumerate(VOCAB)}

    for mode, cfg in modes.items():
        ep_cfg, ep_floor = cfg["ep_cfg"], cfg["ep_floor"]
        eng = eng_factory()
        out["engine"] = eng.name
        out["reject_threshold"] = eng.reject_threshold
        commits_kind = {}
        teach_ok = teach_fail = 0
        # --- teach phase: take -> segment -> exemplar -------------------
        for g in VOCAB:
            for frames, meta in teach_clips[g]:
                pipe = run_clip(frames, ep_cfg=ep_cfg, ep_floor=ep_floor)
                for c in pipe.commits:
                    commits_kind[c["kind"]] = \
                        commits_kind.get(c["kind"], 0) + 1
                    if eng.teach(g, c["path"]):
                        teach_ok += 1
                    else:
                        teach_fail += 1
        # --- eval phase ------------------------------------------------
        per_eval = []
        for g in VOCAB:
            for frames, meta in eval_clips[g]:
                pipe = run_clip(frames, ep_cfg=ep_cfg, ep_floor=ep_floor)
                cmds.journal.clear()
                rec = {"gloss": g, "seed": meta["seed"],
                       "commits": len(pipe.commits), "fired": 0}
                if pipe.commits:
                    c = pipe.commits[0]     # first commit is the sign's
                    commits_kind[c["kind"]] = \
                        commits_kind.get(c["kind"], 0) + 1
                    r = eng.match(c["path"])
                    if r is not None:
                        hit, d, second = r
                        rec.update(best_d=(round(d, 4)
                                           if math.isfinite(d) else None),
                                   second_d=(round(second, 4)
                                             if math.isfinite(second)
                                             else None))
                        veto = getattr(eng.impl.raw, "traj_channel_agrees",
                                       lambda p, h: True)
                        if _accepted(r, eng.reject_threshold) \
                                and veto(c["path"], hit):
                            fired = cmds.spawn(hit)
                            rec.update(commit=True, hit=hit,
                                       correct=(hit == g),
                                       fired=1 if fired else 0)
                        else:
                            rec.update(commit=False, rejected=True,
                                       nearest=hit)
                    else:
                        rec.update(commit=False, unscorable=True)
                    # Any extra commits in the same clip are spurious —
                    # count them honestly as additional fires.
                    for extra in pipe.commits[1:]:
                        r2 = eng.match(extra["path"])
                        if _accepted(r2, eng.reject_threshold):
                            cmds.spawn(r2[0])
                            rec["fired"] += 1
                        rec["extra_commits"] = rec.get("extra_commits", 0) + 1
                else:
                    rec["no_commit"] = True
                per_eval.append(rec)
        total = len(per_eval)
        committed = [r for r in per_eval if r.get("commit")]
        correct = [r for r in committed if r.get("correct")]
        wrong = [r for r in committed if r.get("commit") and not r.get("correct")]
        rejected = [r for r in per_eval if r.get("rejected")]
        unscorable = [r for r in per_eval if r.get("unscorable")]
        no_commit = [r for r in per_eval if r.get("no_commit")]
        confusion = {}
        for r in wrong:
            confusion[r["hit"]] = confusion.get(r["hit"], 0) + 1
        ds = [r["best_d"] for r in per_eval if "best_d" in r]
        margins = [r["second_d"] - r["best_d"]
                   for r in per_eval
                   if r.get("second_d") is not None]
        out["modes"][mode] = {
            "ep_floor": ep_floor,
            "teach_exemplars_ok": teach_ok,
            "teach_exemplars_refused": teach_fail,
            "bank_size": eng.impl.raw.glosses() if hasattr(
                eng.impl.raw, "glosses") else None,
            "commit_kinds": commits_kind,
            "total_eval": total,
            "answered": len(committed),
            "correct": len(correct),
            "wrong": len(wrong),
            "rejected": len(rejected),
            "unscorable": len(unscorable),
            "no_segment_commit": len(no_commit),
            "extra_commits": sum(r.get("extra_commits", 0) for r in per_eval),
            "answer_rate": len(committed) / total if total else None,
            "accuracy_when_answered": (len(correct) / len(committed)
                                       if committed else None),
            "wrong_sign_rate": len(wrong) / total if total else None,
            "recall_correct_over_all": len(correct) / total if total else None,
            "confusion": confusion,
            "best_d_p50": pct(ds, 50), "best_d_p95": pct(ds, 95),
            "margin_p10": pct(margins, 10),
            "per_eval": per_eval,
        }
    return out


# ---------------------------------------------------------------------------
# Arm B — false accepts on non-sign motion.
# ---------------------------------------------------------------------------

def arm_false_accept(args, eng_factory, modes):
    """T seconds of distractor motion through the same pipeline: count
    commits that match a taught gloss AND route to an action = fires.
    Each mode teaches its own bank (same seeds as the recall arm)."""
    out = {"arm": "false_accept",
           "sim_seconds": args.idle_seconds, "seed": args.seed,
           "fps": args.fps, "kp_noise": args.kp_noise, "modes": {}}
    frames = synth.idle_stream(args.seed, args.idle_seconds,
                               fps=args.fps, noise=args.kp_noise)
    sim_s = frames[-1]["t_ms"] / 1000.0
    for mode, cfg in modes.items():
        ep_cfg, ep_floor = cfg["ep_cfg"], cfg["ep_floor"]
        eng = eng_factory()
        out["engine"] = eng.name
        out["reject_threshold"] = eng.reject_threshold
        # Teach this mode's bank first — a fire only exists against a
        # real bank.
        for vi, g in enumerate(VOCAB):
            for k in range(args.takes):
                f2, _ = synth.perform(
                    g, seed=args.seed * 1000 + vi * 100 + k,
                    fps=args.fps, kp_noise=args.kp_noise)
                p2 = run_clip(f2, ep_cfg=ep_cfg, ep_floor=ep_floor)
                for c in p2.commits:
                    eng.teach(g, c["path"])
        pipe = Pipeline(ep_cfg, ep_floor)
        cmds = Commands(COMMANDS_TSV)
        fired = []
        n_tent = 0
        t0 = time.perf_counter()
        for fr in frames:
            ev = pipe.feed(fr)
            if ev and ev["event"] == contract.TENTATIVE:
                n_tent += 1
            for c in [c for c in pipe.commits if not c.get("scored")]:
                c["scored"] = True
                r = eng.match(c["path"])
                if _accepted(r, eng.reject_threshold):
                    hit, d, _ = r
                    fired.append({"t_ms": c["event"]["end_ms"],
                                  "hit": hit,
                                  "d": round(d, 4) if math.isfinite(d) else None,
                                  "kind": c["kind"],
                                  "routed": cmds.spawn(hit) is not None})
        pipe.finish(frames[-1]["t_ms"] + 1)
        for c in pipe.commits:
            if not c.get("scored"):
                c["scored"] = True
                r = eng.match(c["path"])
                if _accepted(r, eng.reject_threshold):
                    fired.append({"t_ms": c["event"]["end_ms"], "hit": r[0],
                                  "d": round(r[1], 4)
                                  if math.isfinite(r[1]) else None,
                                  "kind": c["kind"],
                                  "routed": cmds.spawn(r[0]) is not None})
        wall = time.perf_counter() - t0
        kinds = {}
        for c in pipe.commits:
            kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
        by_hit = {}
        for f_ in fired:
            by_hit[f_["hit"]] = by_hit.get(f_["hit"], 0) + 1
        hrs = sim_s / 3600.0
        out["modes"][mode] = {
            "ep_floor": ep_floor,
            "frames": len(frames), "sim_seconds": round(sim_s, 1),
            "commits": len(pipe.commits), "commit_kinds": kinds,
            "tentatives": n_tent,
            "matches_accepted": len(fired),
            "routed_fires": sum(1 for f_ in fired if f_["routed"]),
            "fires_per_hour": round(len(fired) / hrs, 2),
            "by_hit": by_hit, "fired": fired,
            "feed_wall_s": round(wall, 2),
        }
    return out


# ---------------------------------------------------------------------------
# Arm C — sign-end -> match+route latency.
# ---------------------------------------------------------------------------

def arm_latency(args, eng_factory, modes):
    """Per held-out clip: virtual detect lag (sign motion end -> Commit)
    plus wall-clock match+route+mock-spawn at the commit. Reported
    decomposed — the still-hold wait is a designed pause, not compute; the
    LAN POST (3-10ms, PRD) and real xdotool spawn (10-40ms, PRD) are NOT
    in decide_ms. Each mode teaches its own bank."""
    out = {"arm": "latency", "seed": args.seed,
           "fps": args.fps, "kp_noise": args.kp_noise, "modes": {},
           "not_measured": [
               "keypoints->daemon LAN POST (~3-10ms per PRD)",
               "real action spawn / xdotool (~10-40ms per PRD)"]}
    eval_clips = [synth.perform(g, seed=args.seed * 7000 + vi * 100 + k,
                                fps=args.fps, kp_noise=args.kp_noise)
                  for vi, g in enumerate(VOCAB)
                  for k in range(args.heldout)]
    cmds = Commands(COMMANDS_TSV)
    for mode, cfg in modes.items():
        eng = eng_factory()
        out["engine"] = eng.name
        out["reject_threshold"] = eng.reject_threshold
        for vi, g in enumerate(VOCAB):
            for k in range(args.takes):
                f2, _ = synth.perform(
                    g, seed=args.seed * 1000 + vi * 100 + k,
                    fps=args.fps, kp_noise=args.kp_noise)
                p2 = run_clip(f2, ep_cfg=cfg["ep_cfg"],
                              ep_floor=cfg["ep_floor"])
                for c in p2.commits:
                    eng.teach(g, c["path"])
        rows = []
        for frames, meta in eval_clips:
            pipe = run_clip(frames, ep_cfg=cfg["ep_cfg"],
                            ep_floor=cfg["ep_floor"])
            for c in pipe.commits:
                ev = c["event"]
                # virtual: last ground-truth sign ms -> commit boundary.
                detect = ev["end_ms"] - meta["sign_end_ms"]
                t0 = time.perf_counter_ns()
                r = eng.match(c["path"])
                t1 = time.perf_counter_ns()
                fired = cmds.spawn(r[0]) if _accepted(
                    r, eng.reject_threshold) else None
                t2 = time.perf_counter_ns()
                rows.append({
                    "gloss": meta["gloss"], "kind": c["kind"],
                    "detect_lag_ms": detect,
                    "match_ms": (t1 - t0) / 1e6,
                    "route_ms": (t2 - t1) / 1e6,
                    "decide_ms": (t2 - t0) / 1e6,
                    "fired": bool(fired), "path_len": c["path_len"]})
        det = [r["detect_lag_ms"] for r in rows]
        dec = [r["decide_ms"] for r in rows]
        mch = [r["match_ms"] for r in rows]
        e2e = [r["detect_lag_ms"] + r["decide_ms"] for r in rows]
        out["modes"][mode] = {
            "ep_floor": cfg["ep_floor"], "commits": len(rows),
            "commit_kinds": {k: sum(1 for r in rows if r["kind"] == k)
                             for k in set(r["kind"] for r in rows)},
            "detect_lag_ms": {"p50": pct(det, 50), "p95": pct(det, 95),
                              "mean": round(mean(det), 1) if det else None},
            "decide_ms": {"p50": round(pct(dec, 50), 3),
                          "p95": round(pct(dec, 95), 3),
                          "max": round(max(dec), 3) if dec else None},
            "match_only_ms": {"p50": round(pct(mch, 50), 3),
                              "p95": round(pct(mch, 95), 3)},
            "sign_end_to_decide_ms": {"p50": round(pct(e2e, 50), 1),
                                      "p95": round(pct(e2e, 95), 1)},
            "per_commit": rows,
        }
    return out


# ---------------------------------------------------------------------------
# Arm D — composer shell load time via the REAL wfd.py static server.
# ---------------------------------------------------------------------------

def arm_load(args, eng_factory, modes):
    """Spawn server/wfd.py (stdlib, the shipped daemon) on a free port and
    fetch the composer shell the way a phone browser would. There is no
    service worker and wfd sends no cache headers — warm == the second
    real network fetch, not a cache hit; reported as such."""
    import socket
    out = {"arm": "load", "engine": "n/a (real wfd.py)", "modes": {}}
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
        start_ms = (time.perf_counter() - t0) * 1000
        if not up:
            out["error"] = "wfd.py did not come up in 15s"
            return out
        out["daemon_start_ms"] = round(start_ms, 1)

        assets = ["/", "/manifest.webmanifest", "/icon.svg"]
        hdrs = {"Connection": "close"}

        def fetch(path):
            t = time.perf_counter()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}", headers=hdrs)
            with urllib.request.urlopen(req, timeout=5) as r:
                body = r.read()
                meta = {"status": r.status, "bytes": len(body),
                        "cache_control": r.headers.get("Cache-Control"),
                        "etag": r.headers.get("ETag"),
                        "last_modified": r.headers.get("Last-Modified"),
                        "connection": r.headers.get("Connection"),
                        "server": r.headers.get("Server")}
            return (time.perf_counter() - t) * 1000, meta

        cold, warm, resp_meta = {}, {}, {}
        # Cold: first-ever fetch of each asset after daemon start.
        for a in assets:
            ms, meta = fetch(a)
            cold[a] = round(ms, 2)
            resp_meta[a] = meta
        # Warm: repeats — same process, warm page cache; still real TCP
        # + HTTP round trips because HTTP/1.0 closes every connection.
        for a in assets:
            xs = [fetch(a)[0] for _ in range(args.load_reps)]
            warm[a] = {"p50": round(pct(xs, 50), 2),
                       "p95": round(pct(xs, 95), 2)}
        # A shell load = sequential fresh fetches like a first paint.
        xs = []
        for _ in range(args.load_reps):
            xs.append(sum(fetch(a)[0] for a in assets))
        out["modes"]["loopback"] = {
            "assets": assets,
            "cold_fetch_ms": cold,
            "warm_fetch_ms": warm,
            "shell_total_ms": {"p50": round(pct(xs, 50), 2),
                               "p95": round(pct(xs, 95), 2)},
            "response_meta": resp_meta,
            "service_worker_present": os.path.exists(
                os.path.join(ROOT, "web", "sw.js")) or
            "serviceworker" in open(
                os.path.join(ROOT, "web", "index.html")).read().lower(),
            "cache_headers_present": any(
                resp_meta[a]["cache_control"] for a in assets),
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    return out


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", default="all",
                    choices=["all", "teach", "false-accept", "latency", "load"])
    ap.add_argument("--engine", default="auto",
                    choices=["auto", "signbank", "contract"])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--kp-noise", type=float, default=1.2,
                    help="per-keypoint jitter sigma in px (MediaPipe-scale)")
    ap.add_argument("--takes", type=int, default=3)
    ap.add_argument("--heldout", type=int, default=4)
    ap.add_argument("--idle-seconds", type=int, default=300)
    ap.add_argument("--load-reps", type=int, default=30)
    ap.add_argument("--reject", type=float, default=0.45)
    ap.add_argument("--out", default=PROOFS)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    kp_floor, still_p99 = calibrate_floor(args.seed + 1, args.fps,
                                        args.kp_noise)
    modes = {
        # As shipped: the Rust default floor, calibrated for pixel-space
        # RTMW + the mscore squash the keypoint path does not have.
        "shipped": {"ep_cfg": contract.EndpointConfig(), "ep_floor": 0.02},
        # Keypoint-calibrated: still-motion p99 x 1.5 — what a deployed
        # phone-path product must do per-camera.
        "kp": {"ep_cfg": contract.EndpointConfig(low_motion=kp_floor),
               "ep_floor": kp_floor},
        # The fluent-dip lever the latency arm reports (off by default in
        # the app; here as the measured alternative).
        "kp_fluent": {"ep_cfg": contract.EndpointConfig(
            low_motion=kp_floor, fluent_dip_ms=120),
            "ep_floor": kp_floor},
    }
    eng0 = engine.Engine(args.engine, reject_threshold=args.reject)
    meta = {"tool": "tools/live-eval/run_eval.py", "seed": args.seed,
            "engine": eng0.name, "reject_threshold": eng0.reject_threshold,
            "kp_floor_calibrated": round(kp_floor, 3),
            "still_motion_p99": round(still_p99, 4),
            "python": sys.version.split()[0],
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    print(f"[live-eval] engine={eng0.name} seed={args.seed} "
          f"kp_floor={kp_floor:.2f} (still p99 {still_p99:.3f})")

    def eng_factory():
        return engine.Engine(args.engine, reject_threshold=args.reject)

    arms = (["teach", "false-accept", "latency", "load"]
            if args.arm == "all" else [args.arm])
    summary = {}
    for arm in arms:
        fn = {"teach": arm_teach, "false-accept": arm_false_accept,
              "latency": arm_latency, "load": arm_load}[arm]
        res = fn(args, eng_factory, modes)
        res["meta"] = meta
        path = os.path.join(args.out, f"{arm.replace('-', '_')}.json")
        with open(path, "w") as f:
            json.dump(res, f, indent=1)
        print(f"[live-eval] wrote {path}")
        summary[arm] = res

    # Compact stdout summary — the four numbers.
    for arm, res in summary.items():
        print(f"\n=== {arm} ===")
        if arm == "load":
            m = res.get("modes", {}).get("loopback", {})
            print(f"  daemon start: {res.get('daemon_start_ms')} ms")
            print(f"  shell total p50/p95: {m.get('shell_total_ms')}")
            print(f"  SW present: {m.get('service_worker_present')}  "
                  f"cache hdrs: {m.get('cache_headers_present')}")
            continue
        for mode, r in res.get("modes", {}).items():
            if arm == "teach":
                print(f"  [{mode}] answered {r['answered']}/{r['total_eval']} "
                      f"correct {r['correct']} wrong {r['wrong']} "
                      f"answer_rate={r['answer_rate']:.3f} "
                      f"acc={r['accuracy_when_answered']} "
                      f"kinds={r['commit_kinds']}")
            elif arm == "false-accept":
                print(f"  [{mode}] commits {r['commits']} "
                      f"accepted {r['matches_accepted']} "
                      f"fires/hr {r['fires_per_hour']} hits={r['by_hit']}")
            elif arm == "latency":
                print(f"  [{mode}] detect p50/p95 "
                      f"{r['detect_lag_ms']['p50']}/{r['detect_lag_ms']['p95']}ms "
                      f"decide p50/p95 {r['decide_ms']['p50']}/"
                      f"{r['decide_ms']['p95']}ms "
                      f"e2e p95 {r['sign_end_to_decide_ms']['p95']}ms")


if __name__ == "__main__":
    main()
