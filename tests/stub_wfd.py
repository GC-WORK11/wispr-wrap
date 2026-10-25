#!/usr/bin/env python3
"""stub_wfd — test stub for the merged sign-control daemon contract.

Serves web/ statically (with correct MIME types, unlike today's wfd.py css
gap) plus the new endpoints W1b is implementing:

    GET    /health                    200 "ok"                         (open)
    POST   /teach/begin {name}        open a teach session             X-WF-Key
    POST   /teach/end   {name,takes}  close it                         X-WF-Key
    GET    /bank                      {ok, signs:[…]}                  X-WF-Key
    PUT    /bank                      {gloss,label,action,tier,exemplars}
                                      {gloss, add_exemplar:{frames}}
                                      {gloss, rename:{gloss,label}}    X-WF-Key
    DELETE /bank                      body {gloss}                     X-WF-Key
    GET    /act                       {ok, actions:[…], apps:[…]}      X-WF-Key
    GET    /selftest                  serves tests/selftest.html       (open)
    POST   /_result                   selftest result sink → RESULT_FILE

kp42 frame format matches /live/kp: {t_ms, points:[{x,y,score}×42]}.

Usage: python3 tests/stub_wfd.py [--port 8743] [--old]  (--old = pretend
the daemon predates sign control: all /bank,/teach,/act routes 404.)
"""
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB = os.path.join(ROOT, "web")
KEY = os.environ.get("WF_STUB_KEY", "teach-stub-key")
RESULT_FILE = os.environ.get("WF_STUB_RESULT", "/tmp/wf-selftest-result.json")

BANK = {}          # gloss -> entry
SESSIONS = {}      # name -> {"begin_ms", "ended"}
TEACH = {"active": False, "name": "", "takes": 0, "got": 0}
LISTENING = {"on": False, "last_match": None}
SEG = {"frames": 0, "hands": 0}   # in-flight segment emulation

ACT_CATALOG = {
    "ok": True,
    "actions": [],
    "apps": [
        {"id": "chrome", "name": "Chrome", "argv": ["google-chrome"],
         "wm_class": "Google-chrome"},
        {"id": "terminal", "name": "Terminal", "argv": ["x-terminal-emulator"],
         "wm_class": "XTerm"},
        {"id": "files", "name": "Files", "argv": ["nautilus"],
         "wm_class": "Nautilus"},
    ],
    "tools": [
        {"id": "app.open", "kind": "app", "label": "Open app",
         "tier": 1, "arg": "app"},
        {"id": "open.url", "kind": "builtin", "label": "Open URL",
         "tier": 1, "arg": "url"},
        {"id": "key.press", "kind": "builtin", "label": "Key press",
         "tier": 1, "arg": "keys"},
        {"id": "type.text", "kind": "builtin", "label": "Type text",
         "tier": 1, "arg": "text"},
        {"id": "command", "kind": "command", "label": "Terminal command",
         "tier": 2, "arg": "argv"},
    ],
    "capabilities": {"features": {"icons": False}, "provider": "stub"},
    "provider": "stub",
}

MIME = {
    ".html": "text/html; charset=utf-8", ".js": "application/javascript",
    ".css": "text/css; charset=utf-8", ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
}


def bank_signs():
    out = []
    for g, e in BANK.items():
        out.append({
            "gloss": g, "label": e.get("label", g),
            "action": e.get("action"), "tier": e.get("tier"),
            "argv": e.get("argv_str", ""),
            "bound": bool(e.get("action") or e.get("argv_str")),
            "taught": True,
            "n_exemplars": len(e.get("exemplars", [])),
            "exemplar_count": len(e.get("exemplars", [])),
            "created_ms": e.get("created_ms"),
        })
    return out


class H(BaseHTTPRequestHandler):
    OLD = "--old" in sys.argv

    def _reply(self, code, body=b"", ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code, obj):
        self._reply(code, json.dumps(obj).encode(), "application/json")

    def _authed(self):
        return hmac_safe(self.headers.get("X-WF-Key", ""), KEY)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0 or n > 8 * 1024 * 1024:
            return b""
        return self.rfile.read(n)

    def log_message(self, *a):
        pass

    # ---------------- GET ----------------
    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/health":
            return self._reply(200, b"ok")
        if p == "/probe":
            with open(os.path.join(HERE, "probe.html"), "rb") as f:
                return self._reply(200, f.read(), "text/html; charset=utf-8")
        if p == "/selftest":
            with open(os.path.join(HERE, "selftest.html"), "rb") as f:
                return self._reply(200, f.read(), "text/html; charset=utf-8")
        if p == "/bank":
            if self.OLD:
                return self._reply(404, b"nope")
            if not self._authed():
                return self._json(403, {"ok": False, "error": "unpaired"})
            return self._json(200, {"ok": True, "signs": bank_signs()})
        if p == "/act":
            if self.OLD:
                return self._reply(404, b"nope")
            if not self._authed():
                return self._json(403, {"ok": False, "error": "unpaired"})
            out = dict(ACT_CATALOG)
            out["actions"] = [{"gloss": g, "label": e.get("label", g),
                               "argv": e.get("argv_str", ""),
                               "args": e.get("action", {}).get("args", {}),
                               "tier": e.get("tier"), "confirm": (e.get("tier") or 0) >= 2,
                               "taught": True, "via": "stub"}
                              for g, e in BANK.items()]
            return self._json(200, out)
        if p == "/engine":
            if self.OLD:
                return self._reply(404, b"nope")
            if not self._authed():
                return self._json(403, {"ok": False, "error": "unpaired"})
            return self._json(200, {"engine": "stub-dtw", "bank_size": len(BANK),
                                    "listening": LISTENING["on"],
                                    "signbank": True})
        if p == "/live/state":
            if self.OLD:
                return self._reply(404, b"nope")
            if not self._authed():
                return self._json(403, {"ok": False, "error": "unpaired"})
            return self._json(200, {
                "listening": LISTENING["on"],
                "session": "stub",
                "last_match": LISTENING["last_match"],
                "segment_len": SEG["frames"],
                "signing": SEG["hands"] > 0,
                "teach": dict(TEACH),
                "pending": [],
                "focus": "stub-wm",
                "focus_title": "stub window",
                "bank_size": len(BANK)})
        # static
        name = p.lstrip("/") or "index.html"
        path = os.path.normpath(os.path.join(WEB, name))
        if not path.startswith(WEB) or not os.path.isfile(path):
            return self._reply(404, b"nope")
        ct = MIME.get(os.path.splitext(name)[1], "application/octet-stream")
        with open(path, "rb") as f:
            self._reply(200, f.read(), ct)

    # ---------------- POST ----------------
    def do_POST(self):
        p = self.path.split("?")[0]
        raw = self._body()
        if p == "/_result":
            with open(RESULT_FILE, "wb") as f:
                f.write(raw)
            print("[stub] result:", raw[:400].decode("utf-8", "replace"), flush=True)
            return self._json(200, {"ok": True})
        if self.OLD and p in ("/teach/begin", "/teach/end"):
            return self._reply(404, b"nope")
        if not self._authed():
            return self._json(403, {"ok": False, "error": "unpaired"})
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            return self._json(400, {"ok": False, "error": "bad json"})
        if p == "/teach/begin":
            name = str(data.get("name", ""))[:64].upper()
            SESSIONS[name] = {"begin_ms": int(time.time() * 1000), "ended": False}
            TEACH.update(active=True, name=name,
                         takes=max(1, min(int(data.get("takes", 3)), 5)),
                         got=0)
            print(f"[stub] teach/begin {name}", flush=True)
            return self._json(200, {"ok": True, "name": name,
                                    "takes": TEACH["takes"]})
        if p == "/teach/end":
            got, name = TEACH["got"], TEACH["name"]
            TEACH.update(active=False, name="", takes=0, got=0)
            if name in SESSIONS:
                SESSIONS[name]["ended"] = True
                SESSIONS[name]["takes"] = data.get("takes", got)
            print(f"[stub] teach/end {name} takes={got}", flush=True)
            return self._json(200, {"ok": True, "name": name,
                                    "exemplars": got, "done": got > 0})
        if p == "/live/listen":
            LISTENING["on"] = bool(data.get("on", True))
            return self._json(200, {"ok": True, "listening": LISTENING["on"]})
        if p == "/live/event":
            typ = str(data.get("type", ""))
            if typ in ("palm_open", "listen", "on", "start"):
                LISTENING["on"] = True
            elif typ in ("namaste", "stop", "off", "palm_closed"):
                LISTENING["on"] = False
            return self._json(200, {"ok": True, "listening": LISTENING["on"]})
        if p == "/live/kp":
            return self._live_kp(data)
        if p == "/act/confirm":
            return self._json(200, {"ok": True, "fired": True,
                                    "gloss": str(data.get("id", ""))})
        return self._reply(404, b"nope")

    # -------- /live/kp: presence-edge endpointer emulation --------
    def _live_kp(self, data):
        pts = data.get("points")
        if not isinstance(pts, list) or len(pts) not in (21, 42):
            return self._json(400, {"ok": False,
                                    "error": "points must be 21 or 42"})
        n = 0
        for p in pts:
            try:
                s = p.get("score", 1.0) if isinstance(p, dict) else \
                    (p[2] if len(p) > 2 else 1.0)
                if s and float(s) > 0.2:
                    n += 1
            except (TypeError, ValueError, IndexError):
                pass
        hands = n >= 4
        out = {"ok": True, "session": "stub"}
        if hands:
            SEG["frames"] += 1
            SEG["hands"] += 1
            SEG.setdefault("pts", []).append(
                {"t_ms": data.get("t_ms", 0), "points": pts})
            SEG["pts"] = SEG["pts"][-300:]
        else:
            SEG["hands"] = 0
            if SEG["frames"] >= 3:
                frames = SEG.pop("pts", [])
                SEG["frames"] = 0
                if TEACH["active"]:
                    # the real daemon's signbank stores the segment as an
                    # exemplar here — emulate that, capped at 5 like it is
                    g = TEACH["name"]
                    e = BANK.setdefault(g, {"label": g, "exemplars": [],
                        "created_ms": int(time.time() * 1000)})
                    e["exemplars"].append({"frames": frames})
                    e["exemplars"] = e["exemplars"][-5:]
                    TEACH["got"] += 1
                    t = {"name": g, "got": TEACH["got"],
                         "takes": TEACH["takes"], "ok": True}
                    if TEACH["got"] >= TEACH["takes"]:
                        TEACH["active"] = False
                        t["done"] = True
                    out["teach"] = t
                elif LISTENING["on"] and BANK:
                    SEG.pop("pts", None)
                    g = sorted(BANK)[0]
                    e = BANK[g]
                    m = {"gloss": g, "label": e.get("label", g),
                         "dist": 0.12, "accepted": True,
                         "at_ms": data.get("t_ms", 0),
                         "reason": "stub segment match",
                         "action": {"fired": True, "routed": True,
                                    "label": e.get("label", g)}}
                    LISTENING["last_match"] = m
                    out["match"] = m
            elif SEG["frames"]:
                SEG.pop("pts", None)
                out["dropped"] = "too-short"
                SEG["frames"] = 0
        if TEACH["active"] and "teach" not in out:
            out["teach"] = {"name": TEACH["name"], "got": TEACH["got"],
                            "takes": TEACH["takes"], "ok": True}
        return self._json(200, out)

    # ---------------- PUT ----------------
    def do_PUT(self):
        p = self.path.split("?")[0]
        if self.OLD:
            return self._reply(404, b"nope")
        if not self._authed():
            return self._json(403, {"ok": False, "error": "unpaired"})
        raw = self._body()
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            return self._json(400, {"ok": False, "error": "bad json"})
        if p != "/bank":
            return self._reply(404, b"nope")
        gloss = str(data.get("gloss", "")).strip().upper()
        if not gloss:
            return self._json(400, {"ok": False, "error": "missing gloss"})

        if "add_exemplar" in data:
            e = BANK.get(gloss)
            if not e:
                return self._json(404, {"ok": False, "error": "unknown gloss"})
            frames = (data["add_exemplar"] or {}).get("frames", [])
            if not frames:
                return self._json(400, {"ok": False, "error": "empty exemplar"})
            e["exemplars"].append({"frames": frames})
            e["exemplars"] = e["exemplars"][-5:]          # ≤5 FIFO, bank spec
            print(f"[stub] bank +exemplar {gloss} ({len(frames)} frames)", flush=True)
            return self._json(200, {"ok": True, "exemplar_count": len(e["exemplars"])})

        if "rename" in data:
            e = BANK.get(gloss)
            if not e:
                return self._json(404, {"ok": False, "error": "unknown gloss"})
            rn = data["rename"] or {}
            e["label"] = str(rn.get("label", e["label"]))[:64]
            newg = str(rn.get("gloss", gloss)).strip().upper()
            if newg and newg != gloss:
                if newg in BANK:
                    return self._json(409, {"ok": False, "error": "gloss exists"})
                BANK[newg] = BANK.pop(gloss)
            print(f"[stub] bank rename {gloss} -> {rn}", flush=True)
            return self._json(200, {"ok": True})

        # annotation-only path: {gloss, label} on an existing row = rename
        if data.get("action") is None and data.get("argv") is None and \
                gloss in BANK and data.get("label"):
            BANK[gloss]["label"] = str(data["label"])[:64]
            print(f"[stub] bank label {gloss}", flush=True)
            row = [r for r in bank_signs() if r["gloss"].upper() == gloss]
            return self._json(200, {"ok": True,
                                    "sign": row[0] if row else {}})

        # full create/replace — the teach wizard's payload. Modern shape:
        # action dict {kind,tool,type,args,tier,label,argv?}; argv list/str
        # kept for the commands.tsv fast path.
        action = data.get("action")
        argv = data.get("argv")
        if action is not None and not isinstance(action, dict):
            if isinstance(action, str):
                argv = argv or action
                action = None
            else:
                return self._json(400, {"ok": False, "error": "bad action"})
        if argv is not None and not isinstance(argv, (str, list)):
            return self._json(400, {"ok": False, "error": "bad argv"})
        if action is None and argv is None and "label" not in data:
            return self._json(400, {"ok": False,
                                    "error": "need action, argv or label"})
        if isinstance(argv, str):
            argv = argv.split()
        argv_str = " ".join(str(x) for x in argv) if argv else ""
        exemplars = data.get("exemplars") or []
        e = BANK.get(gloss) or {"exemplars": [],
                                "created_ms": int(time.time() * 1000)}
        e["label"] = str(data.get("label", gloss))[:64]
        if action is not None:
            e["action"] = action
        if argv:
            e["argv_str"] = argv_str
        e["tier"] = int(data.get("tier", e.get("tier", 1)))
        BANK[gloss] = e
        nf = sum(len(x.get("frames", [])) for x in e["exemplars"])
        print(f"[stub] bank put {gloss} action={(action or {}).get('type')} "
              f"argv={argv_str!r} tier={e['tier']} exemplars={nf}", flush=True)
        return self._json(200, {"ok": True,
                                "exemplar_count": len(e["exemplars"]),
                                "sign": [r for r in bank_signs()
                                         if r["gloss"].upper() == gloss][0]})

    # ---------------- DELETE ----------------
    def do_DELETE(self):
        p = self.path.split("?")[0]
        if self.OLD:
            return self._reply(404, b"nope")
        if not self._authed():
            return self._json(403, {"ok": False, "error": "unpaired"})
        if p != "/bank":
            return self._reply(404, b"nope")
        # real daemon accepts ?gloss= OR a {gloss} body — honor both
        import urllib.parse
        q = urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query)
        gloss = (q.get("gloss") or [""])[0].strip().upper()
        if not gloss:
            raw = self._body()
            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                data = {}
            gloss = str(data.get("gloss", "")).strip().upper()
        if gloss in BANK:
            del BANK[gloss]
            print(f"[stub] bank del {gloss}", flush=True)
            return self._json(200, {"ok": True})
        return self._json(404, {"ok": False, "error": "unknown gloss"})


def hmac_safe(a, b):
    import hmac
    return hmac.compare_digest(a.encode(), b.encode())


if __name__ == "__main__":
    port = 8743
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    print(f"[stub] on :{port} web={WEB} key={KEY} old={'--old' in sys.argv}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
