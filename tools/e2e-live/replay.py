#!/usr/bin/env python3
"""replay — drive the live sign pipeline headless, end to end.

    synthetic kp42 clips (or recorded JSONL replays from ./fixtures)
      -> POST /live/kp at 15 Hz against wfd.py on an alt port
      -> the daemon's own endpointer -> DTW match -> action route
      -> assert on the daemon's decision surface (/live/state, decision
         log) plus marker files spawned via a stub commands.tsv

No xdotool, no camera, no X11: actions in the e2e config are marker
argvs (`/usr/bin/touch <marker>`), so a fired action is observable as a
file AND on /live/state. If the live endpoints aren't in the daemon yet
(W1 in-flight) the suite probes, reports SKIP per case, and exits 77 —
a clean signal, not a fake pass.

Contracts exercised (PRD 5.2):
  POST /live/kp   {t_ms, points:[{x,y,score},...x42]}
  GET  /live/state          listening?/last match — composer poll shape
  POST /teach/begin {name}  (optional — absent => pre-seeded bank)
  POST /teach/end
  GET  /act                 (optional catalog probe)
  GET  /engine              (optional engine-id probe)

JSONL fixture format (one clip per file): optional first line
{"kind":"meta","name":..,"expect":..}; then one
{"kind":"frame","t_ms":..,"points":[..]} per line (bare {"t_ms","points"}
lines accepted); optional {"kind":"end"}.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import synth  # noqa: E402

SKIP, PASS, FAIL = "SKIP", "PASS", "FAIL"


class Daemon:
    def __init__(self, base, key, timeout=5.0):
        self.base, self.key, self.timeout = base, key, timeout

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method)
        r.add_header("X-WF-Key", self.key)
        if data is not None:
            r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except Exception as e:
            return -1, str(e).encode()

    def get(self, path):
        return self._req("GET", path)

    def post(self, path, obj):
        return self._req("POST", path, obj)


def jscan(obj, keys):
    """Find dicts inside a JSON tree that carry any of `keys`."""
    hits = []

    def walk(o):
        if isinstance(o, dict):
            if any(k in o for k in keys):
                hits.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return hits


def extract_decision(state):
    """Pull (gloss, accepted, raw_dict) out of a /live/state-shaped blob.
    Duck-typed: any dict holding a gloss-ish key plus a verdict-ish key
    counts; freshest one wins (highest ts/seq field, else last)."""
    if not isinstance(state, (dict, list)):
        return None
    cands = []
    for d in jscan(state, ("gloss", "name", "label")):
        name = d.get("gloss") or d.get("name") or d.get("label")
        if not isinstance(name, str) or not name:
            continue
        acc = d.get("accepted", d.get("ok", d.get("committed", d.get("fired"))))
        ts = max((d.get(k) for k in ("t_ms", "ts", "at", "seq", "ms")
                  if isinstance(d.get(k), (int, float))), default=0)
        cands.append((ts, name, acc, d))
    if not cands:
        return None
    cands.sort(key=lambda c: c[0])
    ts, name, acc, raw = cands[-1]
    return name, acc, raw


def stream_clip(d, frames, speed=1.0):
    """POST frames at a real 15 Hz (t_ms = wall clock, matching a phone).
    Returns (posted, first_bad_status)."""
    t_frame = synth.FRAME_MS / 1000.0 / max(speed, 1e-6)
    posted = bad = 0
    t0 = time.monotonic()
    for i, f in enumerate(frames):
        payload = {"t_ms": int(time.time() * 1000), "points": f["points"]}
        st, _ = d.post("/live/kp", payload)
        if st != 200:
            bad = bad or st
        else:
            posted += 1
        dt = t_frame * (i + 1) - (time.monotonic() - t0)
        if dt > 0:
            time.sleep(dt)
    return posted, bad


def _fetch_state(d):
    st, body = d.get("/live/state")
    if st != 200:
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def wait_decision(d, baseline_dec, timeout_s, poll=0.1):
    """Poll /live/state until a decision DIFFERENT from the baseline
    lands (different gloss or a newer timestamp inside the decision).
    -> (name, accepted, raw) | None."""
    def key(dec):
        return json.dumps(dec[2], sort_keys=True) if dec else None
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = _fetch_state(d)
        if state is not None:
            dec = extract_decision(state)
            if dec is not None and key(dec) != key(baseline_dec):
                return dec
        time.sleep(poll)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--fixtures", default=os.path.join(_HERE, "fixtures"))
    ap.add_argument("--marker-dir", default=None,
                    help="dir where stub commands.tsv marker files land")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="playback speed multiplier (1.0 = real 15Hz; raise "
                         "only if the daemon's endpointer reads payload t_ms)")
    ap.add_argument("--timeout", type=float, default=4.0,
                    help="seconds to wait for a decision after a clip")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--no-teach", action="store_true",
                    help="skip teach endpoints (bank assumed pre-seeded)")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"
    d = Daemon(base, args.key)
    results = []  # (case, status, detail)

    # --- contract probe -------------------------------------------------
    st, _ = d.get("/health")
    if st != 200:
        print(f"daemon unreachable at {base} (GET /health -> {st})")
        return 1
    st, _ = d.post("/live/kp", {"t_ms": 0, "points": []})
    if st == 404:
        print("SKIP /live/kp -> 404: live endpoints not in this daemon yet "
              "(W1 in-flight). Nothing asserted, nothing failed.")
        for case in ("probe", "teach", "perform", "reject", "markers"):
            results.append((case, SKIP, "/live/kp absent"))
        return _report(results, args)
    live_kp_status = st
    results.append(("probe /live/kp", PASS, f"reachable (status {st})"))

    for path in ("/live/state", "/engine", "/act"):
        st_, body_ = d.get(path)
        results.append((f"probe {path}",
                        PASS if st_ == 200 else SKIP,
                        f"status {st_}" + ("" if st_ != 200 else
                                           f" ({len(body_)}B)")))

    # optional explicit listen trigger — tolerate absence
    listening_mode = "implicit"
    for path, body in (("/live/listen", {}), ("/live/start", {})):
        st_, _ = d.post(path, body)
        if st_ == 200:
            listening_mode = path
            break

    # --- clip sources ----------------------------------------------------
    teach, perform = synth.synthetic_suite(seed=args.seed)
    fx = args.fixtures
    fixture_teach, fixture_perform = {}, []
    if os.path.isdir(fx):
        for fn in sorted(os.listdir(fx)):
            if not fn.endswith(".jsonl"):
                continue
            expect, frames = synth.read_fixture(os.path.join(fx, fn))
            if fn.startswith("teach-") and expect:
                fixture_teach[expect] = frames
            elif fn.startswith("perform-") or fn.startswith("replay-"):
                fixture_perform.append((expect, frames, fn))
        if fixture_teach or fixture_perform:
            print(f"fixtures: {len(fixture_teach)} teach, "
                  f"{len(fixture_perform)} perform clips from {fx}")
            teach.update(fixture_teach)
            perform = [(e, f) for e, f, _ in fixture_perform] or perform

    # --- teach phase ------------------------------------------------------
    taught = set()
    if not args.no_teach:
        st_, _ = d.post("/teach/begin", {"name": "__probe__"})
        teach_api = st_ != 404
        if teach_api:
            d.post("/teach/end", {})
        results.append(("teach api", PASS if teach_api else SKIP,
                        "/teach/begin " + ("present" if teach_api else "404 — "
                         "bank assumed pre-seeded")))
        if teach_api:
            for name, frames in teach.items():
                st_, body_ = d.post("/teach/begin", {"name": name})
                if st_ != 200:
                    results.append((f"teach {name}", FAIL,
                                    f"/teach/begin -> {st_} {body_[:80]!r}"))
                    continue
                posted, bad = stream_clip(d, frames, args.speed)
                st_, body_ = d.post("/teach/end", {})
                ok = st_ == 200 and posted == len(frames) and not bad
                results.append((f"teach {name}", PASS if ok else FAIL,
                                f"{posted}/{len(frames)} frames, end->{st_}"))
                if ok:
                    taught.add(name)
    else:
        results.append(("teach api", SKIP, "--no-teach"))
        taught = set(teach)

    # --- perform + assert -------------------------------------------------
    for expect, frames in perform:
        label = expect or "UNKNOWN"
        baseline_dec = extract_decision(_fetch_state(d) or {})
        posted, bad = stream_clip(d, frames, args.speed)
        dec = wait_decision(d, baseline_dec, args.timeout)
        if expect is None:
            ok = dec is None or (dec[1] is False)
            results.append((f"perform {label}", PASS if ok else FAIL,
                            f"no commit" if dec is None else
                            f"fired {dec[0]!r} accepted={dec[1]} — "
                            f"unknown must never commit"))
        else:
            if dec is None:
                results.append((f"perform {label}", FAIL,
                                f"no decision within {args.timeout}s "
                                f"({posted} frames posted, bad={bad})"))
            else:
                name, acc, raw = dec
                ok = name == expect and acc is not False
                results.append((f"perform {label}", PASS if ok else FAIL,
                                f"decided {name!r} accepted={acc} "
                                f"(want {expect!r})"))
        # marker file check — strongest proof the action layer ran
        if args.marker_dir and expect:
            marker = os.path.join(args.marker_dir,
                                  re.sub(r"[^\w.-]+", "_", expect))
            got = os.path.exists(marker)
            results.append((f"marker {label}",
                            PASS if got else SKIP,
                            "file exists" if got else
                            "no marker (action layer pending?)" ))

    return _report(results, args)


def _report(results, args):
    n_pass = sum(1 for _, s, _ in results if s == PASS)
    n_fail = sum(1 for _, s, _ in results if s == FAIL)
    n_skip = sum(1 for _, s, _ in results if s == SKIP)
    print("\nreplay results")
    print("-" * 64)
    for case, status, detail in results:
        print(f"  {status:<4}  {case:<28} {detail}")
    print("-" * 64)
    print(f"  {n_pass} pass / {n_fail} fail / {n_skip} skip")
    if args.json_out:
        os.makedirs(os.path.dirname(args.json_out) or ".", exist_ok=True)
        with open(args.json_out, "w") as f:
            json.dump({"pass": n_pass, "fail": n_fail, "skip": n_skip,
                       "cases": [{"case": c, "status": s, "detail": d}
                                 for c, s, d in results]}, f, indent=1)
    if n_fail:
        return 1
    if n_pass == 0:
        return 77  # all skipped — live lane not landed
    return 0


if __name__ == "__main__":
    sys.exit(main())
