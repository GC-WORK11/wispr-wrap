#!/usr/bin/env python3
"""stubdaemon — the CONTRACT DOUBLE for the live pipeline.

This is NOT the product engine. It is a minimal, honest implementation
of the /live contract from the merged-product PRD, built on refmatch.py
(the matcher.rs port), so the e2e suite can run green end-to-end BEFORE
W1's real endpoints land in server/wfd.py — and so the contract has an
executable spec W1 can run the same suite against:

    POST /live/kp    {t_ms, points:[{x,y,score} xN]}  -> {"ok":true}
    GET  /live/state -> {"listening","engine","last":{gloss,dist,
                       accepted,action,seq,t_ms}|null,"buffer":n}
    POST /teach/begin {"name":G} / POST /teach/end    -> next endpointed
                       segment becomes a taught exemplar of G
    GET  /act        -> commands.tsv rows as [{gloss,label,argv,tier}]
    GET  /engine     -> {"engine":"contract-stub-python-dtw","format":"kpN"}

Pipeline inside: kp42 frame -> dominant-hand score-weighted centroid ->
motion/stillness endpointer -> RefBank.match (resample-32, band-4,
reject_threshold) -> accepted gloss routes through the config's
commands.tsv, argv-spawned without a shell (marker files in e2e).

Config dir layout (same as the product):
    <config>/key           shared auth key (X-WF-Key on everything but /health)
    <config>/commands.tsv  GLOSS\tlabel\targv
    <config>/signbank.json Rust-format bank (optional — a missing file is
                         an empty bank; /teach populates + persists it)

Run: python3 stubdaemon.py --port 8893 --config /tmp/wfcfg
"""
import argparse
import hmac
import json
import math
import os
import shlex
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refmatch  # noqa: E402

MIN_SCORE = 0.3      # confident-keypoint gate (matcher.rs min_score idiom)
MIN_KEYS = 4         # of 21, to trust a hand centroid
ABSENT_END = 3       # frames with no hand that close a segment
STILL_END = 8        # still frames that close a segment
STILL_EPS = 0.004    # per-frame move (in frame units) under which "still"
MAX_SEG = 240        # force-close a runaway segment


def dominant_centroid(points):
    """kpN frame -> (x,y) of the more confident hand, or None.
    Both 42-pt (two hands) and 21-pt (one hand) frames accepted; each
    21-pt block is scored by confident-key count."""
    n_blocks = len(points) // 21
    best = None
    for b in range(max(1, n_blocks)):
        blk = points[b * 21:(b + 1) * 21]
        keys = [p for p in blk if float(p.get("score", 0)) >= MIN_SCORE]
        if len(keys) < MIN_KEYS:
            continue
        sw = sum(float(p["score"]) for p in keys)
        cx = sum(float(p["x"]) * float(p["score"]) for p in keys) / sw
        cy = sum(float(p["y"]) * float(p["score"]) for p in keys) / sw
        # dominant hand = highest total confidence (the signing hand is
        # the confident one; a parked hand scores lower or not at all)
        if best is None or sw > best[0]:
            best = (sw, cx, cy)
    return None if best is None else (best[1], best[2])


def load_commands(path):
    rows = []
    try:
        for line in open(path):
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t", 2)
            if len(parts) == 3:
                rows.append({"gloss": parts[0], "label": parts[1],
                             "argv": parts[2], "tier": 0})
    except OSError:
        pass
    return rows


class Engine:
    """The stub pipeline: frames in, decisions out."""

    def __init__(self, config):
        self.config = config
        self.key = ""
        try:
            self.key = open(os.path.join(config, "key")).read().strip()
        except OSError:
            pass
        self.commands = load_commands(os.path.join(config, "commands.tsv"))
        bank_path = os.path.join(config, "signbank.json")
        self.bank_path = bank_path
        self.bank = (refmatch.RefBank.load(bank_path)
                     if os.path.isfile(bank_path)
                     else refmatch.RefBank())
        self.seg = []
        self.absent = 0
        self.still = 0
        self.teach_name = None
        self.seq = 0
        self.last = None
        self.log = open(os.path.join(config, "decisions.jsonl"), "a", buffering=1)
        self.lock = threading.Lock()

    def _finalize(self):
        seg, self.seg = self.seg, []
        self.still = 0
        if len(seg) < refmatch.MIN_POINTS:
            return None
        if self.teach_name:
            ok = self.bank.add(self.teach_name, list(seg), user=True)
            try:
                self.bank.save(self.bank_path)
            except OSError:
                pass
            self.teach_name = None
            self.seq += 1
            self.last = {"seq": self.seq, "gloss": f"TAUGHT:{ok}",
                         "dist": 0.0, "accepted": ok, "action": "teach",
                         "t_ms": int(time.time() * 1000)}
            self.log.write(json.dumps(self.last) + "\n")
            return self.last
        dec = self.bank.match([list(p) for p in seg])
        action = None
        if dec.accepted:
            row = next((r for r in self.commands if r["gloss"] == dec.gloss), None)
            if row:
                action = row["argv"]
                try:
                    subprocess.Popen(shlex.split(row["argv"]))
                except Exception as e:
                    action = f"spawn-failed:{e}"
        self.seq += 1
        self.last = {"seq": self.seq, "gloss": dec.gloss, "dist": dec.dist,
                     "accepted": dec.accepted, "action": action,
                     "score": dec.score,
                     "t_ms": int(time.time() * 1000)}
        self.log.write(json.dumps(self.last) + "\n")
        return self.last

    def feed(self, points):
        with self.lock:
            c = dominant_centroid(points)
            if c is None:
                self.absent += 1
                if self.absent >= ABSENT_END and self.seg:
                    return self._finalize()
                return None
            self.absent = 0
            if self.seg:
                dx, dy = c[0] - self.seg[-1][0], c[1] - self.seg[-1][1]
                if math.hypot(dx, dy) < STILL_EPS:
                    self.still += 1
                else:
                    self.still = 0
            self.seg.append(c)
            if self.still >= STILL_END or len(self.seg) >= MAX_SEG:
                return self._finalize()
            return None


def make_handler(engine):
    class H(BaseHTTPRequestHandler):
        def _reply(self, code, body=b"", ctype="text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, *a):
            pass

        def _auth(self):
            return engine.key and hmac.compare_digest(
                self.headers.get("X-WF-Key", ""), engine.key)

        def _body(self):
            n = int(self.headers.get("Content-Length", 0))
            return self.rfile.read(min(n, 8 * 1024 * 1024))

        def do_GET(self):
            p = self.path.split("?")[0]
            if p == "/health":
                return self._reply(200, b"ok")
            if not self._auth():
                return self._reply(403, b'{"ok":false}', "application/json")
            if p == "/live/state":
                out = {"listening": True, "buffer": len(engine.seg),
                       "engine": "contract-stub-python-dtw",
                       "last": engine.last}
                return self._reply(200, json.dumps(out).encode(),
                                   "application/json")
            if p == "/engine":
                out = {"engine": "contract-stub-python-dtw", "format": "kpN",
                       "bank_glosses": [t["gloss"] for t in engine.bank.templates],
                       "reject_threshold": engine.bank.reject_threshold}
                return self._reply(200, json.dumps(out).encode(),
                                   "application/json")
            if p == "/act":
                return self._reply(200, json.dumps(engine.commands).encode(),
                                   "application/json")
            return self._reply(404, b"nope")

        def do_POST(self):
            p = self.path.split("?")[0]
            if not self._auth():
                return self._reply(403, b'{"ok":false}', "application/json")
            raw = self._body()
            try:
                obj = json.loads(raw) if raw else {}
            except Exception:
                obj = {}
            if p == "/live/kp":
                engine.feed(obj.get("points", []))
                return self._reply(200, b'{"ok":true}', "application/json")
            if p == "/teach/begin":
                name = str(obj.get("name", "")).strip()
                if not name:
                    return self._reply(400, b'{"ok":false,"error":"name"}',
                                       "application/json")
                engine.teach_name = name
                return self._reply(200, b'{"ok":true}', "application/json")
            if p == "/teach/end":
                # explicit capture boundary: a buffered segment becomes
                # the exemplar now (no wait for the endpointer)
                with engine.lock:
                    if engine.teach_name and len(engine.seg) >= refmatch.MIN_POINTS:
                        engine._finalize()
                    else:
                        engine.teach_name = None
                return self._reply(200, b'{"ok":true}', "application/json")
            if p in ("/live/listen", "/live/start"):
                return self._reply(200, b'{"ok":true}', "application/json")
            return self._reply(404, b"nope")

        do_OPTIONS = lambda self: self._reply(204)
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8893)
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    engine = Engine(args.config)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(engine))
    print(f"[stubdaemon] contract double on :{args.port} "
          f"(config {args.config}; bank glosses: "
          f"{[t['gloss'] for t in engine.bank.templates]})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
