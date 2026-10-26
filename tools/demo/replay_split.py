#!/usr/bin/env python3
"""replay_split.py — :8877 kp42 replay service with per-consumer queues.

Drop-in superset of tools/demo-rec/replay_server.py. Same wire, same
endpoints, one addition: the queue is partitioned by CONSUMER ORIGIN.

Why: the composer page fetches /pull cross-origin, and Chrome sends
`Origin: http://127.0.0.1:<daemon-port>` on those GETs. On a shared box
several composers can pull the same singleton queue — clips posted for
one demo leak into another daemon's /live/kp. Partitioning by the
puller's Origin port isolates each composer:

  POST /clip  {"frames":[...]}
      -> queue chosen by `X-Replay-Route: <tag>` header or ?route=<tag>
         (the demo lane tags "8874"; untagged pushes land in "default")
  GET  /pull?n=K
      -> drains the queue named by the request's Origin port
         (a page at http://127.0.0.1:8874 drains queue "8874"; curl or a
         foreign composer drains "default")
  GET  /health  -> {"ok":true,"queued":N,"queues":{tag:n,...}}
  GET  /stats   -> same + served/clips
  POST /clear   -> clears the caller's route (or all with ?route=*)

Behaviour for unmodified existing consumers is unchanged: untagged push
-> "default" queue; a puller without a recognizable Origin drains
"default". Only pages whose Origin port matches a tagged route see that
route's frames.

Loopback only. CORS-open + Private-Network-Access, same as upstream.
"""
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8877
Q = {}                    # route -> [frames]
QL = threading.Lock()
SERVED = {}
CLIPS = {}


def _route_of(headers, query):
    """Queue name for a request: explicit tag wins, else Origin port."""
    tag = headers.get("X-Replay-Route") or query.get("route")
    if tag:
        return str(tag)
    org = headers.get("Origin") or ""
    m = urllib.parse.urlparse(org)
    if m.port:
        return str(m.port)
    return "default"


def _drain(route, n):
    with QL:
        q = Q.setdefault(route, [])
        out = q[:n]
        del q[:n]
        SERVED[route] = SERVED.get(route, 0) + len(out)
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
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        route = _route_of(self.headers, {k: v[0] for k, v in qs.items()})
        if u.path == "/health":
            with QL:
                sizes = {k: len(v) for k, v in Q.items()}
            return self._r(200, {"ok": True,
                                 "queued": sizes.get(route, 0),
                                 "queues": sizes, "split": True})
        if u.path == "/stats":
            with QL:
                sizes = {k: len(v) for k, v in Q.items()}
            return self._r(200, {"ok": True, "queued": sizes.get(route, 0),
                                 "queues": sizes,
                                 "served": SERVED.get(route, 0),
                                 "served_by_route": dict(SERVED),
                                 "clips": CLIPS.get(route, 0),
                                 "clips_by_route": dict(CLIPS)})
        if u.path == "/pull":
            try:
                n = max(1, min(int((qs.get("n") or ["8"])[0]), 64))
            except ValueError:
                n = 8
            return self._r(200, {"frames": _drain(route, n)})
        return self._r(404, {"ok": False})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            n = 0
        raw = self.rfile.read(min(n, 8 << 20)) if n else b""
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        route = _route_of(self.headers, {k: v[0] for k, v in qs.items()})
        if u.path in ("/clip", "/push"):
            try:
                d = json.loads(raw)
                frames = d.get("frames") or []
            except Exception:
                return self._r(400, {"ok": False, "error": "bad json"})
            with QL:
                q = Q.setdefault(route, [])
                for f in frames:
                    pts = f.get("points") if isinstance(f, dict) else None
                    if isinstance(pts, list) and len(pts) in (21, 42):
                        q.append({"points": pts})
                CLIPS[route] = CLIPS.get(route, 0) + 1
                qlen = len(q)
            return self._r(200, {"ok": True, "queued": qlen,
                                 "route": route})
        if u.path == "/clear":
            with QL:
                if route == "*" or qs.get("route") == ["*"]:
                    Q.clear()
                else:
                    Q[route] = []
            return self._r(200, {"ok": True, "route": route})
        return self._r(404, {"ok": False})


if __name__ == "__main__":
    print(f"[replay-split] http://127.0.0.1:{PORT} (origin-partitioned)",
          flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
