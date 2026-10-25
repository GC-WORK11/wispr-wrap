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

ACT_CATALOG = {
    "ok": True,
    "actions": [
        {"type": "open_app", "label": "open app",
         "desc": "launch a program on the laptop",
         "fields": [{"k": "argv", "ph": "google-chrome"}], "tier": 2},
        {"type": "open_url", "label": "open url",
         "desc": "open a page in the laptop browser",
         "fields": [{"k": "url", "ph": "https://example.com"}], "tier": 1},
        {"type": "key_press", "label": "key press",
         "desc": "fire a key combo on the laptop",
         "fields": [{"k": "keys", "ph": "ctrl+alt+t"}], "tier": 2},
        {"type": "type_text", "label": "type text",
         "desc": "type a phrase at the laptop cursor",
         "fields": [{"k": "text", "ph": "on my way —"}], "tier": 2},
        {"type": "screenshot", "label": "screenshot",
         "desc": "save a screenshot on the laptop", "fields": [], "tier": 1},
    ],
    "apps": [
        {"name": "Chrome", "argv": "google-chrome"},
        {"name": "Terminal", "argv": "x-terminal-emulator"},
        {"name": "Files", "argv": "nautilus"},
    ],
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
            return self._json(200, ACT_CATALOG)
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
            name = str(data.get("name", ""))[:64]
            SESSIONS[name] = {"begin_ms": int(time.time() * 1000), "ended": False}
            print(f"[stub] teach/begin {name}", flush=True)
            return self._json(200, {"ok": True})
        if p == "/teach/end":
            name = str(data.get("name", ""))[:64]
            if name in SESSIONS:
                SESSIONS[name]["ended"] = True
                SESSIONS[name]["takes"] = data.get("takes")
            print(f"[stub] teach/end {name} takes={data.get('takes')}", flush=True)
            return self._json(200, {"ok": True})
        return self._reply(404, b"nope")

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

        # full create/replace — the teach wizard's payload
        action = data.get("action") or {}
        if not action.get("type"):
            return self._json(400, {"ok": False, "error": "missing action"})
        exemplars = data.get("exemplars") or []
        BANK[gloss] = {
            "label": str(data.get("label", gloss))[:64],
            "action": action, "tier": int(data.get("tier", 1)),
            "exemplars": exemplars[-5:], "created_ms": int(time.time() * 1000),
        }
        nf = sum(len(x.get("frames", [])) for x in exemplars)
        print(f"[stub] bank put {gloss} action={action.get('type')} "
              f"tier={data.get('tier')} exemplars={len(exemplars)} frames={nf}", flush=True)
        return self._json(200, {"ok": True, "exemplar_count": len(BANK[gloss]["exemplars"])})

    # ---------------- DELETE ----------------
    def do_DELETE(self):
        p = self.path.split("?")[0]
        if self.OLD:
            return self._reply(404, b"nope")
        if not self._authed():
            return self._json(403, {"ok": False, "error": "unpaired"})
        if p != "/bank":
            return self._reply(404, b"nope")
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
