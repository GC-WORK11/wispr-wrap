#!/usr/bin/env python3
"""wfd — Flow Composer daemon. Receives text/images/files from the phone app
and injects them at the laptop cursor. Auth via shared pairing key."""
import hashlib, hmac, json, os, re, secrets, string, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("WF_PORT", "8741"))
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
WEB = os.path.join(ROOT, "web")
KEY_FILE = os.path.expanduser("~/.config/wf/key")
UPLOADS = os.path.expanduser("~/wf-uploads")

TERMINAL_CLASSES = (
    "terminal", "konsole", "alacritty", "kitty", "xterm", "st", "wezterm",
    "terminator", "tilix", "yakuake", "guake", "foot", "ghostty", "warp",
)


def load_key():
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
    try:
        return open(KEY_FILE).read().strip()
    except OSError:
        key = secrets.token_hex(20)
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key)
        return key


SECRET = load_key()
# minted once per daemon run — stays valid until `wrap stop`
PAIR_CODE = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
PAIR_RATE = {}  # ip -> last /pair attempt (rate limit, not secrecy)


def all_ips():
    """All global IPv4 addrs, wifi interfaces first (a USB-tether IP is
    unreachable for a wifi-only phone)."""
    try:
        out = sh("ip", "-4", "-o", "addr", "show", "scope", "global").stdout
        wifi, other = [], []
        for line in out.splitlines():
            p = line.split()
            ifname, addr = p[1], p[3].split("/")[0]
            if ifname.startswith(("docker", "veth", "br-", "virbr", "lo")):
                continue  # host-internal nets — phone can never reach these
            (wifi if ifname.startswith(("wl", "wlan")) else other).append(addr)
        return wifi + other
    except Exception:
        return []


def lan_ip():
    ips = all_ips()
    if ips:
        return ips[0]
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=10)


def active_window_class():
    try:
        wid = sh("xdotool", "getactivewindow").stdout.strip()
        if not wid.isdigit():
            out = sh("xprop", "-root", "_NET_ACTIVE_WINDOW").stdout
            import re as _re
            m = _re.search(r"0x[0-9a-fA-F]+", out)
            wid = str(int(m.group(0), 16)) if m else ""
        if not wid:
            return ""
        wm = sh("xprop", "-id", wid, "WM_CLASS").stdout
        names = re.findall(r'"([^"]+)"', wm)
        return (names[-1] if names else "").lower()
    except Exception:
        return ""


def type_keystrokes(text):
    subprocess.Popen(["xdotool", "type", "--clearmodifiers", "--delay", "12", "--", text])


def type_paste(text):
    cls = active_window_class()
    paste_keys = "ctrl+shift+v" if any(t in cls for t in TERMINAL_CLASSES) else "ctrl+v"
    try:
        old = sh("xclip", "-selection", "clipboard", "-o").stdout
    except Exception:
        old = None

    def job():
        subprocess.run(["xclip", "-selection", "clipboard", "-i"], input=text, text=True)
        time.sleep(0.15)
        sh("xdotool", "key", "--clearmodifiers", paste_keys)
        time.sleep(0.6)
        if old is not None:
            subprocess.run(["xclip", "-selection", "clipboard", "-i"], input=old, text=True)

    threading.Thread(target=job, daemon=True).start()


def send_text(text, mode):
    if os.environ.get("WF_NO_LOG_TEXT"):
        print(f"[wfd] {mode}: <{len(text)} chars>", flush=True)
    else:
        print(f"[wfd] {mode}: {text!r}", flush=True)
    if mode == "paste":
        type_paste(text)
    else:
        type_keystrokes(text)


IMG_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}


def sniff_mime(data, ctype):
    if data[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if data[:3] == b"\xff\xd8\xff": return "image/jpeg"
    if data[:4] == b"GIF8": return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP": return "image/webp"
    return ctype.split(";")[0] if ctype in IMG_EXT else "image/png"


def paste_file(path, mime, cls):
    keys = "ctrl+shift+v" if any(t in cls for t in TERMINAL_CLASSES) else "ctrl+v"
    try:
        old = sh("xclip", "-selection", "clipboard", "-o").stdout or None
    except Exception:
        old = None

    def job():
        subprocess.Popen([sys.executable, os.path.join(BASE, "wfclip.py"), path, mime],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        time.sleep(0.3)
        sh("xdotool", "key", "--clearmodifiers", keys)
        # hand the clipboard back so the next Ctrl+V doesn't replay the file path
        time.sleep(1.2)
        subprocess.run(["xclip", "-selection", "clipboard", "-i"],
                       input=old if old is not None else "", text=True)

    threading.Thread(target=job, daemon=True).start()


def send_image(data, ctype=""):
    mime = sniff_mime(data, ctype)
    src = f"/tmp/wf-img-raw.{IMG_EXT.get(mime, 'bin')}"
    with open(src, "wb") as f:
        f.write(data)
    if mime == "image/gif":
        path = "/tmp/wf-img.gif"
    else:
        path = "/tmp/wf-img.png"
        if mime != "image/png":
            r = subprocess.run(["convert", src, path], capture_output=True, timeout=15)
            if r.returncode != 0:
                path, mime = src, "image/jpeg"
            else:
                mime = "image/png"
        else:
            path = src
    cls = active_window_class()
    paste_file(path, mime, cls)
    print(f"[wfd] image: {len(data)}B {mime} into '{cls}'", flush=True)
    return cls, mime


def send_file(data, name, ctype):
    import re
    safe = re.sub(r"[^\w.\-]+", "_", name or "file")
    os.makedirs(UPLOADS, exist_ok=True)
    path = os.path.join(UPLOADS, f"{int(time.time())}_{safe}")
    with open(path, "wb") as f:
        f.write(data)
    cls = active_window_class()
    paste_file(path, ctype or "", cls)
    print(f"[wfd] file: {len(data)}B {safe} -> {path} into '{cls}'", flush=True)
    return cls, path


def authed(headers):
    return hmac.compare_digest(headers.get("X-WF-Key", ""), SECRET)


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, body=b"", ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/health":
            return self._reply(200, b"ok")
        # localhost-only pairing info (for `wrap` CLI + USB-bridged auto-pair).
        # Proxied requests (cloudflared etc.) also arrive from 127.0.0.1 but carry
        # forwarding headers — treat them as remote or the key leaks to the tunnel.
        if p == "/pair-info":
            proxied = any(h.lower().startswith(("cf-", "x-forwarded"))
                          for h in self.headers)
            if self.client_address[0] != "127.0.0.1" or proxied:
                return self._reply(403, b"localhost only")
            out = json.dumps({"ip": lan_ip(), "ips": all_ips(), "port": PORT,
                              "key": SECRET, "code": PAIR_CODE,
                              "name": os.uname().nodename}).encode()
            return self._reply(200, out, "application/json")
        name = p.lstrip("/") or "index.html"
        path = os.path.normpath(os.path.join(WEB, name))
        if not path.startswith(WEB) or not os.path.isfile(path):
            return self._reply(404, b"nope")
        ct = "text/html; charset=utf-8" if name.endswith(".html") else \
             "application/javascript" if name.endswith(".js") else \
             "application/manifest+json" if name.endswith(".webmanifest") else \
             "image/svg+xml" if name.endswith(".svg") else "application/octet-stream"
        with open(path, "rb") as f:
            self._reply(200, f.read(), ct)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        p = self.path.split("?")[0]

        if p == "/pair":
            ip = self.client_address[0]
            now = time.time()
            if now - PAIR_RATE.get(ip, 0) < 1.0:   # 1 try/sec/IP — blunts code guessing
                return self._reply(429, b'{"ok":false}', "application/json")
            PAIR_RATE[ip] = now
            try:
                code = json.loads(raw).get("code", "").strip().upper()
            except Exception:
                code = ""
            if code and hmac.compare_digest(code, PAIR_CODE):
                out = {"ok": True, "key": SECRET, "name": os.uname().nodename}
                return self._reply(200, json.dumps(out).encode(), "application/json")
            return self._reply(403, b'{"ok":false}', "application/json")

        if not authed(self.headers):
            return self._reply(403, b'{"ok":false,"error":"unpaired"}', "application/json")

        if p == "/send-image":
            if raw:
                cls, mime = send_image(raw, self.headers.get("Content-Type", ""))
                out = json.dumps({"ok": True, "into": cls, "mime": mime,
                                  "kb": round(len(raw) / 1024)}).encode()
                return self._reply(200, out, "application/json")
            return self._reply(200, b'{"ok":false}', "application/json")
        if p == "/send-file":
            if raw:
                name = self.headers.get("X-File-Name", "file")
                cls, path = send_file(raw, name, self.headers.get("Content-Type", ""))
                out = json.dumps({"ok": True, "into": cls, "path": path,
                                  "mb": round(len(raw) / 1048576, 1)}).encode()
                return self._reply(200, out, "application/json")
            return self._reply(200, b'{"ok":false}', "application/json")
        if p == "/send":
            try:
                data = json.loads(raw)
                text, mode = data.get("text", ""), data.get("mode", "type")
            except Exception:
                text, mode = raw.decode("utf-8", "replace"), "type"
            if text:
                send_text(text, mode)
            return self._reply(200, b'{"ok":true,"into":"' +
                               active_window_class().encode() + b'"}', "application/json")
        return self._reply(404, b"nope")

    def do_OPTIONS(self):
        self._reply(204)


if __name__ == "__main__":
    print(f"[wfd] listening on http://0.0.0.0:{PORT} (key: {KEY_FILE})", flush=True)
    print(f"[wfd] pair code: {PAIR_CODE} (valid until stop)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
