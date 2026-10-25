#!/usr/bin/env python3
"""drive.py — drive the recorded demo end to end.

Chapters (each marked for cut.sh):
  00 scene    everything up, title card on screen
  01 pair     pair.html: type address + real pair code -> composer
  02 type     type in the composer, lands in the real editor sink
  03 live     LIVE sheet, replayed kp42, palm->listening, sign OPEN-TERM
              -> xfce4-terminal really opens
  04 teach    teach "take a shot" (z-shape) through the wizard, bind the
              screenshot tool via the @ picker, sign it -> PNG lands
  05 context  same sign (NEXT): editor focus -> typed marker, chrome
              focus -> ctrl+Tab switches tab
  06 macro    STANDUP sign -> macro opens a terminal and types into it
  07 media    VOL-UP -> the box has no pactl/amixer -> honest refusal
              shown on the reading strip
  08 observe  an untrained squiggle -> rejected, reason on the strip
  09 bank     library sheet lists every taught sign + its binding

Driving is two-layered and honest about it:
  * xdotool moves/clicks/type for everything where laptop focus may move
    (pair form, window focus, tabs) — real input events.
  * CDP Runtime.evaluate clicks (el.click()) for buttons whose REAL side
    effect is an HTTP call the phone makes — on a real phone the tap
    never moves laptop focus; dispatching via CDP reproduces that. The
    DOM event path is identical: real handlers, real fetch, real POST.

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
LOGS = os.path.join(DEMO, "logs")
WFD = "http://127.0.0.1:8742"
REPLAY = "http://127.0.0.1:8877"
CDP = "http://127.0.0.1:9223"
DISPLAY = ":98"
KEY = open(os.path.join(DEMO, "home", ".config", "wf", "key")).read().strip()

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
    r = xdo("search", "--name", title)
    wids = [w for w in (r.stdout or "").split() if w.strip()]
    if not wids:
        return None
    xdo("windowactivate", wids[-1])
    return wids[-1]


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
    page = next(t for t in targets if t.get("type") == "page" and
                "127.0.0.1:8742" in (t.get("url") or ""))
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
    focus("pair") or focus("127.0.0.1")
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
        try:
            if text.split("—")[0].strip() in open(os.path.join(
                    OUT, "sink.log")).read():
                log("sink got the line")
                time.sleep(1.5)
                return
        except OSError:
            pass
        time.sleep(0.3)
    raise AssertionError("text never landed in sink")


def _palm_sign_then(ws, sign_clip, gloss, timeout=14):
    """Push palm (listen on), then the sign; return last_match."""
    push_clip("palm")
    wait_state(lambda j: j.get("listening"), 8, "listening")
    log("listening — signing", gloss)
    push_clip(sign_clip)
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = live_state()
        m = j.get("last_match") or {}
        if m.get("gloss") and not m.get("mode"):
            return m
        time.sleep(0.2)
    raise TimeoutError("no match for " + gloss)


def ch03_live(ws):
    mark("ch03-live", "live sheet + replay + OPEN-TERM fires a terminal")
    click_el(ws, "#tray-live")
    wait_js(ws, "!!document.querySelector('.lv-replay')", 15,
            "replay tag visible")
    time.sleep(1.0)
    m = _palm_sign_then(ws, "swipe-r-perform", "OPEN-TERM")
    log("match:", json.dumps(m)[:200])
    assert m.get("gloss") == "OPEN-TERM" and m.get("accepted"), m
    assert wait_win("wf-fired", 8), "fired terminal never appeared"
    log("wf-fired window present")
    with open(os.path.join(OUT, "ch03.txt"), "w") as f:
        f.write("match: %s\n" % json.dumps(m))
        f.write(run("wmctrl", "-l").stdout)
    time.sleep(1.5)
    push_clip("namaste")
    wait_state(lambda j: not j.get("listening"), 8, "stopped")
    log("namaste — stopped")


def ch04_teach(ws):
    mark("ch04-teach", "teach a sign -> screenshot binding -> fires")
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
        push_clip("z-shape-take%d" % i)
        log("take", i, "pushed")
        t0 = time.time()
        while time.time() - t0 < 15:
            j = live_state()
            t = j.get("teach") or {}
            if int(t.get("got") or 0) >= i:
                log("take %d captured (got=%s)" % (i, t.get("got")))
                break
            time.sleep(0.25)
        else:
            raise AssertionError("take %d not captured" % i)
        time.sleep(1.0)                   # hands fully away between takes
    click_el(ws, "#tw-rec-done")
    wait_js(ws, "!!document.querySelector('.tw-step[data-step=bind].on')",
            8, "bind step")
    time.sleep(0.8)
    click_el(ws, "#tw-pick")
    wait_js(ws, "!document.getElementById('sheet-actions').hidden", 8,
            "picker open")
    time.sleep(1.0)
    # the screenshot row, wherever it sits in the list
    found = js(ws, """(() => {
      const rows = [...document.querySelectorAll('#act-list .act-row')];
      const r = rows.find(r => /screenshot/i.test(r.textContent));
      if (!r) return null; r.click(); return r.textContent.trim(); })()""")
    log("picker row:", found)
    assert found, "no screenshot row in picker"
    time.sleep(0.6)
    click_el(ws, "#tw-save")
    wait_js(ws, "!!document.querySelector('.tw-step[data-step=done].on')",
            8, "done step")
    log("TAKE-A-SHOT bound to sys.screenshot")
    time.sleep(1.0)
    click_el(ws, "#tw-done-live")
    wait_js(ws, "!document.getElementById('sheet-live').hidden", 8)
    time.sleep(1.0)
    focus("wf-editor")                    # harmless focus for the shot
    pics_before = set(os.listdir(os.path.join(DEMO, "home", "Pictures")))
    m = _palm_sign_then(ws, "z-shape-perform", "TAKE-A-SHOT")
    log("match:", json.dumps(m)[:200])
    assert m.get("gloss") == "TAKE-A-SHOT" and m.get("accepted"), m
    t0 = time.time()
    while time.time() - t0 < 12:
        pics = set(os.listdir(os.path.join(DEMO, "home", "Pictures")))
        new = pics - pics_before
        if new:
            log("screenshot landed:", sorted(new))
            break
        time.sleep(0.4)
    else:
        raise AssertionError("no screenshot file landed")
    push_clip("namaste")
    time.sleep(1.2)


def ch05_context(ws):
    mark("ch05-context", "same sign, different apps -> different effects")
    click_el(ws, "#tray-live")
    wait_js(ws, "!document.getElementById('sheet-live').hidden", 8)
    # branch 1: editor focused -> type marker
    focus("wf-editor")
    time.sleep(0.8)
    m = _palm_sign_then(ws, "swipe-l-perform", "NEXT")
    log("editor branch:", json.dumps(m)[:200])
    assert m.get("accepted"), m
    t0 = time.time()
    while time.time() - t0 < 10:
        if "context dispatch" in open(os.path.join(OUT, "sink.log")).read():
            log("marker typed into editor")
            break
        time.sleep(0.3)
    else:
        raise AssertionError("context marker never typed")
    # branch 2: tabs chrome focused -> ctrl+Tab
    tabs = focus("New Tab") or focus("Tab")
    if not tabs:
        log("WARN: tabs window not found — context chrome branch skipped")
        return
    time.sleep(0.8)
    title0 = xdo("getactivewindow", "getwindowname").stdout.strip()
    m = _palm_sign_then(ws, "swipe-l-perform", "NEXT")
    log("chrome branch:", json.dumps(m)[:200])
    time.sleep(1.5)
    title1 = xdo("getactivewindow", "getwindowname").stdout.strip()
    log("tab title before/after:", title0, "/", title1)
    with open(os.path.join(OUT, "ch05.txt"), "w") as f:
        f.write("editor branch: %s\nchrome branch: %s\n"
                "title %s -> %s\n" % (json.dumps(m)[:0] or "ok",
                                      json.dumps(m), title0, title1))
    push_clip("namaste")
    time.sleep(1.0)


def ch06_macro(ws):
    mark("ch06-macro", "one sign -> opens terminal + types")
    focus("wf-editor")
    time.sleep(0.6)
    before = xdo("search", "--name", "Terminal").stdout.count("\n")
    m = _palm_sign_then(ws, "v-check-perform", "STANDUP")
    log("match:", json.dumps(m)[:200])
    assert m.get("accepted"), m
    assert wait_win("Terminal", 12) or wait_win("xfce4", 12) or \
        xdo("search", "--name", "Terminal").stdout.count("\n") > before, \
        "macro terminal never opened"
    log("macro terminal opened")
    with open(os.path.join(OUT, "ch06.txt"), "w") as f:
        f.write(run("wmctrl", "-l").stdout)
    time.sleep(1.5)
    push_clip("namaste")
    time.sleep(1.0)


def ch07_media(ws):
    mark("ch07-media", "volume-up sign -> honest refusal (no pactl)")
    focus("wf-editor")
    time.sleep(0.6)
    m = _palm_sign_then(ws, "push-up-perform", "VOL-UP")
    log("match:", json.dumps(m)[:220])
    assert m.get("gloss") == "VOL-UP" and m.get("accepted"), m
    act = m.get("action") or {}
    assert act.get("refused"), "expected honest refusal"
    log("refused:", act.get("refused"))
    time.sleep(2.0)
    push_clip("namaste")
    time.sleep(0.8)


def ch08_observe(ws):
    mark("ch08-observe", "untrained sign -> rejected, reason shown")
    push_clip("palm")
    wait_state(lambda j: j.get("listening"), 8, "listening")
    push_clip("unknown")
    m = wait_state(lambda j: (j.get("last_match") or {}).get("accepted")
                   is False and (j.get("last_match") or {}).get("reason")
                   not in (None, ""), 12, "rejection")
    log("reject row:", json.dumps(m.get("last_match"))[:220])
    time.sleep(2.5)
    push_clip("namaste")
    time.sleep(0.8)


def ch09_bank(ws):
    mark("ch09-bank", "library view lists signs + bindings")
    click_el(ws, "#tray-signs")
    wait_js(ws, "!document.getElementById('sheet-signs').hidden", 8)
    time.sleep(1.2)
    n = js(ws, "document.querySelectorAll('#lib-list .lib-row,"
               " #lib-list .lib-card, #lib-list section > div').length")
    txt = js(ws, "(document.getElementById('lib-list')||{}).textContent||''")
    log("library rows:", n, "—", (txt or "")[:160].replace("\n", " "))
    s, j = http("/bank")
    with open(os.path.join(OUT, "ch09-bank.json"), "w") as f:
        json.dump(j, f, indent=1)
    time.sleep(2.5)
    click_el(ws, "#lib-x")
    time.sleep(0.8)


def main():
    open(CHAP, "w").close()
    open(MARK, "w").close()
    ws = cdp_connect()
    log("cdp connected")
    chapters = [ch00_scene, ch01_pair, ch02_type, ch03_live, ch04_teach,
                ch05_context, ch06_macro, ch07_media, ch08_observe,
                ch09_bank]
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
