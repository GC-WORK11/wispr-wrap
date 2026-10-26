#!/usr/bin/env python3
"""bench.py — measure the merged Sign-Flow app against a LIVE wfd.py.

Five measured numbers, all on THIS box, all stated with the method used:

  assets   sequential HTTP GET of every asset index.html references
           (+ sw.js), n=5 runs — the server path a fresh phone hits
  chrome   `google-chrome --headless --dump-dom` wall time:
             cold = fresh --user-data-dir + --disk-cache-dir each run (n=5)
             warm = same persistent profile after a priming run that lets
                    the service worker install + precache (n=5)
             baseline = about:blank (chrome process-start overhead, n=3)
  send     POST /send x30 (60-char text, mode:'type' — what compose.js sends),
             per-POST wall time -> p50/p95; unique strings verified to land
             in the realrun sink editor (XTEST keystrokes, not a pipe)
  kp       POST /live/kp at the app's real cadence (~12 Hz, KP_MS=83 in
             web/js/live.js) with wall-clock t_ms: teach 1 sign, bind it,
             palm -> listening, perform -> the response that carries the
             match is timed; per-POST RTT across the whole session -> p50/p95
  weight   byte accounting: shell (what / + its references serve) vs the
             lazily-loaded vendored mediapipe payload (pose.js SIZE_BYTES)

Loopback honesty: every endpoint is 127.0.0.1. These are server-path +
browser-process numbers — NOT phone-over-LAN numbers (no radio RTT, no
throughput cap, no TLS). Where a number would change on WiFi it is said.

Reads the realrun env file for SINK/TERM_WID (X11 editor used for the
send-landing check). Never prints the pairing key.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools", "e2e-live"))
import synth  # noqa: E402  — the same clip grammar kpstream/e2e use

KP_MS = 0.083       # ~12 Hz — web/js/live.js KP_MS (the real phone cadence)
CHROME = os.environ.get("BENCH_CHROME", "google-chrome")


# ---------- small helpers -------------------------------------------------

def now():
    return time.monotonic()


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def stats(xs):
    return {"n": len(xs), "min_ms": round(min(xs) * 1000, 2),
            "p50_ms": round(pct(xs, 50) * 1000, 2),
            "p95_ms": round(pct(xs, 95) * 1000, 2),
            "max_ms": round(max(xs) * 1000, 2),
            "all_ms": [round(x * 1000, 2) for x in xs]}


def req(base, key, method, path, obj=None, raw=None, ctype=None, timeout=15):
    """One HTTP request; returns (status, body_bytes, wall_seconds)."""
    data = raw if raw is not None else \
        (json.dumps(obj).encode() if obj is not None else None)
    r = urllib.request.Request(base + path, data=data, method=method)
    r.add_header("X-WF-Key", key)
    if data is not None:
        r.add_header("Content-Type", ctype or "application/json")
    t0 = now()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read(), now() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read(), now() - t0


def read_env(path):
    env = {}
    try:
        for line in open(path):
            m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
            if m:
                env[m.group(1)] = m.group(2)
    except OSError:
        pass
    return env


# ---------- phase: shell asset list ---------------------------------------

def shell_assets(web_root):
    """Assets a first load pulls: every href/src in index.html + sw.js
    (registered on window 'load' — web/js/app.js)."""
    html = open(os.path.join(web_root, "index.html")).read()
    urls = re.findall(r'(?:href|src)="([^"#]+)"', html)
    seen, out = set(), ["/index.html"]
    for u in urls:
        if u.startswith("http") or u in seen:
            continue
        seen.add(u)
        out.append("/" + u.lstrip("/"))
    if "/sw.js" not in seen:
        out.append("/sw.js")
    return out


def phase_assets(base, key, urls, n=5):
    runs, per = [], {}
    for _ in range(n):
        total_b, total_t = 0, 0.0
        for u in urls:
            st, body, dt = req(base, key, "GET", u)
            if st != 200:
                per.setdefault(u, []).append({"status": st})
                continue
            total_b += len(body)
            total_t += dt
            per.setdefault(u, []).append(
                {"bytes": len(body), "ms": round(dt * 1000, 2)})
        runs.append(total_t)
    return {"urls": urls, "runs_s": [round(r, 4) for r in runs],
            "median_s": round(pct(runs, 50), 4),
            "total_bytes": total_b,
            "per_asset": {u: v[0] for u, v in per.items()}}


def served_mimes(base, key, urls):
    out = {}
    for u in urls:
        r = urllib.request.Request(base + u, method="HEAD")
        r.add_header("X-WF-Key", key)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                out[u] = {"status": resp.status,
                          "ctype": resp.headers.get("Content-Type", ""),
                          "bytes": int(resp.headers.get("Content-Length") or 0)}
        except urllib.error.HTTPError as e:
            out[u] = {"status": e.code}
    return out


# ---------- phase: chrome -------------------------------------------------

def chrome_run(url, profile=None, disk_cache=None, budget=None, extra=None):
    args = [CHROME, "--headless", "--no-sandbox", "--disable-gpu",
            "--no-first-run", "--disable-sync", "--disable-extensions",
            "--disable-background-networking", "--mute-audio"]
    if profile:
        args.append("--user-data-dir=" + profile)
    if disk_cache:
        args.append("--disk-cache-dir=" + disk_cache)
    if budget:
        args.append("--virtual-time-budget=" + str(budget))
    if extra:
        args += extra
    args += ["--dump-dom", url]
    t0 = now()
    p = subprocess.run(args, capture_output=True, text=True, timeout=90)
    wall = now() - t0
    return wall, p.stdout or "", p.stderr or "", p.returncode


def dir_bytes(path):
    total = 0
    for dp, _, fns in os.walk(path):
        for fn in fns:
            try:
                total += os.path.getsize(os.path.join(dp, fn))
            except OSError:
                pass
    return total


def phase_chrome(url, n=5):
    out = {"url": re.sub(r"[?&]k=[^&]+", r"\1k=<redacted>", url)}
    # baseline: process start + trivial page (what every run also pays)
    blanks = []
    for _ in range(3):
        w, dom, err, rc = chrome_run("about:blank",
                                   profile=tempfile.mkdtemp(prefix="wfb-blank-"))
        blanks.append(w)
    out["blank_baseline"] = stats(blanks)

    # cold: brand-new profile + disk cache every run
    cold, cold_dom = [], 0
    for i in range(n):
        w, dom, err, rc = chrome_run(
            url,
            profile=tempfile.mkdtemp(prefix="wfb-cold-p-"),
            disk_cache=tempfile.mkdtemp(prefix="wfb-cold-dc-"),
            budget=15000)
        cold.append(w)
        cold_dom = len(dom)
    out["cold"] = stats(cold)
    out["cold"]["dom_bytes"] = cold_dom

    # warm: one persistent profile. Prime = keep the page open ~20s of
    # real time so the SW installs + precaches SHELL+VENDOR.
    prof = tempfile.mkdtemp(prefix="wfb-warm-p-")
    try:
        subprocess.run([CHROME, "--headless", "--no-sandbox", "--disable-gpu",
                        "--no-first-run", "--disable-sync",
                        "--disable-extensions", "--mute-audio",
                        "--user-data-dir=" + prof, url],
                       capture_output=True, timeout=25)
    except subprocess.TimeoutExpired:
        pass  # expected — killed after the settle window
    sw_dir = os.path.join(prof, "Default", "Service Worker", "CacheStorage")
    out["warm_sw_cache_bytes"] = dir_bytes(sw_dir) if os.path.isdir(sw_dir) else 0
    # one settle nav so the SW controls (register -> activate -> claim)
    chrome_run(url, profile=prof, budget=8000)
    warm, warm_dom = [], 0
    for i in range(n):
        w, dom, err, rc = chrome_run(url, profile=prof, budget=8000)
        warm.append(w)
        warm_dom = len(dom)
    out["warm"] = stats(warm)
    out["warm"]["dom_bytes"] = warm_dom
    out["warm_profile"] = prof
    return out


def chrome_console_probe(url):
    """One instrumented load — capture Chrome's console for MIME drops
    (wfd.py labels .css application/octet-stream; Chrome refuses those)."""
    prof = tempfile.mkdtemp(prefix="wfb-con-")
    _, _, err, _ = chrome_run(url, profile=prof, budget=15000,
                              extra=["--enable-logging=stderr", "--v=0"])
    hits = [l for l in err.splitlines()
            if "Refused to" in l or "MIME" in l or "net::ERR" in l]
    return hits[:40]


# ---------- phase: /send --------------------------------------------------

def phase_send(base, key, sink, sink_wid, display, n=30):
    # focus the realrun editor so XTEST keystrokes have somewhere to land
    if display and sink_wid:
        subprocess.run(["xdotool", "windowactivate", "--sync", str(sink_wid)],
                       env={**os.environ, "DISPLAY": display},
                       capture_output=True, timeout=10)
    texts = [("bench-%02d-" % i + "abcdefghijklmnopqrstuvwxyz"
              "0123456789ABCDEFGHIJKLM")[:60] for i in range(n)]
    for t in texts:
        assert len(t) == 60
    times, codes = [], []
    for t in texts:
        st, body, dt = req(base, key, "POST", "/send",
                           obj={"text": t, "mode": "type"})
        codes.append(st)
        times.append(dt)
    res = {"n": n, "chars": 60, "codes": sorted(set(codes)),
           **stats(times)}
    # the inject queue drains at ~12 ms/char (xdotool --delay 12):
    # ~0.72 s per 60-char send behind the ack — poll the sink.
    deadline = now() + 60
    landed = set()
    while now() < deadline and len(landed) < n:
        try:
            buf = open(sink, errors="replace").read()
        except OSError:
            buf = ""
        for t in texts:
            if t not in landed and t in buf:
                landed.add(t)
        if len(landed) < n:
            time.sleep(0.3)
    res["landed_in_sink"] = len(landed)
    # land-latency samples with a drained queue: POST -> visible in editor
    samples = []
    for i in range(3):
        t = ("land%d-" % i + "z" * 60)[:60]
        t0 = now()
        st, _, _ = req(base, key, "POST", "/send",
                       obj={"text": t, "mode": "type"})
        seen = None
        dd = now() + 8
        while now() < dd:
            try:
                if t in open(sink, errors="replace").read():
                    seen = now() - t0
                    break
            except OSError:
                pass
            time.sleep(0.03)
        samples.append({"chars": len(t), "post_to_land_s":
                        round(seen, 3) if seen else None})
        time.sleep(0.3)
    res["land_samples"] = samples
    return res


# ---------- phase: kp -> decision -----------------------------------------

def stream(base, key, frames, phase, rec, kp_s=KP_MS):
    """POST frames at the app cadence; per-POST wall time recorded.
    rec entries: {phase, i, status, ms, event?}. Returns a dict of
    wall-clock marks for the caller's latency math."""
    marks = {"t_first": None, "t_last_present": None,
             "events": []}          # (resp_time, i, event_dict)
    for i, f in enumerate(frames):
        payload = {"t_ms": int(time.time() * 1000),
                   "points": f["points"]}
        if f.get("hand"):
            payload["hand"] = f["hand"]
        t0 = now()
        st, body, dt = req(base, key, "POST", "/live/kp", obj=payload)
        rec.append({"phase": phase, "i": i, "status": st,
                    "ms": round(dt * 1000, 2)})
        if marks["t_first"] is None:
            marks["t_first"] = t0
        if f.get("_present"):
            marks["t_last_present"] = now()
        try:
            j = json.loads(body)
        except Exception:
            j = {}
        if isinstance(j, dict):
            ev = {k: j[k] for k in ("match", "mode", "teach", "seg")
                  if k in j}
            if ev:
                rec[-1]["event"] = ev
                marks["events"].append((now(), i, ev))
        if st == 429:
            time.sleep(0.3)   # back off; a real phone holds 12 Hz anyway
        time.sleep(kp_s)
    return marks


def phase_kp(base, key, marker_dir, gloss="BENCH-SWIPE"):
    res = {"gloss": gloss, "cadence_hz": round(1 / KP_MS, 1),
           "t_ms": "wall-clock (what the phone sends)"}
    rec = []
    Z = {"x": 0, "y": 0, "z": 0, "score": 0}

    def absent(n):
        return [{"t_ms": 0, "points": [dict(Z) for _ in range(42)]}
                for _ in range(n)]

    # 0) flush: hands away ~1.4 s so any pre-open segment closes first —
    #    like a user settling before they sign.
    stream(base, key, absent(16), "flush", rec)

    # 1) teach: one take through the real teach path
    st, body, dt = req(base, key, "POST", "/teach/begin",
                       obj={"name": gloss, "takes": 1, "label": "bench"})
    res["teach_begin"] = {"status": st, "ms": round(dt * 1000, 2)}
    teach_frames = synth.render_clip(synth.sign_paths()["SWIPE-R"],
                                     n_motion=14, lead=3, tail=3, absent=2,
                                     seed=701)
    for i, f in enumerate(teach_frames):
        f["_present"] = i < len(teach_frames) - 2   # trailing absent tail
    stream(base, key, teach_frames + absent(30), "teach", rec)
    st, body, dt = req(base, key, "POST", "/teach/end", obj={})
    try:
        res["teach_end"] = {"status": st,
                            "json": json.loads(body)}
    except Exception:
        res["teach_end"] = {"status": st, "raw": body[:200].decode("replace")}

    # 2) bind gloss -> marker argv through the real /bank write path
    argv = "/usr/bin/touch " + os.path.join(marker_dir, "bench-fire")
    st, body, dt = req(base, key, "PUT", "/bank",
                       obj={"gloss": gloss, "label": "bench swipe",
                            "argv": argv, "tier": 0})
    res["bind"] = {"status": st, "argv": argv}

    # 3) held open palm -> PALM-OPEN -> listening=True
    palm = synth.render_clip([(0.50, 0.55)], n_motion=24, lead=0, tail=0,
                             absent=0, jitter=0.001)
    for f in palm:
        f["_present"] = True
    pm = stream(base, key, palm + absent(4), "palm", rec)
    palm_mode_t = next((t for t, i, e in pm["events"]
                        if e.get("mode") == "PALM-OPEN"), None)
    res["palm_first_frame_to_mode_s"] = (
        round(palm_mode_t - pm["t_first"], 3)
        if palm_mode_t and pm["t_first"] else None)
    st, body, _ = req(base, key, "GET", "/live/state")
    try:
        stt = json.loads(body)
        res["listening_after_palm"] = stt.get("listening")
    except Exception:
        res["listening_after_palm"] = None

    # 3b) hands-away pause — what a real user does between the palm
    #     listen-gesture and the sign; lets any stale segment close so
    #     the sign gets a clean segment of its own.
    sm = stream(base, key, absent(14), "settle", rec)
    stray = [e["match"] for t, i, e in sm["events"] if "match" in e]
    if stray:
        res["stale_segment_during_settle"] = [
            {"accepted": e.get("accepted"), "gloss": e.get("gloss"),
             "dist": round(e.get("dist") or 0, 4)} for e in stray]

    # 4) perform rounds — two clip geometries:
    #    A) canonical clip: still lead-in + motion + still tail (the lead
    #       stills themselves trip enter_v — the segment covers the whole
    #       clip and reaches the 3.0 s maxlen right at the absent tail)
    #    B) motion-first clip (lead=0): segment opens on the moving hand
    #       and closes through the designed quiet_s path instead.
    rounds = []
    for name, clip_kwargs in (
            ("A-lead", dict(n_motion=20, lead=5, tail=6, absent=3,
                            jitter=0.005, seed=907)),
            ("B-motion-first", dict(n_motion=18, lead=0, tail=4, absent=3,
                                    jitter=0.005, seed=411))):
        perf = synth.render_clip(synth.sign_paths()["SWIPE-R"], **clip_kwargs)
        n_present = clip_kwargs["lead"] + clip_kwargs["n_motion"] + \
            clip_kwargs["tail"]
        for i, f in enumerate(perf):
            f["_present"] = i < n_present
        frames = perf + absent(45)
        mk = stream(base, key, frames, "perform-" + name, rec)
        m = {"round": name, "n_present_frames": n_present}
        matches = [(t, i, e["match"]) for t, i, e in mk["events"]
                   if "match" in e]
        m["match_responses"] = [
            {"post_index": i, "accepted": ev.get("accepted"),
             "gloss": ev.get("gloss"), "dist": round(ev.get("dist") or 0, 4)}
            for t, i, ev in matches]
        acc = next(((t, i, ev) for t, i, ev in matches
                    if ev.get("accepted")), None)
        if acc:
            t_match, i_match, ev = acc
            m["accepted"] = ev
            m["accepted_post_index"] = i_match
            m["first_frame_to_match_s"] = round(t_match - mk["t_first"], 4)
            if mk["t_last_present"]:
                m["hands_down_to_match_s"] = round(
                    t_match - mk["t_last_present"], 4)
            closing = next((r["ms"] for r in rec
                            if r["phase"] == "perform-" + name
                            and r["i"] == i_match), None)
            if closing is not None:
                m["closing_post_ms"] = closing
        rounds.append(m)
        stream(base, key, absent(14), "settle", rec)   # re-settle
    res["rounds"] = rounds
    res["marker_fired"] = os.path.exists(
        os.path.join(marker_dir, "bench-fire"))
    # per-POST RTT stats over every /live/kp call in the session
    rtts = [r["ms"] / 1000.0 for r in rec if r["status"] == 200]
    res["post_rtt"] = stats(rtts)
    res["post_statuses"] = sorted({r["status"] for r in rec})
    res["n_posts"] = len(rec)
    res["posts"] = rec
    return res


# ---------- phase: weight -------------------------------------------------

def phase_weight(web_root, urls):
    shell = []
    for u in urls:
        p = os.path.join(web_root, u.lstrip("/"))
        shell.append({"url": u,
                      "bytes": os.path.getsize(p) if os.path.isfile(p) else 0})
    vend = os.path.join(web_root, "vendor", "mediapipe")
    vendor = []
    for dp, _, fns in os.walk(vend):
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            vendor.append({"file": os.path.relpath(p, web_root),
                           "bytes": os.path.getsize(p)})
    # what the SW would precache (sw.js SHELL + VENDOR lists)
    sw = open(os.path.join(web_root, "sw.js")).read()
    precache = re.findall(r'"(/[^"]*)"', sw)
    pre_bytes = 0
    for u in precache:
        p = os.path.join(web_root, u.lstrip("/") or "index.html")
        if os.path.isfile(p):
            pre_bytes += os.path.getsize(p)
    return {"shell_assets": shell,
            "shell_bytes": sum(a["bytes"] for a in shell),
            "vendor_files": vendor,
            "vendor_bytes": sum(v["bytes"] for v in vendor),
            "sw_precache_bytes": pre_bytes,
            "sw_precache_entries": len(precache)}


# ---------- main -----------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--env", default="/tmp/wf-realrun/env")
    ap.add_argument("--marker-dir", default="/tmp/wf-bench/markers")
    ap.add_argument("--n-load", type=int, default=5)
    ap.add_argument("--n-send", type=int, default=30)
    ap.add_argument("--chrome", action="store_true", default=False)
    ap.add_argument("--send", action="store_true", default=False)
    ap.add_argument("--kp", action="store_true", default=False)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    web_root = os.path.join(ROOT, "web")
    env = read_env(a.env)
    os.makedirs(a.marker_dir, exist_ok=True)

    # repo state for provenance
    head = subprocess.run(["git", "-C", ROOT, "log", "--oneline", "-1"],
                          capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", ROOT, "status", "--short"],
                           capture_output=True, text=True).stdout.strip()
    urls = shell_assets(web_root)

    out = {"meta": {
        "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "box": {"uname": subprocess.run(["uname", "-srvm"],
                                        capture_output=True, text=True)
                .stdout.strip(),
                "cpus": os.cpu_count(),
                "python": sys.version.split()[0]},
        "repo": ROOT, "head": head, "tree_dirty": bool(dirty),
        "daemon_base": a.base,
        "chrome": subprocess.run([CHROME, "--version"],
                                 capture_output=True, text=True).stdout.strip(),
        "endpoint": "loopback 127.0.0.1 — NOT phone-over-LAN"}}

    print("[bench] shell assets:", len(urls))
    out["assets"] = phase_assets(a.base, a.key, urls, n=a.n_load)
    out["assets"]["served_headers"] = served_mimes(a.base, a.key, urls)
    print("[bench] asset fetch median %.1f ms (%d assets, %d B)"
          % (out["assets"]["median_s"] * 1000, len(urls),
             out["assets"]["total_bytes"]))

    if a.chrome:
        keyq = ("?k=" + a.key)
        print("[bench] chrome cold/warm loads (n=%d)…" % a.n_load)
        out["chrome"] = phase_chrome(a.base + "/" + keyq, n=a.n_load)
        out["chrome_console"] = chrome_console_probe(a.base + "/" + keyq)
        print("[bench] chrome cold median %.0f ms / warm %.0f ms"
              % (out["chrome"]["cold"]["p50_ms"],
                 out["chrome"]["warm"]["p50_ms"]))

    if a.send:
        print("[bench] /send x%d…" % a.n_send)
        out["send"] = phase_send(a.base, a.key, env.get("SINK", ""),
                                 env.get("TERM_WID", ""),
                                 env.get("D", ""), n=a.n_send)
        print("[bench] /send p50 %.1f ms p95 %.1f ms, landed %d/%d"
              % (out["send"]["p50_ms"], out["send"]["p95_ms"],
                 out["send"]["landed_in_sink"], a.n_send))

    if a.kp:
        print("[bench] kp -> decision…")
        out["kp"] = phase_kp(a.base, a.key, a.marker_dir)
        accs = [r.get("accepted", {}).get("gloss")
                for r in out["kp"]["rounds"]]
        print("[bench] kp posts n=%d p50 %.1f p95 %.1f; accepts=%s"
              % (out["kp"]["n_posts"], out["kp"]["post_rtt"]["p50_ms"],
                 out["kp"]["post_rtt"]["p95_ms"], accs))

    out["weight"] = phase_weight(web_root, urls)
    print("[bench] shell %d B / vendor %d B"
          % (out["weight"]["shell_bytes"], out["weight"]["vendor_bytes"]))

    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w") as f:
            json.dump(out, f, indent=1)
        print("[bench] wrote", a.out)
    else:
        print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
