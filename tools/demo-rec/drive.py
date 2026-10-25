#!/usr/bin/env python3
"""drive.py — drive the recorded demo end to end.

Chapters (each marked for cut.sh):
  00 scene    everything up, title card on screen
  01 pair     pair.html: type address + real pair code -> composer
  02 type     type in the composer, lands in the real editor sink
  03 attach   a real file lands in ~/wf-uploads; a real photo goes to
              the laptop clipboard and its path pastes into the sink
  04 live     LIVE sheet, replayed kp42, palm->listening, sign OPEN-TERM
              -> xfce4-terminal really opens
  05 teach    teach "take a shot" (z-shape) through the wizard, bind the
              screenshot tool via the @ picker, sign it -> PNG lands
  06 context  same sign (NEXT): editor focus -> typed marker, chrome
              focus -> ctrl+Tab switches the visible tab
  07 macro    STANDUP sign -> macro opens a terminal and types into it
  08 media    VOL-UP -> the box has no pactl/amixer -> honest refusal in
              /live/state (shown in the wf-proof window)
  09 observe  an untrained squiggle -> rejected, reason on the strip
  10 bank     library sheet lists every taught sign + its binding

Driving is two-layered and honest about it:
  * xdotool moves/clicks/type for everything where laptop focus may move
    (pair form, window focus, tabs) — real input events.
  * CDP Runtime.evaluate clicks (el.click()) for buttons whose REAL side
    effect is an HTTP call the phone makes — on a real phone the tap
    never moves laptop focus; dispatching via CDP reproduces that. The
    DOM event path is identical: real handlers, real fetch, real POST.
  * CDP DOM.setFileInputFiles for the attach input — a real file walks
    the page's real change->stage->send path into a real POST /send-file.

Keypoints arrive via the replay seam (Pose.replay -> 127.0.0.1:8877) —
the page posts them as real /live/kp; the skeleton overlay draws the
SAME points that get scored.
"""
import base64
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.request

DEMO = "/tmp/demo"
OUT = os.path.join(DEMO, "out")
CLIPS = os.path.join(DEMO, "clips")
ASSETS = os.path.join(DEMO, "assets")
LOGS = os.path.join(DEMO, "logs")
DEMOHOME = os.path.join(DEMO, "home")
UPLOADS = os.path.join(DEMOHOME, "wf-uploads")
PICTURES = os.path.join(DEMOHOME, "Pictures")
SINK = os.path.join(OUT, "sink.log")

# scene.sh writes the display + ports it actually claimed — siblings on
# this box own :97-:111 and the stock CDP port, so nothing is hardcoded.
_env = {}
for _l in open(os.path.join(DEMO, "env")):
    if "=" in _l:
        _k, _v = _l.strip().split("=", 1)
        _env[_k] = _v
WFD = "http://127.0.0.1:" + _env.get("WFD_PORT", "8742")
REPLAY = "http://127.0.0.1:" + _env.get("REPLAY_PORT", "8877")
CDP = "http://127.0.0.1:" + _env.get("CDP_PORT", "9224")
DISPLAY = _env.get("DEMO_DISPLAY", ":116")
KEY = open(os.path.join(DEMOHOME, ".config", "wf", "key")).read().strip()

os.makedirs(OUT, exist_ok=True)
MARK = os.path.join(DEMO, "markers.log")
CHAP = os.path.join(DEMO, "chapters.tsv")
REC0 = float(open(os.path.join(DEMO, "rec-t0")).read())


# ---------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------
def log(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    with open(MARK, "a") as f:
        f.write("[%6.2f] %s\n" % (time.time() - REC0, line))


def mark(ch, note=""):
    with open(CHAP, "a") as f:
        f.write("%s\t%.3f\t%s\n" % (ch, time.time() - REC0, note))
    log("── %s %s" % (ch, note))


def run(*a, **kw):
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("timeout", 15)
    env = dict(os.environ, DISPLAY=DISPLAY)
    return subprocess.run([str(x) for x in a], env=env, **kw)


def xdo(*a):
    return run("xdotool", *a)


def http(path, body=None, key=KEY, base=WFD, method=None):
    url = base + path
    data = None
    h = {"X-WF-Key": key}
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h,
                                 method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def push_clip(name):
    p = os.path.join(CLIPS, name + ".json")
    fr = json.load(open(p))["frames"]
    s, j = http("/clip", {"frames": fr}, key="", base=REPLAY)
    log("replay:", name, len(fr), "frames ->", j.get("queued"))
    return s == 200


def live_state():
    s, j = http("/live/state")
    return j if s == 200 else {}


def wait_state(pred, timeout=12, what="state"):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = live_state()
        if pred(j):
            return j
        time.sleep(0.18)
    raise TimeoutError("wait_state(%s) timed out" % what)


def focus(title):
    """Focus a window whose title matches; retry — WMs are async."""
    t0 = time.time()
    while time.time() - t0 < 8:
        r = xdo("search", "--name", title)
        wids = [w for w in (r.stdout or "").split() if w.strip()]
        if wids:
            xdo("windowactivate", wids[-1])
            return wids[-1]
        time.sleep(0.4)
    return None


def place(title, x, y, w, h):
    run("wmctrl", "-r", title, "-e", "0,%d,%d,%d,%d" % (x, y, w, h))


def win_exists(title):
    r = xdo("search", "--name", title)
    return bool((r.stdout or "").strip())


def wait_win(title, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if win_exists(title):
            return True
        time.sleep(0.25)
    return False


def sink_text():
    try:
        return open(SINK).read()
    except OSError:
        return ""


# ---------------------------------------------------------------------
# minimal CDP client (stdlib websocket)
# ---------------------------------------------------------------------
class WS:
    def __init__(self, url):
        m = re.match(r"ws://([^:/]+):(\d+)(/.*)", url)
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((host, port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key))
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.sock.recv(4096)
        assert b"101" in resp.split(b"\r\n")[0], resp[:200]
        self._id = 0
        self.buf = b""

    def _send(self, payload: bytes):
        n = len(payload)
        if n < 126:
            hdr = struct.pack("!BB", 0x81, 0x80 | n)
        elif n < 65536:
            hdr = struct.pack("!BBH", 0x81, 0x80 | 126, n)
        else:
            hdr = struct.pack("!BBQ", 0x81, 0x80 | 127, n)
        mask = os.urandom(4)
        self.sock.sendall(hdr + mask +
                          bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _recv(self):
        while True:
            hdr = self._need(2)
            b0, b1 = hdr[0], hdr[1]
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._need(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._need(8))[0]
            data = self._need(n)
            op = b0 & 0x0F
            if op == 1:
                return json.loads(data)
            if op == 9:
                self._send_pong(data)
            # ping/continuation/etc: keep reading

    def _send_pong(self, data):
        n = len(data)
        hdr = struct.pack("!BB", 0x8A, 0x80 | n) if n < 126 else \
            struct.pack("!BBH", 0x8A, 0x80 | 126, n)
        mask = os.urandom(4)
        self.sock.sendall(hdr + mask +
                          bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise ConnectionError("ws closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def cmd(self, method, params=None):
        self._id += 1
        self._send(json.dumps({"id": self._id, "method": method,
                               "params": params or {}}).encode())
        t0 = time.time()
        while time.time() - t0 < 20:
            m = self._recv()
            if m.get("id") == self._id:
                if "error" in m:
                    raise RuntimeError(str(m["error"]))
                return m.get("result", {})
        raise TimeoutError(method)


def cdp_connect():
    with urllib.request.urlopen(CDP + "/json", timeout=10) as r:
        targets = json.loads(r.read())
    host = WFD.split("://", 1)[1]
    page = next(t for t in targets if t.get("type") == "page" and
                host in (t.get("url") or ""))
    ws = WS(page["webSocketDebuggerUrl"])
    ws.cmd("Runtime.enable")
    return ws


def js(ws, expr, timeout=20):
    r = ws.cmd("Runtime.evaluate",
               {"expression": expr, "returnByValue": True,
                "awaitPromise": True})
    res = r.get("result", {})
    if r.get("exceptionDetails"):
        raise RuntimeError(json.dumps(r["exceptionDetails"])[:300])
    return res.get("value")


def click_el(ws, sel, extra=""):
    """Real click on a DOM element, dispatched in-page (no focus steal —
    like a phone tap). Returns element center in page coords."""
    expr = """(() => { const el = document.querySelector(%s)%s;
      if (!el) return null;
      el.scrollIntoView({block:'center'});
      const r = el.getBoundingClientRect();
      el.click();
      return {x:r.x+r.width/2, y:r.y+r.height/2}; })()""" % (
          json.dumps(sel), extra)
    return js(ws, expr)


def wait_js(ws, expr, timeout=12, what=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if js(ws, expr):
                return True
        except Exception:
            pass
        time.sleep(0.25)
    raise TimeoutError("wait_js %s: %s" % (what, expr[:80]))


def type_into(ws, sel, text, delay=45):
    """Clear + focus an input then type real keystrokes into the page
    (focus is already on the browser window — it's the pairing form)."""
    js(ws, """(() => { const el = document.querySelector(%s);
        if (!el) return false;
        el.value = ''; el.dispatchEvent(new Event('input',{bubbles:true}));
        el.focus(); return true; })()""" % json.dumps(sel))
    time.sleep(0.25)
    xdo("type", "--delay", str(delay), text)
    time.sleep(0.2)


def set_files(ws, sel, paths):
    """DOM.setFileInputFiles — a real file walks the page's real
    change -> stage -> send path. Identical to a phone file picker."""
    ws.cmd("DOM.enable")
    doc = ws.cmd("DOM.getDocument", {"depth": 1})
    nid = ws.cmd("DOM.querySelector",
                 {"nodeId": doc["root"]["nodeId"], "selector": sel})
    nid = nid["nodeId"]
    ws.cmd("DOM.setFileInputFiles", {"nodeId": nid, "files": paths})
    return nid


# ---------------------------------------------------------------------
# chapters
# ---------------------------------------------------------------------
def ch00_scene(ws):
    mark("ch00-scene")
    wait_js(ws, "!!document.body", 15, "page up")
    time.sleep(2.5)


def ch01_pair(ws):
    mark("ch01-pair", "pair page -> paired composer")
    wait_js(ws, "!!document.getElementById('code')", 15, "pair form")
    code = ""
    for line in open(os.path.join(LOGS, "wfd.log")):
        m = re.search(r"pair code: ([A-Z0-9]+)", line)
        if m:
            code = m.group(1)
    assert code, "no pair code in daemon log"
    log("pair code:", code)
    focus("pair") or focus("wispr")
    time.sleep(0.6)
    type_into(ws, "#addr", "127.0.0.1:8742")
    type_into(ws, "#code", code)
    js(ws, "document.getElementById('go').click()")
    wait_js(ws, "location.pathname === '/' || location.pathname === '/index.html'",
            15, "redirected to composer")
    wait_js(ws, "!!document.getElementById('box')", 10, "composer mounted")
    time.sleep(2.0)
    log("paired — composer up")


def ch02_type(ws):
    mark("ch02-type", "type on phone -> lands in editor")
    assert os.path.exists(SINK), "sink.log missing — sink.py never ran"
    editor = focus("wf-editor")
    assert editor, "editor window"
    text = "hello laptop — typed from the phone"
    js(ws, """(() => { const b=document.getElementById('box');
        b.value = %s; b.dispatchEvent(new Event('input',{bubbles:true}));
        return b.value; })()""" % json.dumps(text))
    time.sleep(0.5)
    click_el(ws, "#go")
    t0 = time.time()
    while time.time() - t0 < 12:
        if text.split("—")[0].strip() in sink_text():
            log("sink got the line")
            time.sleep(1.5)
            return
        time.sleep(0.3)
    raise AssertionError("text never landed in sink")


def ch03_attach(ws):
    """Attach menu -> real file input -> POST /send-file ->
    ~/wf-uploads/<ts>_notes.txt (visible in wf-proof) + the path pasted
    into the focused editor. Then a real photo -> /send-image ->
    clipboard -> its path pastes into the same editor."""
    mark("ch03-attach", "file lands in wf-uploads; photo -> clipboard+paste")
    assert focus("wf-editor"), "editor window for paste target"
    time.sleep(0.6)

    # --- a real file: notes.txt ---------------------------------------
    click_el(ws, "#plus")
    wait_js(ws, "!!document.querySelector('.attach-menu')", 8, "attach menu")
    time.sleep(0.6)
    # 'any file' -> hidden #fileany input — feed it a real file via CDP
    found = js(ws, """(() => {
      const rows=[...document.querySelectorAll('.attach-item')];
      const b=rows.find(r=>/any file/i.test(r.textContent));
      if(!b) return null; b.click(); return b.textContent.trim(); })()""")
    assert found, "no 'any file' item in attach menu"
    time.sleep(0.5)
    set_files(ws, "#fileany", [os.path.join(ASSETS, "notes.txt")])
    wait_js(ws, "!document.getElementById('attach').hidden", 8,
            "attach chip shown")
    log("staged:", js(ws, "document.getElementById('aname').textContent"))
    time.sleep(1.2)
    before = set(os.listdir(UPLOADS)) if os.path.isdir(UPLOADS) else set()
    click_el(ws, "#go")
    t0 = time.time()
    landed = None
    while time.time() - t0 < 12:
        new = (set(os.listdir(UPLOADS)) if os.path.isdir(UPLOADS)
               else set()) - before
        if new:
            landed = sorted(new)[0]
            break
        time.sleep(0.4)
    assert landed, "no file landed in ~/wf-uploads"
    log("file landed:", landed)
    wait_js(ws, "document.getElementById('attach').hidden", 8, "chip cleared")
    t0 = time.time()
    while time.time() - t0 < 8 and landed.split("_", 1)[-1] not in sink_text():
        time.sleep(0.3)
    log("path pasted into editor:", "wf-uploads" in sink_text() or landed in sink_text())
    time.sleep(1.5)

    # --- a real photo: sunset.png --------------------------------------
    click_el(ws, "#plus")
    wait_js(ws, "!!document.querySelector('.attach-menu')", 8, "attach menu 2")
    time.sleep(0.5)
    found = js(ws, """(() => {
      const rows=[...document.querySelectorAll('.attach-item')];
      const b=rows.find(r=>/photo or video/i.test(r.textContent));
      if(!b) return null; b.click(); return b.textContent.trim(); })()""")
    assert found, "no 'photo or video' item in attach menu"
    time.sleep(0.5)
    set_files(ws, "#file", [os.path.join(ASSETS, "sunset.png")])
    wait_js(ws, "!document.getElementById('attach').hidden", 8,
            "image chip shown")
    log("staged image:", js(ws, "document.getElementById('aname').textContent"))
    time.sleep(1.2)
    s0 = len(sink_text())
    click_el(ws, "#go")
    t0 = time.time()
    ok = False
    while time.time() - t0 < 12:
        st = live_state()
        s, st2 = http("/status")
        ls = (st2 or {}).get("last_send") or {}
        if ls.get("kind") == "image" and ls.get("ok"):
            ok = True
            break
        time.sleep(0.4)
    assert ok, "image send never confirmed by /status last_send"
    log("image sent — clipboard owns it")
    t0 = time.time()
    while time.time() - t0 < 8 and "wf-img" not in sink_text()[s0:]:
        time.sleep(0.3)
    log("image path pasted into editor:", "wf-img" in sink_text())
    time.sleep(1.0)


def _palm_sign_then(ws, sign_clip, gloss, timeout=16):
    """Push palm (listen on), then the sign; return the FRESH match —
    staleness is guarded by at_ms advancing past the pre-sign row."""
    prev = (live_state().get("last_match") or {}).get("at_ms") or 0
    push_clip("palm")
    wait_state(lambda j: j.get("listening"), 8, "listening")
    log("listening — signing", gloss)
    push_clip(sign_clip)
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = live_state()
        m = j.get("last_match") or {}
        if m.get("gloss") and not m.get("mode") and \
           (m.get("at_ms") or 0) > prev:
            return m
        time.sleep(0.2)
    raise TimeoutError("no fresh match for " + gloss)


def ch04_live(ws):
    mark("ch04-live", "live sheet + replay + OPEN-TERM fires a terminal")
    click_el(ws, "#tray-live")
    wait_js(ws, "!!document.querySelector('.lv-replay')", 15,
            "replay tag visible")
    time.sleep(1.0)
    m = _palm_sign_then(ws, "swipe-r-perform", "OPEN-TERM")
    log("match:", json.dumps(m)[:200])
    assert m.get("gloss") == "OPEN-TERM" and m.get("accepted"), m
    assert wait_win("wf-fired", 8), "fired terminal never appeared"
    place("wf-fired", 520, 220, 640, 420)   # center-stage, clearly new
    log("wf-fired window present — placed center")
    with open(os.path.join(OUT, "ch04.txt"), "w") as f:
        f.write("match: %s\n" % json.dumps(m))
        f.write(run("wmctrl", "-l").stdout)
    time.sleep(2.5)
    push_clip("namaste")
    wait_state(lambda j: not j.get("listening"), 8, "stopped")
    log("namaste — stopped")
    time.sleep(0.8)


def ch05_teach(ws):
    mark("ch05-teach", "teach a sign -> screenshot binding -> fires")
    click_el(ws, "#tray-teach")
    wait_js(ws, "!!document.getElementById('tw-name')", 12, "teach sheet")
    time.sleep(0.8)
    js(ws, """(() => { const i=document.getElementById('tw-name');
        i.value='take a shot';
        i.dispatchEvent(new Event('input',{bubbles:true})); })()""")
    time.sleep(0.4)
    click_el(ws, "#tw-name-next")
    wait_js(ws, "!!document.querySelector('.tw-step[data-step=record].on')",
            10, "record step")
    time.sleep(1.5)                       # capture pump + engine up
    for i in (1, 2, 3):
        prev_got = int((live_state().get("teach") or {}).get("got") or 0)
        push_clip("z-shape-take%d" % i)
        log("take", i, "pushed")
        t0 = time.time()
        while time.time() - t0 < 15:
            j = live_state()
            t = j.get("teach") or {}
            if int(t.get("got") or 0) >= prev_got + 1:
                log("take %d captured (got=%s)" % (i, t.get("got")))
                break
            time.sleep(0.25)
        else:
            raise AssertionError("take %d not captured" % i)
        time.sleep(1.2)                   # hands fully away between takes
    # 3rd take auto-advances to bind (recDone also goes there if clicked)
    wait_js(ws, "!!document.querySelector('.tw-step[data-step=bind].on')",
            10, "bind step")
    time.sleep(0.8)
    click_el(ws, "#tw-pick")
    wait_js(ws, "!document.getElementById('sheet-actions').hidden", 8,
            "picker open")
    time.sleep(1.0)
    found = js(ws, """(() => {
      const rows = [...document.querySelectorAll('#act-list .act-row')];
      const r = rows.find(r => /screenshot/i.test(r.textContent));
      if (!r) return null; r.click(); return r.textContent.trim(); })()""")
    log("picker row:", found)
    assert found, "no screenshot row in picker"
    time.sleep(0.6)
    wait_js(ws, "!document.getElementById('tw-save').disabled", 8,
            "save enabled")
    click_el(ws, "#tw-save")
    wait_js(ws, "!!document.querySelector('.tw-step[data-step=done].on')",
            8, "done step")
    log("TAKE-A-SHOT bound to sys.screenshot")
    time.sleep(1.0)
    click_el(ws, "#tw-done-live")
    wait_js(ws, "!document.getElementById('sheet-live').hidden", 8)
    time.sleep(1.0)
    focus("wf-editor")                    # harmless focus for the shot
    pics_before = set(os.listdir(PICTURES))
    m = _palm_sign_then(ws, "z-shape-perform", "TAKE-A-SHOT")
    log("match:", json.dumps(m)[:200])
    assert m.get("gloss") == "TAKE-A-SHOT" and m.get("accepted"), m
    t0 = time.time()
    shot = None
    while time.time() - t0 < 12:
        new = set(os.listdir(PICTURES)) - pics_before
        if new:
            shot = sorted(new)[0]
            log("screenshot landed:", shot)
            break
        time.sleep(0.4)
    assert shot, "no screenshot file landed in ~/Pictures"
    push_clip("namaste")
    time.sleep(1.2)


def ch06_context(ws):
    mark("ch06-context", "same sign, different apps -> different effects")
    if win_exists("sheet-live") or True:
        js(ws, "App && App.nav ? App.nav('live') : "
               "document.getElementById('tray-live').click()")
    wait_js(ws, "!document.getElementById('sheet-live').hidden", 8)
    # branch 1: editor focused -> type marker
    focus("wf-editor")
    time.sleep(0.8)
    m = _palm_sign_then(ws, "swipe-l-perform", "NEXT")
    log("editor branch:", json.dumps(m)[:200])
    assert m.get("accepted"), m
    t0 = time.time()
    while time.time() - t0 < 10:
        if "context dispatch" in sink_text():
            log("marker typed into editor")
            break
        time.sleep(0.3)
    else:
        raise AssertionError("context marker never typed")
    # branch 2: chrome tabs focused -> ctrl+Tab flips the visible tab
    tabs = focus("demo tab")
    if not tabs:
        log("WARN: tabs window not found — context chrome branch skipped")
        return
    time.sleep(0.8)
    title0 = xdo("getactivewindow", "getwindowname").stdout.strip()
    m = _palm_sign_then(ws, "swipe-l-perform", "NEXT")
    log("chrome branch:", json.dumps(m)[:200])
    t0 = time.time()
    title1 = title0
    while time.time() - t0 < 8:
        time.sleep(0.4)
        title1 = xdo("getactivewindow", "getwindowname").stdout.strip()
        if title1 != title0:
            break
    log("tab title before/after:", title0, "/", title1)
    with open(os.path.join(OUT, "ch06.txt"), "w") as f:
        f.write("editor branch ok\nchrome branch: %s\n"
                "title %s -> %s\n" % (json.dumps(m), title0, title1))
    push_clip("namaste")
    time.sleep(1.0)


def ch07_macro(ws):
    mark("ch07-macro", "one sign -> opens terminal + types")
    focus("wf-editor")
    time.sleep(0.6)
    m = _palm_sign_then(ws, "v-check-perform", "STANDUP")
    log("match:", json.dumps(m)[:200])
    assert m.get("accepted"), m
    assert wait_win("Terminal", 12) or wait_win("xfce4", 12), \
        "macro terminal never opened"
    place("Terminal", 980, 240, 700, 400)
    log("macro terminal opened")
    with open(os.path.join(OUT, "ch07.txt"), "w") as f:
        f.write(run("wmctrl", "-l").stdout)
    time.sleep(2.5)                       # let the typed line land visibly
    push_clip("namaste")
    time.sleep(1.0)


def ch08_media(ws):
    mark("ch08-media", "volume-up sign -> honest refusal (no pactl)")
    focus("wf-editor")
    time.sleep(0.6)
    m = _palm_sign_then(ws, "push-up-perform", "VOL-UP")
    log("match:", json.dumps(m)[:220])
    assert m.get("gloss") == "VOL-UP" and m.get("accepted"), m
    # with the OPS composer active the refused dispatch lands in
    # last_action (feed + wf-proof window), not last_match.action
    t0 = time.time()
    refused = None
    while time.time() - t0 < 10:
        la = (live_state().get("last_action") or {})
        res = la.get("result") or {}
        if "VOL-UP" in json.dumps(la.get("glosses") or []) and \
           (la.get("error") or res.get("refused")):
            refused = la.get("error") or res.get("refused")
            break
        time.sleep(0.3)
    log("refusal:", refused)
    with open(os.path.join(OUT, "ch08.txt"), "w") as f:
        f.write("match: %s\nrefusal: %s\n" % (json.dumps(m), refused))
    assert refused, "expected honest refusal for VOL-UP (no pactl/amixer)"
    time.sleep(2.0)
    push_clip("namaste")
    time.sleep(0.8)


def ch09_observe(ws):
    mark("ch09-observe", "untrained sign -> rejected, reason shown")
    prev = (live_state().get("last_match") or {}).get("at_ms") or 0
    push_clip("palm")
    wait_state(lambda j: j.get("listening"), 8, "listening")
    push_clip("unknown")
    m = wait_state(lambda j: (j.get("last_match") or {}).get("accepted")
                   is False and
                   ((j.get("last_match") or {}).get("at_ms") or 0) > prev and
                   (j.get("last_match") or {}).get("reason")
                   not in (None, ""), 12, "rejection")
    log("reject row:", json.dumps(m.get("last_match"))[:220])
    time.sleep(2.5)
    push_clip("namaste")
    time.sleep(0.8)


def ch10_bank(ws):
    mark("ch10-bank", "library view lists signs + bindings")
    click_el(ws, "#tray-signs")
    wait_js(ws, "!document.getElementById('sheet-signs').hidden", 8)
    time.sleep(1.2)
    txt = js(ws, "(document.getElementById('lib-list')||{}).textContent||''")
    log("library rows —", (txt or "")[:160].replace("\n", " "))
    s, j = http("/bank")
    with open(os.path.join(OUT, "ch10-bank.json"), "w") as f:
        json.dump(j, f, indent=1)
    time.sleep(2.5)
    click_el(ws, "#lib-x")
    time.sleep(0.8)


def main():
    open(CHAP, "w").close()
    open(MARK, "w").close()
    ws = cdp_connect()
    log("cdp connected")
    chapters = [ch00_scene, ch01_pair, ch02_type, ch03_attach, ch04_live,
                ch05_teach, ch06_context, ch07_macro, ch08_media,
                ch09_observe, ch10_bank]
    results = {}
    for ch in chapters:
        try:
            ch(ws)
            results[ch.__name__] = "ok"
        except Exception as e:
            results[ch.__name__] = "FAIL: %s" % e
            log("!! %s failed: %r" % (ch.__name__, e))
            try:
                push_clip("namaste")
            except Exception:
                pass
            time.sleep(1.0)
    log("results: %s" % json.dumps(results))
    with open(os.path.join(OUT, "drive-results.json"), "w") as f:
        json.dump(results, f, indent=1)
    bad = [k for k, v in results.items() if v != "ok"]
    sys.exit(2 if bad else 0)


if __name__ == "__main__":
    main()
