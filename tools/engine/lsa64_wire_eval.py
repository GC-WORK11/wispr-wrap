#!/usr/bin/env python3
"""Real signers through the daemon's own live stack: kp42 wire -> endpointer -> signbank.

This is what the laptop-webcam engine (sign-flow `sf-engine`) feeds wfd.py: one kp42 frame per pose
(points[0:21] = the signer's left hand, points[21:42] = right; x,y normalized to the frame; score
0..1). Here the frames come from LSA64 keypoint files that the Sign-Flow pose stack already produced
(yolox-m + rtmw-l, 15 Hz, `tools/teach/lsa64_extract.py` in sign-flow), so the daemon logic can be
measured on real signers without a camera.

Protocol (the Teach flow of the app): per signer, TAUGHT signs are taught with reps 1-3 exactly like
POST /teach/begin does it (the next 3 endpointed segments become exemplars); then reps 4-5 of every
taught sign and of the untaught NEG signs are performed. A clip is padded with the signer standing
still (first/last row repeated) so the endpointer sees rest -> sign -> rest, like a live stream.
Per performed clip: the first accepted match (else the closest rejected one), or no segment at all.

LSA64 (Ronchetti et al. 2016) is CC BY-NC-SA 4.0: keypoints stay outside git; only numbers are kept.

  lsa64_wire_eval.py --kp /path/lsa64-kp --signers 6,7,8,9,10 [--units norm|px] [--json out.json]
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "server"))
import endpointer  # noqa: E402
import signbank  # noqa: E402

TAUGHT = "001 009 017 025 033 041 049 057".split()
NEG = "003 007 011 013 019 027 029 035 045 051 061 063".split()
PAD = 8          # rows of standing still before/after a clip (~0.5 s at 15 Hz)


def load(kp_dir, sign, signer, rep):
    p = os.path.join(kp_dir, f"{sign}_{signer:03d}_{rep:03d}.json")
    with open(p) as f:
        d = json.load(f)
    return d["rows"], float(d.get("hz", 15.0)), float(d.get("aspect", 16 / 9))


def wire(rows, hz, t0_ms, units, w, h):
    """kp53 rows -> kp42 wire frames (the engine's exact mapping)."""
    sx, sy = (w, h) if units == "px" else (1.0, 1.0)
    out = []
    for i, r in enumerate(rows):
        pts = []
        for j in range(11, 53):
            x, y, s = r[j]
            pts.append({"x": x * sx, "y": y * sy, "score": s})
        out.append({"t_ms": int(t0_ms + i * 1000.0 / hz), "points": pts})
    return out


EP_KW = {}       # --ep quiet_s=0.5 ... (endpointer knob overrides; defaults = shipped)


class Stream:
    """One continuous kp42 stream into one endpointer (the daemon's LIVE['ep'])."""

    def __init__(self, units, w, h):
        self.ep = endpointer.Endpointer(palm_sign=None, namaste_sign=None, **EP_KW)
        self.t = 0.0
        self.units, self.w, self.h = units, w, h

    def play(self, rows, hz):
        rows = [rows[0]] * PAD + list(rows) + [rows[-1]] * PAD
        frames = wire(rows, hz, self.t, self.units, self.w, self.h)
        self.t = frames[-1]["t_ms"] + 1000.0 / hz
        segs = []
        for fr in frames:
            for ev in self.ep.feed(fr):
                if ev.get("type") == "segment":
                    segs.append(ev["frames"])
        return segs


def run_signer(kp_dir, signer, units, taught, neg):
    _, hz, aspect = load(kp_dir, taught[0], signer, 1)
    w, h = 960.0, 960.0 / aspect
    st = Stream(units, w, h)
    bank = signbank.SignBank()
    teach_segs = {}
    for s in taught:                       # /teach/begin {takes:3}: next 3 segments
        got = []
        for rep in (1, 2, 3):
            rows, hz, _ = load(kp_dir, s, signer, rep)
            got.extend(st.play(rows, hz))
        teach_segs[s] = len(got)
        for seg in got[:3]:
            bank.add_exemplar("LSA" + s, seg)
    res = []
    match_ms = []
    for kind, signs in (("taught", taught), ("untaught", neg)):
        for s in signs:
            for rep in (4, 5):
                rows, hz, _ = load(kp_dir, s, signer, rep)
                segs = st.play(rows, hz)
                acc, near = None, None
                for seg in segs:
                    t0 = time.perf_counter()
                    m = bank.match(seg)
                    match_ms.append((time.perf_counter() - t0) * 1000.0)
                    if m is None:
                        continue
                    if m.accepted and acc is None:
                        acc = m.gloss
                    if near is None:
                        near = m.gloss
                res.append({"kind": kind, "sign": "LSA" + s, "rep": rep, "segs": len(segs),
                            "accepted": acc, "nearest": near})
    return res, teach_segs, match_ms, bank.reject_threshold


def summarize(rows):
    pos = [r for r in rows if r["kind"] == "taught"]
    neg = [r for r in rows if r["kind"] == "untaught"]
    right = sum(1 for r in pos if r["accepted"] == r["sign"])
    wrong = sum(1 for r in pos if r["accepted"] and r["accepted"] != r["sign"])
    noseg = sum(1 for r in pos if r["segs"] == 0)
    near_right = sum(1 for r in pos if r["nearest"] == r["sign"])
    fa = sum(1 for r in neg if r["accepted"])
    return {"taught_clips": len(pos), "right": right, "wrong": wrong, "no_segment": noseg,
            "recall": round(right / max(1, len(pos)), 3),
            "precision": round(right / max(1, right + wrong + fa), 3),
            "nearest_right": near_right, "untaught_clips": len(neg), "false_accepts": fa,
            "fa_rate": round(fa / max(1, len(neg)), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kp", required=True)
    ap.add_argument("--signers", default="6,7,8,9,10")
    ap.add_argument("--units", default="norm", choices=("norm", "px"))
    ap.add_argument("--json")
    ap.add_argument("--ep", action="append", default=[], help="endpointer knob, e.g. quiet_s=0.5")
    ap.add_argument("--quiet", action="store_true", help="only the ALL line")
    a = ap.parse_args()
    for kv in a.ep:
        k, v = kv.split("=", 1)
        EP_KW[k] = float(v)
    allrows, per, ms = [], {}, []
    for sg in [int(x) for x in a.signers.split(",") if x]:
        rows, tsegs, mms, thr = run_signer(a.kp, sg, a.units, TAUGHT, NEG)
        allrows += [dict(r, signer=sg) for r in rows]
        ms += mms
        per[sg] = dict(summarize(rows), teach_segments=tsegs)
        if not a.quiet:
            print(f"signer {sg} ({a.units}): {json.dumps(per[sg])}", flush=True)
    tot = summarize(allrows)
    ms.sort()
    q = (lambda p: round(ms[min(len(ms) - 1, int(p * len(ms)))], 2)) if ms else (lambda p: None)
    tot["match_ms_p50"], tot["match_ms_p95"] = q(0.5), q(0.95)
    tot["reject_threshold"] = thr
    segs = [r["segs"] for r in allrows]
    tot["perform_segments_per_clip"] = {str(k): segs.count(k) for k in sorted(set(segs))}
    tot["teach_segments_per_3_takes"] = sorted(
        (v for p in per.values() for v in p["teach_segments"].values()))
    print(f"ALL ({a.units}, ep {EP_KW or 'shipped'}): {json.dumps(tot)}")
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"units": a.units, "taught": TAUGHT, "neg": NEG, "per_signer": per,
                       "total": tot, "clips": allrows}, f, indent=1)


if __name__ == "__main__":
    main()
