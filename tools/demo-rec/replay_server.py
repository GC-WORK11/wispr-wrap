#!/usr/bin/env python3
"""replay_server.py — loopback kp42 replay service for the dev seam.

The composer/teach pages probe http://127.0.0.1:8877/health (see
web/js/pose.js Pose.replay); when this answers, the sheet's pose engine
becomes this service — a stand-in for camera+mediapipe on displays that
have neither (Xvfb, CI). Frames queued here are the EXACT data the page
posts to the daemon as /live/kp — WYSIWYS, nothing is faked downstream.

  POST /clip   {"frames":[{t_ms?, points:[{x,y,z?,score} x42]}, ...]}
                 enqueue a clip (t_ms is ignored — the page stamps time)
  POST /push   alias of /clip
  GET  /pull?n=K   -> {"frames":[...]}   drain up to K queued frames
  GET  /health     -> {"ok":true,"queued":N}
  GET  /stats      -> {"ok":true,"queued":N,"served":N,"clips":N}
  POST /clear      drop the queue

Only loopback. CORS-open + Private-Network-Access so the page at
http://127.0.0.1:<wfd-port> can fetch it.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8877
Q = []
QL = threading.Lock()
SERVED = 0
CLIPS = 0


def _drain(n):
    global SERVED
    with QL:
        out = Q[:n]
        del Q[:n]
        SERVED += len(out)
        return out


class H(BaseHTTPRequestHandler):
    def _r(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        if b:
            self.wfile.write(b)

    def log_message(self, *a):
        pass

    def do_OPTIONS(self):
        self._r(204, {})

    def do_GET(self):
        import urllib.parse
        u = urllib.parse.urlparse(self.path)
        if u.path == "/health":
            with QL:
                n = len(Q)
            return self._r(200, {"ok": True, "queued": n})
        if u.path == "/stats":
            with QL:
                n = len(Q)
            return self._r(200, {"ok": True, "queued": n, "served": SERVED,
                                 "clips": CLIPS})
        if u.path == "/pull":
            qs = urllib.parse.parse_qs(u.query)
            try:
                n = max(1, min(int((qs.get("n") or ["8"])[0]), 64))
            except ValueError:
                n = 8
            return self._r(200, {"frames": _drain(n)})
        return self._r(404, {"ok": False})

    def do_POST(self):
        global CLIPS
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            n = 0
        raw = self.rfile.read(min(n, 8 << 20)) if n else b""
        if self.path in ("/clip", "/push"):
            try:
                d = json.loads(raw)
                frames = d.get("frames") or []
            except Exception:
                return self._r(400, {"ok": False, "error": "bad json"})
            with QL:
                for f in frames:
                    pts = f.get("points") if isinstance(f, dict) else None
                    if isinstance(pts, list) and len(pts) in (21, 42):
                        Q.append({"points": pts})
            CLIPS += 1
            return self._r(200, {"ok": True, "queued": len(Q)})
        if self.path == "/clear":
            with QL:
                Q.clear()
            return self._r(200, {"ok": True})
        return self._r(404, {"ok": False})


if __name__ == "__main__":
    print(f"[replay] http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
