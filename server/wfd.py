#!/usr/bin/env python3
"""wfd — Flow Composer daemon. Receives text/images/files/sign keypoints
from the phone app, injects them at the laptop cursor, and fires taught
signs' bound actions. Auth via shared pairing key; the live-sign surface
is LAN/USB only (never through a tunnel)."""
import hashlib, hmac, json, math, os, queue, re, secrets, shutil, string, subprocess, sys, tempfile, threading, time, urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import endpointer            # motion segmenter — ships in server/
except ImportError:
    endpointer = None
try:
    import signbank              # W1a pure-python DTW bank — optional
except ImportError:
    signbank = None
try:
    import act_iface             # the ActionProvider contract + gate
    import act_linux             # the Linux/X11 provider
except ImportError:
    act_iface = act_linux = None
try:
    import act_platform          # the macOS/Windows provider
except ImportError:
    act_platform = None
try:
    import system_ctl            # sys/media/net/clip ops behind the catalog
except ImportError:
    system_ctl = None
try:
    import macro as _macro       # {"kind":"macro","steps":[...]} chains
except ImportError:
    _macro = None
try:
    import signflow_ops          # composer: sequences, profiles, observe
except ImportError:
    signflow_ops = None
try:
    import record_action         # teach-by-demonstration input capture
except ImportError:
    record_action = None
try:
    import packs                 # prefilled Teach packs (§B)
except ImportError:
    packs = None
try:
    import teach_train           # held-out-take training check (§B)
except ImportError:
    teach_train = None

PORT = int(os.environ.get("WF_PORT", "8741"))
BOUND_PORT = PORT  # set to the real port after bind (fallback may differ)
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
WEB = os.path.join(ROOT, "web")
KEY_FILE = os.path.expanduser("~/.config/wf/key")
UPLOADS = os.path.expanduser("~/wf-uploads")
BANK_FILE = os.path.expanduser("~/.config/wf/signbank.json")
COMMANDS_TSV = os.path.expanduser("~/.config/wf/commands.tsv")
ENGINE_FILE = os.path.expanduser("~/.config/wf/engine")

# request bodies are capped BEFORE reading (was: unbounded rfile.read)
MAX_BODY = int(os.environ.get("WF_MAX_BODY", str(256 * 1024 * 1024)))
BODY_CAPS = {"/pair": 16 * 1024, "/live/kp": 256 * 1024,
             "/live/event": 16 * 1024, "/live/listen": 16 * 1024,
             "/live/mode": 16 * 1024,
             "/send": 8 * 1024 * 1024, "/teach/begin": 64 * 1024,
             "/teach/end": 64 * 1024, "/act/confirm": 64 * 1024,
             "/bank": 256 * 1024}
# live pipeline knobs — env-tunable, not hidden constants
LIVE_MAX_FPS = int(os.environ.get("WF_LIVE_MAX_FPS", "45"))
# One camera stream is 15-30 fps; allow a 2x burst per identity so two
# coalesced consumers/tabs at 40 fps do not 429 each other. A single
# abusive identity is still capped (90 fps default), and the whole daemon
# has a 2x-global hard cap. env-tunable, not hidden.
LIVE_BURST = max(1, int(os.environ.get("WF_LIVE_BURST", "2")))
LIVE_MAX_CLIENTS = max(1, int(os.environ.get("WF_LIVE_MAX_CLIENTS", "16")))
LIVE_IDLE_S = float(os.environ.get("WF_LIVE_IDLE_S", "120"))
CONFIRM_TTL_S = float(os.environ.get("WF_CONFIRM_TTL", "120"))
# one movement must fire once: a segment split by mid-sign hesitation
# reopens within ~0.5s; a deliberate repeat sign pauses longer.
DUP_GAP_MS = int(os.environ.get("WF_DUP_GAP_MS", "550"))
PALM_SIGN = os.environ.get("WF_PALM_SIGN", "PALM-OPEN")
NAMASTE_SIGN = os.environ.get("WF_NAMASTE_SIGN", "NAMASTE")
TEACH_TAKES = int(os.environ.get("WF_TEACH_TAKES", "3"))

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
    if _darwin():
        try:
            out = sh("ifconfig").stdout
            wifi, other = [], []
            ifname = ""
            for line in out.splitlines():
                m = re.match(r"^(\S+):", line)
                if m:
                    ifname = m.group(1)
                    continue
                m = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)\b", line)
                if not m:
                    continue
                addr = m.group(1)
                if addr.startswith(("127.", "169.254.")) or ifname.startswith(
                        ("utun", "bridge", "vmenet", "awdl", "llw",
                         "gif", "stf", "lo", "ap", "anpi", "ipsec")):
                    continue
                (wifi if ifname.startswith("en") else other).append(addr)
            return wifi + other
        except Exception:
            return []
    try:
        out = sh("ip", "-4", "-o", "addr", "show", "scope", "global").stdout
        wifi, other = [], []
        for line in out.splitlines():
            p = line.split()
            ifname, addr = p[1], p[3].split("/")[0]
            if ifname.startswith(("docker", "veth", "br-", "virbr", "lo")) or \
                addr.startswith("169.254."):
                continue  # host-internal / link-local nets — a phone can never reach these
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


def _darwin():
    return sys.platform == "darwin"


def _run(argv, timeout=15):
    """(ok, detail) argv-only spawn — same contract as act_platform._run."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode == 0, (r.stderr or r.stdout or "").strip()
    except FileNotFoundError:
        return False, "not found: %s" % argv[0]
    except subprocess.TimeoutExpired:
        return False, "%s timed out" % os.path.basename(str(argv[0]))
    except OSError as e:
        return False, str(e)


def _osa(script, timeout=15):
    return _run(["osascript", "-e", script], timeout=timeout)


def _osa_esc(s):
    """A string safe to embed inside an AppleScript "..." literal."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def active_window_class():
    if _darwin():
        try:
            ok, out = _osa('tell application "System Events" to get name '
                           'of first application process whose frontmost '
                           'is true')
            return out.strip().lower() if ok else ""
        except Exception:
            return ""
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


# macOS window rows: id = "<pid>:<window index>" — the pid focuses the
# app, the index AXRaise's the exact window (AX exposes no stable id).
_MAC_AX_LIST = (
    'tell application "System Events"\n'
    'set _o to ""\n'
    'repeat with _p in (every application process whose visible is true)\n'
    'set _n to name of _p\n'
    'set _u to unix id of _p\n'
    'repeat with _i from 1 to (count of windows of _p)\n'
    'try\n'
    'set _t to name of (window _i of _p)\n'
    'set _o to _o & _u & "\\t" & _i & "\\t" & _n & "\\t" & _t & "\\n"\n'
    'end try\n'
    'end repeat\n'
    'end repeat\n'
    'return _o\n'
    'end tell')

def _mac_windows_ax():
    ok, out = _osa(_MAC_AX_LIST, timeout=20)
    if not ok or not out.strip():
        return []
    rows = []
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) != 4:
            continue
        pid, wid, app, title = p
        if not pid.strip().isdigit():
            continue
        rows.append({"id": "%s:%s" % (pid.strip(), wid.strip()),
                     "app": app.strip(),
                     "title": (title.strip() or app.strip())[:120]})
    return rows


def _mac_app_names():
    """Fallback when AX enumeration is unavailable (Accessibility denied):
    visible app names, which `open -a` can still activate."""
    ok, out = _osa('tell application "System Events" to get name of '
                   'every application process whose visible is true')
    if not ok:
        return []
    return [{"id": n.strip(), "title": n.strip(), "app": n.strip()}
            for n in out.split(",") if n.strip()]


def _mac_app_path(pid):
    """pid -> the owning .app path ('open <path>' activates it without any
    consent dialog), or ''."""
    try:
        comm = sh("ps", "-o", "comm=", "-p", str(pid)).stdout.strip()
    except Exception:
        return ""
    parts = comm.split("/")
    for i, seg in enumerate(parts):
        if seg.endswith(".app"):
            return "/".join(parts[:i + 1])
    return ""


def list_windows():
    """Open top-level windows on the laptop: [{id, title, app}]."""
    if _darwin():
        rows = _mac_windows_ax()
        return rows if rows else _mac_app_names()
    out = []
    try:
        r = sh("wmctrl", "-lx")
        for line in (r.stdout or "").splitlines():
            parts = line.split(None, 4)
            if len(parts) < 4 or not parts[0].startswith("0x"):
                continue
            if parts[1] == "-1":                 # sticky panels/docks
                continue
            wclass = parts[2].split(".")[-1]
            title = parts[4].strip() if len(parts) > 4 else ""
            if not title:
                continue
            out.append({"id": str(int(parts[0], 16)), "title": title[:120],
                        "app": wclass})
    except Exception as e:
        print(f"[wfd] windows: {e!r}", flush=True)
    return out


def focus_window(wid):
    """Bring the picked window to the front before a send. True on success."""
    wid = str(wid or "").strip()
    if not wid:
        return False
    if _darwin():
        return _mac_focus_window(wid)
    if not wid.isdigit():
        return False
    try:
        r = subprocess.run(["xdotool", "windowactivate", "--sync", wid],
                           capture_output=True, timeout=4)
        if r.returncode != 0:
            r = subprocess.run(["wmctrl", "-i", "-a", hex(int(wid))],
                               capture_output=True, timeout=4)
        time.sleep(0.2)
        return r.returncode == 0
    except Exception:
        return False


def _mac_focus_window(wid):
    """'pid:windex' rows raise the exact window (System Events, needs
    Accessibility); a bare app name activates it (open -a, consent-free)."""
    pid, _, winid = wid.partition(":")
    if pid.isdigit():
        ok, _ = _osa('tell application "System Events" to tell '
                     '(first application process whose unix id is %s) '
                     'to set frontmost to true' % pid)
        if ok:
            # winid is the window's index in the app's AX window order
            # (AX exposes no stable window id on modern macOS).
            if winid.isdigit() and winid != "0":
                _osa('tell application "System Events" to tell '
                     '(first application process whose unix id is %s) to '
                     'perform action "AXRaise" of window %s'
                     % (pid, winid))
            time.sleep(0.2)
            return True
        # no Accessibility: NSWorkspace-level activate via the .app path
        path = _mac_app_path(pid)
        if path:
            ok, _ = _run(["open", path])
            if ok:
                time.sleep(0.2)
                return True
        return False
    # app-name row (or a name the picker shipped back)
    ok, _ = _run(["open", "-a", wid])
    if not ok:
        ok, _ = _osa('tell application "%s" to activate' % _osa_esc(wid))
    if ok:
        time.sleep(0.2)
    return ok


# ALL keystroke/clipboard injection runs through ONE serial worker.
# (bug fix: fire-and-forget `xdotool type` Popens interleaved keystrokes
# on rapid sends — every inject job now queues behind the previous one.)
_INJECT_Q = queue.Queue()


def _inject_worker():
    while True:
        job = _INJECT_Q.get()
        try:
            job()
        except Exception as e:
            print(f"[wfd] inject: {e!r}", flush=True)


threading.Thread(target=_inject_worker, daemon=True).start()


def type_keystrokes(text):
    if _darwin():
        # osascript 'keystroke "…"' posts real key events — same one-shot
        # semantics as `xdotool type --` — but only for ASCII: macOS maps
        # anything else through the keyboard layout and produces garbage
        # (verified: "é"/"✓" typed as "a"). Non-ASCII takes the clipboard
        # path instead, which preserves the bytes exactly.
        if not str(text).isascii():
            type_paste(text)
            return

        def job():
            _osa('tell application "System Events" to keystroke "%s"'
                 % _osa_esc(text), timeout=600)
        _INJECT_Q.put(job)
        return

    def job():
        subprocess.run(["xdotool", "type", "--clearmodifiers",
                        "--delay", "12", "--", text], timeout=600)
    _INJECT_Q.put(job)


def _clip_get():
    """Current plain-text clipboard (used to restore after a paste)."""
    try:
        if _darwin():
            return sh("pbpaste").stdout
        return sh("xclip", "-selection", "clipboard", "-o").stdout
    except Exception:
        return None


def _clip_set(text):
    if _darwin():
        subprocess.run(["pbcopy"], input=text, text=True)
    else:
        subprocess.run(["xclip", "-selection", "clipboard", "-i"],
                       input=text, text=True)


def _paste_chord(cls):
    """The focused app's paste shortcut."""
    if _darwin():
        return None                      # sent via osascript, not xdotool
    return "ctrl+shift+v" if any(t in cls for t in TERMINAL_CLASSES) \
        else "ctrl+v"


def _send_paste_chord(cls):
    if _darwin():
        _osa('tell application "System Events" to keystroke "v" '
             'using {command down}')
    else:
        sh("xdotool", "key", "--clearmodifiers", cls)


def type_paste(text):
    cls = active_window_class()
    paste_keys = _paste_chord(cls)
    old = _clip_get()

    def job():
        _clip_set(text)
        time.sleep(0.15)
        _send_paste_chord(paste_keys)
        time.sleep(0.6)
        if old is not None:
            _clip_set(old)

    _INJECT_Q.put(job)


def send_text(text, mode):
    global LAST_SEND
    if os.environ.get("WF_NO_LOG_TEXT"):
        print(f"[wfd] {mode}: <{len(text)} chars>", flush=True)
    else:
        print(f"[wfd] {mode}: {text!r}", flush=True)
    if mode == "paste":
        type_paste(text)
    else:
        type_keystrokes(text)
    LAST_SEND = {"t_ms": now_ms(), "ok": True, "kind": "text",
                 "into": active_window_class(), "n": len(text)}


IMG_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}


def sniff_mime(data, ctype):
    if data[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if data[:3] == b"\xff\xd8\xff": return "image/jpeg"
    if data[:4] == b"GIF8": return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP": return "image/webp"
    return ctype.split(";")[0] if ctype in IMG_EXT else "image/png"


def _mac_set_file_clipboard(path, mime):
    """NSPasteboard: file URL (pastes as a file anywhere) plus the image
    object itself for image/* (pastes as pixels in image-capable apps).
    JXA, stock frameworks — replaces the X11 wfclip.py owner."""
    esc = str(path).replace("\\", "\\\\").replace('"', '\\"')
    js = ('ObjC.import("AppKit");'
          'var pb=$.NSPasteboard.generalPasteboard;pb.clearContents;'
          'var u=$.NSURL.fileURLWithPath("%s");var o=[u];' % esc)
    if str(mime or "").startswith("image/"):
        js += ('var i=$.NSImage.alloc.initWithContentsOfFile("%s");'
               'if(i&&!i.isNil){o.push(i);}' % esc)
    js += 'pb.writeObjects(o);'
    return _run(["osascript", "-l", "JavaScript", "-e", js])


def paste_file(path, mime, cls):
    keys = _paste_chord(cls)
    old = _clip_get()

    def job():
        if _darwin():
            ok, det = _mac_set_file_clipboard(path, mime)
            if not ok:
                print(f"[wfd] mac clip failed: {det}", flush=True)
                return
            time.sleep(0.15)
        else:
            subprocess.Popen(
                [sys.executable, os.path.join(BASE, "wfclip.py"), path,
                 mime],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            time.sleep(0.3)
        _send_paste_chord(keys)
        # hand the clipboard back so the next paste doesn't replay the file
        time.sleep(1.2)
        _clip_set(old if old is not None else "")

    _INJECT_Q.put(job)


def _to_png(src, png):
    """jpeg/webp -> png. sips is stock on macOS; ImageMagick elsewhere."""
    if _darwin():
        r = subprocess.run(["sips", "-s", "format", "png", src,
                            "--out", png], capture_output=True, timeout=15)
        if r.returncode == 0 and os.path.isfile(png):
            return True
    conv = shutil.which("convert")
    if conv:
        r = subprocess.run([conv, src, png],
                           capture_output=True, timeout=15)
        return r.returncode == 0
    return False


def send_image(data, ctype=""):
    mime = sniff_mime(data, ctype)
    # bug fix: fixed /tmp/wf-img* paths raced on concurrent sends — mkstemp.
    fd, src = tempfile.mkstemp(prefix="wf-img-raw-",
                               suffix="." + IMG_EXT.get(mime, "bin"))
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if mime == "image/png" or mime == "image/gif":
        path = src                       # usable as-is (gif was never
    else:                                # written to its old fixed path)
        fd, png = tempfile.mkstemp(prefix="wf-img-", suffix=".png")
        os.close(fd)
        if _to_png(src, png):
            path, mime = png, "image/png"
            try:
                os.unlink(src)
            except OSError:
                pass
        else:
            path = src                   # keep the real sniffed mime
    cls = active_window_class()
    paste_file(path, mime, cls)
    print(f"[wfd] image: {len(data)}B {mime} into '{cls}'", flush=True)
    global LAST_SEND
    LAST_SEND = {"t_ms": now_ms(), "ok": True, "kind": "image",
                 "into": cls, "n": len(data)}
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
    global LAST_SEND
    LAST_SEND = {"t_ms": now_ms(), "ok": True, "kind": "file",
                 "into": cls, "n": len(data), "path": path}
    return cls, path


# ---------------------------------------------------------------------
# Live sign engine glue (PRD §5.2):
#   /live/kp frames -> endpointer -> signbank match -> commands.tsv argv.
# All live/teach/confirm state sits behind LIVE_LOCK. The daemon still
# runs as a pure text bridge when endpointer/signbank are absent — the
# imports are guarded and every use degrades honestly.
# ---------------------------------------------------------------------

LIVE_LOCK = threading.RLock()
LIVE = {"session": None, "listening": False, "ep": None,
        "last_match": None, "seen": 0.0,
        "rates": {}, "rate_all": deque(),
        "observe": False, "text_mode": False,
        "last_action": None, "last_accept": None,
        "dup_fires": 0,
        "feed": deque(maxlen=64)}
TEACH = {"active": False, "name": "", "takes": 0, "got": 0,
         "label": "", "action": None, "session": None, "frames": []}
PENDING = {}                    # confirm_id -> {gloss,label,argv,at}
_cmd_cache = {"key": None, "rows": {}}
LIVE_POSTS = ("/live/kp", "/live/event", "/live/listen", "/live/mode",
              "/teach/begin", "/teach/end", "/teach/record/start",
              "/teach/record/stop", "/act/confirm", "/packs/install")


def now_ms():
    return int(time.time() * 1000)


def load_bank():
    if signbank is None:
        return None
    try:
        return signbank.SignBank.load(BANK_FILE)
    except Exception as e:
        print(f"[wfd] signbank load failed ({e!r}); starting empty", flush=True)
        try:
            b = signbank.SignBank()
            b.path = BANK_FILE
            return b
        except Exception:
            return None


def save_bank():
    if BANK is None:
        return
    try:
        BANK.save(BANK_FILE)
    except Exception as e:
        print(f"[wfd] signbank save failed: {e!r}", flush=True)


BANK = load_bank()


# ---------------------------------------------------------------------
# Action provider — bank-bound action OBJECTS (the @ picker's
# {"type":...}/{kind,tool,args} shapes, kind:context, kind:macro) execute
# through act_iface.ActionProvider: tier>=2 confirm gate, first-use argv
# ledger and the focus guard all live in that layer. commands.tsv argv
# rows keep their existing direct-spawn path (legacy, already audited).
# ---------------------------------------------------------------------
ACT_PROVIDER = None
if act_iface is not None:
    try:
        if sys.platform.startswith("linux") and act_linux is not None:
            ACT_PROVIDER = act_linux.LinuxX11Provider(
                config_dir=os.path.dirname(BANK_FILE))
        elif act_platform is not None:
            ACT_PROVIDER = act_platform.CrossPlatformProvider(
                config_dir=os.path.dirname(BANK_FILE))

        if system_ctl is not None:
            def _sys_plan(a, ctx):
                """canonical type -> system_ctl catalog id -> ActionPlan."""
                t = str(a.get("type") or "")
                toolmap = {
                    "volume": "sys.mute" if str(a.get("op")) == "mute"
                              else "sys.volume-" + str(a.get("op", "up")),
                    "media": "media." + str(a.get("op", "playpause")).replace("_", ""),
                    "screenshot": "sys.screenshot-region"
                                  if str(a.get("mode")) == "region"
                                  else "sys.screenshot",
                    "lock": "sys.lock",
                    "dnd": "sys.dnd",
                    "wifi": "net.wifi",
                    "bluetooth": "net.bluetooth",
                    "brightness": "sys.brightness-" + str(a.get("op", "up")),
                    "clipboard_history": "clipboard.history",
                }
                tid = toolmap.get(t, t)
                args = {k: a[k] for k in ("arg", "index", "mode", "step")
                        if k in a}

                def _fn(_c):
                    ok, detail = system_ctl.dispatch(tid, args)
                    if ok:
                        return act_iface.ActionResult.done(detail)
                    return act_iface.ActionResult.refused(detail)
                tier = system_ctl.TOOL_TIERS.get(tid, 1)
                return act_iface.ActionPlan(_fn, tier=tier,
                                            describe="%s (%s)" % (a.get(
                                                "label") or tid, tid))
            _SYS_CAT = [
                ("screenshot", {"id": "sys.screenshot", "icon": "camera-photo", "kind": "sys",
                                "label": "take a screenshot", "tier": 1,
                                "desc": "full display -> ~/Pictures"}),
                ("volume", {"id": "sys.volume-up", "icon": "audio-volume-high", "kind": "sys",
                            "label": "volume up", "tier": 1}),
                ("volume", {"id": "sys.volume-down", "icon": "audio-volume-low", "kind": "sys",
                            "label": "volume down", "tier": 1}),
                ("volume", {"id": "sys.mute", "icon": "audio-volume-muted", "kind": "sys",
                            "label": "mute / unmute", "tier": 1}),
                ("media", {"id": "media.playpause", "icon": "media-playback-start", "kind": "media",
                           "label": "play / pause", "tier": 1}),
                ("media", {"id": "media.next", "kind": "media",
                           "label": "next track", "tier": 1}),
                ("media", {"id": "media.prev", "kind": "media",
                           "label": "previous track", "tier": 1}),
                ("brightness", {"id": "sys.brightness-up", "kind": "sys",
                                "label": "brightness up", "tier": 1}),
                ("brightness", {"id": "sys.brightness-down", "kind": "sys",
                                "label": "brightness down", "tier": 1}),
                ("lock", {"id": "sys.lock", "kind": "sys",
                          "label": "lock the screen", "tier": 2}),
                ("dnd", {"id": "sys.dnd", "kind": "sys",
                         "label": "do-not-disturb", "tier": 1}),
                ("wifi", {"id": "net.wifi", "kind": "net",
                          "label": "wifi toggle", "tier": 2}),
                ("bluetooth", {"id": "net.bluetooth", "kind": "net",
                               "label": "bluetooth toggle", "tier": 2}),
                ("clipboard_history", {"id": "clipboard.history",
                                       "kind": "clip",
                                       "label": "clipboard history",
                                       "tier": 1}),
            ]
            try:
                _caps = system_ctl.capabilities()
            except Exception:
                _caps = {}
            for _t, _row in _SYS_CAT:
                row = dict(_row)
                cap = _caps.get(row["id"], {})
                if cap and not cap.get("available"):
                    row["desc"] = (row.get("desc", "") +
                                   (" — " if row.get("desc") else "") +
                                   "not available here").strip(" —")
                ACT_PROVIDER.register(_t, _sys_plan, catalog=row)

        if _macro is not None:
            def _macro_plan(a, ctx):
                steps = a.get("steps")
                # argv-bearing steps join the plan's argv list so the
                # first-use gate covers them like any other command
                argvs = [list(s["argv"]) for s in (steps or [])
                         if isinstance(s, dict) and
                         isinstance(s.get("argv"), list)]
                if steps is not None:
                    _n, _errs = _macro.validate_steps(steps)
                    if _errs:
                        return act_iface.ActionResult.refused(
                            "macro: " + "; ".join(_errs))

                def _fn(c):
                    r = _macro.run_macro(
                        steps, ACT_PROVIDER,
                        ctx=dict(c, depth=int(c.get("depth", 0)) + 1))
                    return act_iface.ActionResult(
                        ok=bool(r.get("ok")),
                        detail=r.get("error") or
                               "%d/%d steps" % (r.get("ran", 0),
                                                len(r.get("steps") or [])),
                        tier=int(a.get("tier", 2) or 0),
                        extra={"steps": r.get("steps")})
                return act_iface.ActionPlan(
                    _fn, describe="macro (%d steps)" % len(steps or []),
                    argv=argvs)
            ACT_PROVIDER.register("macro", _macro_plan)
    except Exception as e:
        print(f"[wfd] act provider unavailable: {e!r}", flush=True)
        ACT_PROVIDER = None

LAST_SEND = None                      # {t_ms, ok, into, kind}


def bank_size():
    if BANK is None:
        return 0
    try:
        return len(BANK.list())
    except Exception:
        return 0


def live_reset():
    """New listen session / idle expiry: clean segmenter + session id.
    An active teach is NOT touched — teach state is owned by the
    session that began it (TEACH['session']) and ends via /teach/end,
    /teach/cancel or a fresh /teach/begin, never by a kp-stream
    rotation (a foreign streamer must not cancel someone's capture)."""
    LIVE.update(session=None, listening=False, last_match=None, seen=0.0,
                last_accept=None)
    # rate counters are per-identity and self-prune; keeping them across a
    # session change means a new tab cannot reset the limiter for free.
    LIVE["feed"].clear()
    LIVE["ep"] = endpointer.Endpointer(
        palm_sign=PALM_SIGN, namaste_sign=NAMASTE_SIGN) if endpointer else None
    if OPS is not None:
        try:
            OPS.reset_session()
        except Exception:
            pass


def commands():
    """GLOSS -> {label, argv, raw, tier} from ~/.config/wf/commands.tsv.
    Hot-reloads on (mtime,size) change — deliberately NOT the Rust
    OnceLock one-shot, so edits apply without a daemon restart.
    Row format: GLOSS<TAB>label<TAB>argv[<TAB>tier]; '#' comments;
    argv whitespace-split like the Rust loader (no shell)."""
    try:
        st = os.stat(COMMANDS_TSV)
        key = (st.st_mtime, st.st_size)
    except OSError:
        key = None
    if key == _cmd_cache["key"]:
        return _cmd_cache["rows"]
    rows = {}
    try:
        with open(COMMANDS_TSV, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                gloss = parts[0].strip().upper()
                label = parts[1].strip()
                cols, tier = parts[2:], 0
                if len(cols) > 1:                      # optional 4th col
                    try:
                        tier = int(cols[-1].strip())
                        cols = cols[:-1]
                    except ValueError:
                        pass
                raw = "\t".join(cols).strip()
                argv = raw.split()
                if gloss and argv:
                    rows[gloss] = {"label": label, "argv": argv,
                                   "raw": raw, "tier": max(tier, 0)}
    except OSError:
        pass
    _cmd_cache.update(key=key, rows=rows)
    return rows


def _write_commands(rows):
    """Atomic commands.tsv rewrite (mkstemp+replace, mode 600)."""
    d = os.path.dirname(COMMANDS_TSV)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".commands-", text=True)
    with os.fdopen(fd, "w") as f:
        f.write("# wf commands — GLOSS<TAB>label<TAB>argv[<TAB>tier]\n")
        f.write("# signed GLOSS fires argv (no shell). tier>=2 parks for "
                "POST /act/confirm.\n")
        for g in sorted(rows):
            r = rows[g]
            line = "%s\t%s\t%s" % (g, r.get("label", ""), r.get("raw", ""))
            if r.get("tier"):
                line += "\t%d" % int(r["tier"])
            f.write(line + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, COMMANDS_TSV)


def _spawn(argv, note):
    """commands.tsv semantics: direct spawn, NO shell, stdio null,
    reaped by a waiter thread."""
    if not argv:
        return False
    try:
        env = dict(os.environ)
        env.pop("LD_LIBRARY_PATH", None)
        env.pop("SF_EXCLUSIVE", None)
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True, env=env)
        threading.Thread(target=lambda: proc.wait(), daemon=True).start()
        print(f"[wfd] act {note}: {' '.join(argv)} (pid {proc.pid})", flush=True)
        return True
    except Exception as e:
        print(f"[wfd] act {note} failed: {e!r}", flush=True)
        return False


def _notify(title, msg):
    if _darwin():
        try:
            subprocess.Popen(
                ["osascript", "-e",
                 'display notification "%s" with title "%s"'
                 % (_osa_esc(msg), _osa_esc(title))],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        except Exception:
            pass
        return
    if not (os.environ.get("DISPLAY") or
            os.environ.get("DBUS_SESSION_BUS_ADDRESS")):
        return
    try:
        subprocess.Popen(["notify-send", "-t", "10000", title, msg],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _purge_pending():
    dead = [k for k, v in PENDING.items()
            if now_ms() - v["at"] > CONFIRM_TTL_S * 1000]
    for k in dead:
        print(f"[wfd] confirm {k} expired ({PENDING[k]['gloss']})", flush=True)
        del PENDING[k]


def pending_rows():
    _purge_pending()
    return [{"id": k, "gloss": v["gloss"], "label": v["label"],
             "argv": v["argv"], "at_ms": v["at"]}
            for k, v in sorted(PENDING.items(), key=lambda kv: kv[1]["at"])]


def _route_provider(gloss, action, label, tier):
    """Bank-carried action dict -> act_iface provider: normalization,
    tier>=2 / first-use-argv gating and the focus guard all happen in
    that layer. A provider-held action lands in PENDING with provider=
    True so POST /act/confirm releases it via provider.confirm()."""
    if ACT_PROVIDER is None:
        return {"routed": False, "gloss": gloss,
                "refused": "no action provider on this box"}
    try:
        res = ACT_PROVIDER.run(dict(action),
                               {"gloss": gloss, "via": "sign",
                                "label": label})
    except Exception as e:
        print(f"[wfd] provider run {gloss}: {e!r}", flush=True)
        return {"routed": False, "gloss": gloss, "refused": str(e)}
    d = res.to_dict() if hasattr(res, "to_dict") else \
        {"ok": res.ok, "detail": res.detail, "tier": res.tier,
         "confirm_id": res.confirm_id}
    if res.confirm_id:
        _purge_pending()
        PENDING[res.confirm_id] = {"gloss": gloss, "label": label,
                                   "argv": [str(d.get("detail", ""))],
                                   "at": now_ms(), "provider": True}
        _notify("sign: %s needs confirm" % (label or gloss),
                "tier-%d action parked — approve in composer "
                "or POST /act/confirm" % res.tier)
        print(f"[wfd] parked tier{res.tier} {gloss} -> "
              f"{d.get('detail', '')} (confirm {res.confirm_id})",
              flush=True)
        return {"routed": True, "pending": res.confirm_id,
                "tier": res.tier, "detail": d.get("detail")}
    if res.ok:
        print(f"[wfd] fired {gloss}: {d.get('detail', '')}", flush=True)
        return {"routed": True, "fired": True, "tier": res.tier,
                "detail": d.get("detail")}
    print(f"[wfd] refused {gloss}: {d.get('detail', '')}", flush=True)
    return {"routed": False, "refused": d.get("detail"), "tier": res.tier}


def route_gloss(gloss, m_action=None, m_tier=0):
    """An accepted match -> bound action. The commands.tsv row wins the
    direct-spawn path; a bank-stored action OBJECT routes through the
    provider (catalog tools, context dispatch, macros, gated argv).
    tier>=2 parks as a pending confirm — enforced HERE, laptop-side,
    not by the composer."""
    row = commands().get(gloss)
    argv, tier, label = None, m_tier or 0, ""
    if row:
        argv, tier, label = row["argv"], row["tier"], row["label"]
    elif isinstance(m_action, dict):
        label = str(m_action.get("label", ""))
        try:
            tier = int(m_action.get("tier", tier))
        except (TypeError, ValueError):
            pass
        # dict actions go through the provider — kind:context, kind:macro,
        # catalog tools and run_argv all execute there with gating.
        return _route_provider(gloss, m_action, label, tier)
    elif isinstance(m_action, str) and m_action.strip():
        argv = m_action.split()
    if not argv:
        return {"routed": False, "gloss": gloss}
    if tier >= 2:
        _purge_pending()
        cid = secrets.token_hex(4)
        PENDING[cid] = {"gloss": gloss, "label": label, "argv": argv,
                        "at": now_ms()}
        _notify("sign: %s needs confirm" % (label or gloss),
                "tier-%d action parked — approve in composer "
                "or POST /act/confirm" % tier)
        print(f"[wfd] parked tier{tier} {gloss} -> {' '.join(argv)} "
              f"(confirm {cid})", flush=True)
        return {"routed": True, "pending": cid, "tier": tier}
    ok = _spawn(argv, gloss)
    return {"routed": ok, "fired": ok, "tier": tier}


# ---------------------------------------------------------------------
# signflow_ops composer — matches route through it BEFORE dispatch:
# compositional sequences (compositions.json), per-profile actions and
# observe/rehearsal mode all live in that session layer. It is pure
# logic; effects reach the OS through _ops_router below.
#
# Latency contract: ops.on_match runs synchronously (microseconds —
# dict lookups and list appends under its own lock), but EXECUTION is
# serialized on _ACT_Q. A macro with wait steps or a 100-char type_text
# must never stall the kp pipeline — fires run one at a time, in sign
# order, off the request thread. The router returns {"dispatched":True}
# so the ops' fire events stay honest about what is known when.
# ---------------------------------------------------------------------
_ACT_Q = queue.Queue()


def _act_worker():
    while True:
        job = _ACT_Q.get()
        try:
            job()
        except Exception as e:
            print(f"[wfd] act worker: {e!r}", flush=True)


threading.Thread(target=_act_worker, daemon=True).start()


def _feed(ev):
    """One event dict onto the /live/state feedback ring."""
    if isinstance(ev, dict):
        LIVE["feed"].append(ev)


def _ops_sink(ev):
    """Composer events -> /live/state feed. Would-fire and fire both
    update last_action (fire entries get exec results backfilled by
    _ops_exec; until then they read dispatched, not fired)."""
    if not isinstance(ev, dict):
        return
    t = ev.get("type")
    with LIVE_LOCK:
        _feed(ev)
        if t in ("fire", "would-fire"):
            LIVE["last_action"] = {
                "kind": ev.get("kind"), "glosses": ev.get("glosses"),
                "label": ev.get("label", ""),
                "action": ev.get("action"), "profile": ev.get("profile"),
                "observe": t == "would-fire",
                "ok": ev.get("ok"), "error": ev.get("error"),
                "dispatched": t == "fire", "t_ms": ev.get("t_ms")}


def _ops_exec(action, ctx):
    """Worker body: the resolved action reaches the OS through the same
    path a direct match uses — a single gloss reuses route_gloss (tsv
    direct-spawn or provider dict), a composition resolves through the
    provider. The result lands in /live/state last_action."""
    glosses = list(ctx.get("glosses") or [])
    label = str(ctx.get("label") or "")
    if len(glosses) == 1:
        res = route_gloss(glosses[0], action, 0)
    else:
        res = _route_provider("+".join(glosses) or "?",
                              action, label, 0)
    with LIVE_LOCK:
        la = {"kind": ctx.get("kind"), "glosses": glosses,
              "label": label, "profile": ctx.get("profile"),
              "observe": False, "dispatched": True,
              "result": res, "t_ms": now_ms()}
        la["ok"] = bool(res.get("fired") or
                        (res.get("routed") and not res.get("refused")))
        if res.get("pending"):
            la["pending"] = res["pending"]
        if res.get("refused"):
            la["error"] = res["refused"]
        LIVE["last_action"] = la
        _feed({"type": "fired", "glosses": glosses, "ok": la["ok"],
               "result": {k: res.get(k) for k in
                          ("routed", "fired", "pending", "refused",
                           "detail") if res.get(k) is not None},
               "t_ms": la["t_ms"]})


def _ops_router(action, ctx):
    """signflow_ops fire channel. Executions queue on _ACT_Q (serial,
    sign order) — the ops layer gets an honest dispatched marker back;
    last_action carries the real result when the worker lands it."""
    if not isinstance(action, dict) or not action:
        return {"ok": False, "error": "no resolved action"}
    _ACT_Q.put(lambda: _ops_exec(action, dict(ctx or {})))
    return {"ok": True, "dispatched": True}


OPS = None
if signflow_ops is not None:
    try:
        OPS = signflow_ops.SignFlowOps(
            BANK, action_router=_ops_router, event_sink=_ops_sink)
        OPS.set_observe(LIVE["observe"])
    except Exception as e:
        print(f"[wfd] signflow_ops unavailable: {e!r}", flush=True)
        OPS = None


def _on_segment(frames, t_ms):
    """A completed sign segment: teach captures it as an exemplar;
    a listening session matches it and routes the bound action."""
    if TEACH["active"]:
        ok = False
        if BANK is not None:
            try:
                ok = bool(BANK.add_exemplar(TEACH["name"], frames,
                                            TEACH["label"], TEACH["action"]))
            except Exception as e:
                print(f"[wfd] add_exemplar: {e!r}", flush=True)
        if ok:
            TEACH["got"] += 1
            TEACH["frames"].append(frames)
            TEACH["frames"] = TEACH["frames"][-5:]
        out = {"teach": {"name": TEACH["name"], "got": TEACH["got"],
                         "takes": TEACH["takes"], "ok": ok}}
        if TEACH["got"] >= TEACH["takes"]:
            save_bank()
            TEACH["active"] = False
            TEACH["session"] = None
            out["teach"]["done"] = True
        return out
    if not LIVE["listening"]:
        return {"seg": "idle"}            # signed while not listening
    res = {}
    if BANK is None:
        return res
    try:
        m = BANK.match(frames)
    except Exception as e:
        print(f"[wfd] match: {e!r}", flush=True)
        return res
    if m is None:
        return res
    def _finite(v):
        return v if (v is None or (isinstance(v, (int, float)) and math.isfinite(v))) else None
    last = {"gloss": m.gloss, "dist": _finite(m.dist), "at_ms": t_ms,
            "accepted": bool(m.accepted),
            "label": getattr(m, "label", "") or "",
            "score": _finite(getattr(m, "score", None)),
            "margin": _finite(getattr(m, "margin", None)),
            "second": getattr(m, "second_gloss", None),
            "reason": getattr(m, "reason", "")}
    # segment-split dedup: a mid-sign hesitation ends one segment and the
    # rest of the same movement re-arms immediately — without this the
    # halves both match and ONE gesture fires twice. A real repeat sign
    # pauses longer than the split gap (~0.45-0.55s), so same-gloss
    # segments reopening inside DUP_GAP_MS are suppressed (counted, fed).
    if m.accepted and m.gloss and frames:
        seg_start = int(frames[0].get("t_ms") or t_ms)
        seg_end = int(frames[-1].get("t_ms") or t_ms)
        prev = LIVE.get("last_accept") or {}
        if prev.get("gloss") == m.gloss and \
                seg_start - prev.get("seg_end", -10**9) < DUP_GAP_MS:
            LIVE["dup_fires"] = LIVE.get("dup_fires", 0) + 1
            LIVE["last_accept"] = {"gloss": m.gloss, "seg_end": seg_end}
            last["dup_suppressed"] = True
            _feed({"type": "dup-fire", "gloss": m.gloss,
                   "gap_ms": seg_start - prev.get("seg_end", 0),
                   "t_ms": t_ms})
            LIVE["last_match"] = last
            res["match"] = last
            return res
        LIVE["last_accept"] = {"gloss": m.gloss, "seg_end": seg_end}
    if m.accepted and m.gloss and LIVE.get("text_mode") \
            and not LIVE["observe"]:
        # TEXT mode (Go Live Text|Control toggle): the recognised sign's
        # word types into the laptop's focused window instead of
        # dispatching the bound action. The word is the sign's label
        # (what the user taught it as), falling back to the gloss.
        word = (last.get("label") or m.gloss or "").strip()
        if word:
            _ACT_Q.put(lambda w=word: send_text(w + " ", "live-text"))
        last["action"] = {"kind": "text", "typed": word,
                          "dispatched": bool(word)}
        res["action"] = last["action"]
        LIVE["last_action"] = {"kind": "text", "glosses": [m.gloss],
                               "label": last["label"], "typed": word,
                               "dispatched": bool(word), "t_ms": t_ms}
        _feed({"type": "type", "glosses": [m.gloss], "text": word,
               "t_ms": t_ms})
        LIVE["last_match"] = last
        res["match"] = last
        return res
    if OPS is not None:
        # composer decides: compositional sequences, profile-bound
        # actions, observe mode — and emits events the feed reports.
        try:
            evs = OPS.on_match(m, t_ms=t_ms) or []
            if evs:
                res["events"] = evs
        except Exception as e:
            print(f"[wfd] signflow_ops: {e!r}", flush=True)
    elif m.accepted and m.gloss:
        if LIVE["observe"]:
            # composer-less observe fallback: report, never fire
            last["action"] = {"would_fire": True,
                              "observe": True}
            res["action"] = last["action"]
            LIVE["last_action"] = {
                "kind": "would-fire", "glosses": [m.gloss],
                "label": last["label"], "observe": True,
                "dispatched": False, "t_ms": t_ms}
            _feed({"type": "would-fire", "glosses": [m.gloss],
                   "label": last["label"], "t_ms": t_ms})
        else:
            route = route_gloss(m.gloss, getattr(m, "action", None),
                                getattr(m, "tier", 0))
            if route.get("pending"):
                last["pending"] = route["pending"]
            last["action"] = route
            res["action"] = route
    LIVE["last_match"] = last
    res["match"] = last
    return res


def _norm_points(pts):
    """-> [{'x','y','score'}] list of len 21 or 42, else None."""
    if not isinstance(pts, list) or len(pts) not in (21, 42):
        return None
    out = []
    for p in pts:
        try:
            if isinstance(p, dict):
                s = p.get("score", 1.0)
                out.append({"x": float(p.get("x", 0.0)),
                            "y": float(p.get("y", 0.0)),
                            "score": 1.0 if s is None else float(s)})
            else:
                out.append({"x": float(p[0]), "y": float(p[1]),
                            "score": float(p[2]) if len(p) > 2 else 1.0})
        except (TypeError, ValueError, IndexError, KeyError):
            return None
    return out


def _rate_limited(key, now):
    """Per-identity token window + a global hard cap. Appends the current
    frame and returns True when THIS frame must be rejected.

    Identity = the stream's session id when present, else the peer address
    (two raw clients on one host share one budget). Rejected frames are not
    counted twice; the caller only appends once. Bounded client table.
    """
    rates = LIVE["rates"]
    dq = rates.get(key)
    if dq is None:
        if len(rates) >= LIVE_MAX_CLIENTS:
            old = min(rates, key=lambda k: rates[k][-1] if rates[k] else 0.0)
            rates.pop(old, None)
        dq = rates[key] = deque()
    dq.append(now)
    while dq and now - dq[0] > 1.0:
        dq.popleft()
    if len(dq) > LIVE_MAX_FPS * LIVE_BURST:
        return True
    allq = LIVE["rate_all"]
    allq.append(now)
    while allq and now - allq[0] > 1.0:
        allq.popleft()
    return len(allq) > LIVE_MAX_FPS * LIVE_BURST * 2


def live_kp(data, client=None):
    """POST /live/kp: append one keypoint frame to the session buffer."""
    if not isinstance(data, dict):
        return 400, {"ok": False, "error": "json object required"}
    pts = _norm_points(data.get("points"))
    if pts is None:
        return 400, {"ok": False,
                     "error": "points must be 21 or 42 {x,y,score}"}
    try:
        t_ms = int(data.get("t_ms", now_ms()))
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "bad t_ms"}
    now = time.time()
    with LIVE_LOCK:
        sid = data.get("session")
        if TEACH["active"]:
            # A teach owns its capture stream.  The first kp frame
            # carrying a session id claims it (or /teach/begin pre-binds
            # via its own 'session' field); frames from any OTHER session
            # are refused so a foreign streamer can neither cancel the
            # teach nor contaminate its exemplars.
            owner = TEACH.get("session")
            if sid and owner and str(sid)[:64] != owner:
                return 409, {"ok": False,
                             "error": "teach in progress",
                             "session": LIVE["session"]}
            if sid and owner is None:
                TEACH["session"] = str(sid)[:64]
                LIVE["session"] = TEACH["session"]
        elif (sid and sid != LIVE["session"]) or \
             (LIVE["seen"] and now - LIVE["seen"] > LIVE_IDLE_S):
            live_reset()
        key = ("sid:" + str(sid)[:64]) if sid else \
              ("ip:" + str(client or "local"))
        if _rate_limited(key, now):
            return 429, {"ok": False, "error": "rate limited"}
        if LIVE["ep"] is None:
            if endpointer is None:
                return 503, {"ok": False, "error": "endpointer unavailable"}
            LIVE["ep"] = endpointer.Endpointer(palm_sign=PALM_SIGN,
                                               namaste_sign=NAMASTE_SIGN)
        if not LIVE["session"]:
            LIVE["session"] = str(sid)[:64] if sid else secrets.token_hex(4)
        LIVE["seen"] = now
        frame = {"t_ms": t_ms, "points": pts}
        if "hand" in data:
            frame["hand"] = str(data["hand"])
        out = {"ok": True, "session": LIVE["session"]}
        try:
            events = LIVE["ep"].feed(frame) or []
        except Exception as e:
            print(f"[wfd] endpointer: {e!r}", flush=True)
            events = []
        for ev in events:
            if ev.get("type") == "mode":
                name = ev.get("name", "")
                was = LIVE["listening"]
                if name == PALM_SIGN:
                    LIVE["listening"] = True
                elif name == NAMASTE_SIGN:
                    LIVE["listening"] = False
                    if OPS is not None:
                        try:
                            OPS.flush()  # held prefix resolves at session end
                        except Exception:
                            pass
                # a hold that changes nothing (an open hand at rest while
                # already listening) must not replace the last sign reading
                if LIVE["listening"] != was:
                    LIVE["last_match"] = {
                        "gloss": name, "dist": 0.0, "at_ms": t_ms,
                        "accepted": True, "mode": True,
                        "label": "listening" if LIVE["listening"] else "stopped"}
                out["mode"] = name
            elif ev.get("type") == "segment":
                out.update(_on_segment(ev.get("frames") or [], t_ms))
            elif ev.get("type") == "segment-drop":
                LIVE["last_match"] = {
                    "gloss": "", "dist": None, "at_ms": t_ms,
                    "accepted": False, "mode": False, "label": "",
                    "reason": "dropped:" + str(ev.get("reason", ""))}
                out["dropped"] = ev.get("reason")
        if OPS is not None:
            try:
                OPS.tick(t_ms)      # frame cadence expires held prefixes
            except Exception:
                pass
        return 200, out


def _focus_info():
    """Best-effort focused-window report for /live/state + /status."""
    if ACT_PROVIDER is not None:
        try:
            f = ACT_PROVIDER.focused_app()
            if f is not None:
                return {"wm_class": getattr(f, "wm_class", "") or "",
                        "title": getattr(f, "title", "") or ""}
        except Exception:
            pass
    cls = active_window_class()
    return {"wm_class": cls or "", "title": ""}


def live_state():
    # The composer clock ticks on state polls too — a held prefix must
    # resolve even when no new frame arrives to drive _on_segment.
    if OPS is not None:
        try:
            OPS.tick()
        except Exception:
            pass
    with LIVE_LOCK:
        ep = LIVE["ep"]
        pend = pending_rows()
        if ACT_PROVIDER is not None:
            try:
                for p in ACT_PROVIDER.pending():
                    cid = p.get("confirm_id") or p.get("id")
                    if cid and cid not in PENDING:
                        pend.append({"id": cid,
                                     "gloss": p.get("gloss", ""),
                                     "label": p.get("describe",
                                                    p.get("label", "")),
                                     "argv": p.get("argv") or [],
                                     "at_ms": p.get("at_ms", 0)})
            except Exception:
                pass
        lm = LIVE["last_match"] or {}
        focus = _focus_info()
        st = {"listening": LIVE["listening"],
              "text_mode": bool(LIVE["text_mode"]),
              "session": LIVE["session"],
              "last_match": LIVE["last_match"],
              "reason": lm.get("reason", ""),
              "second_gloss": lm.get("second"),
              "margin": lm.get("margin"),
              "segment_len": ep.current_len if ep else 0,
              "signing": bool(ep and ep.active),
              "teach": {"active": TEACH["active"], "name": TEACH["name"],
                        "got": TEACH["got"], "takes": TEACH["takes"]},
              "pending": pend,
              "focus": focus.get("wm_class", ""),
              "focus_title": focus.get("title", ""),
              "observe_mode": bool(LIVE["observe"]),
              "last_action": LIVE["last_action"],
              "dup_fires": LIVE.get("dup_fires", 0),
              "feed": list(LIVE["feed"])[-16:],
              "bank_size": bank_size()}
        if OPS is not None:
            try:
                s = OPS.state()
                st["profile"] = s.get("profile")
                st["observe_mode"] = bool(s.get("observe",
                                              LIVE["observe"]))
                st["composition_in_progress"] = s.get("pending") or None
                st["pending_due_ms"] = s.get("pending_due_ms")
                st["n_compositions"] = s.get("n_compositions")
                st["profile_signs"] = s.get("profile_signs")
                st["ops"] = s.get("stats")
            except Exception:
                pass
        if record_action is not None:
            try:
                st["record"] = record_action.status()
            except Exception:
                pass
        return st


def _display_name():
    """What the composer shows as the laptop display backend."""
    if _darwin():
        return "macos"
    if sys.platform.startswith(("win32", "cygwin")):
        return "windows"
    return "x11" if os.environ.get("DISPLAY") else ""


def _status_payload():
    """GET /status — the composer status strip's feed: focused window,
    live-listening flag, last send, parked confirms."""
    with LIVE_LOCK:
        lst = LIVE["listening"]
        last = dict(LIVE["last_match"] or {})
    f = _focus_info()
    out = {"ok": True, "engine": detect_engine(),
           "display": _display_name(),
           "focus": f.get("wm_class", ""), "focus_title": f.get("title", ""),
           "live": {"on": True, "listening": lst,
                    "text_mode": bool(LIVE.get("text_mode"))},
           "last_send": LAST_SEND, "last_match": last or None,
           "confirms": live_state()["pending"],
           "bank_size": bank_size(),
           "name": os.uname().nodename}
    # Sidebar "connected laptop" row: a 200 reply already proves the
    # laptop link; this block is the compact descriptor the UI shows —
    # hostname, display, whether the action layer can really reach the
    # OS, and how many signs are taught.
    out["laptop"] = {"connected": True, "name": out["name"],
                     "display": out["display"],
                     "can_act": ACT_PROVIDER is not None,
                     "signs": out["bank_size"],
                     "engine": out["engine"]}
    return out


def _norm_gloss(s):
    return re.sub(r"[^A-Za-z0-9_.\-]+", "-", str(s or "")).strip("-").upper()


def teach_begin(data):
    if BANK is None:
        return 503, {"ok": False, "error": "signbank unavailable"}
    name = _norm_gloss(data.get("name"))
    if not name:
        return 400, {"ok": False, "error": "name required"}
    if name in (PALM_SIGN, NAMASTE_SIGN):
        return 400, {"ok": False,
                     "error": name + " is a reserved mode sign"}
    try:
        takes = max(1, min(int(data.get("takes", TEACH_TAKES)), 5))
    except (TypeError, ValueError):
        takes = TEACH_TAKES
    label = str(data.get("label", ""))[:120]
    action = data.get("action")
    row = commands().get(name)
    if action is None and row:            # pick up an existing binding
        action = {"argv": row["argv"], "tier": row["tier"],
                  "label": row["label"]}
        label = label or row["label"]
    with LIVE_LOCK:
        TEACH.update(active=True, name=name, takes=takes, got=0,
                     label=label, action=action, frames=[],
                     session=(str(data["session"])[:64]
                              if data.get("session") else None))
    return 200, {"ok": True, "name": name, "takes": takes}


def teach_end(_data):
    with LIVE_LOCK:
        was, got, name = TEACH["active"], TEACH["got"], TEACH["name"]
        takes_frames = list(TEACH.get("frames") or [])
        TEACH.update(active=False, name="", takes=0, got=0,
                     label="", action=None, session=None, frames=[])
    if got:
        save_bank()
    if not was and not got:
        return 409, {"ok": False, "error": "not teaching"}
    train = {"state": "needs_more", "takes": got,
             "checked": 0, "ok": 0, "worst_dist": None}
    if teach_train is not None and takes_frames and name:
        try:
            train = teach_train.verify(name, takes_frames)
        except Exception as e:
            train["error"] = str(e)[:120]
    return 200, {"ok": True, "name": name, "exemplars": got,
                 "done": got > 0, "train": train}


def bank_rows():
    """Merged library view: taught glosses joined with bindings."""
    cmds = commands()
    rows = {}
    if BANK is not None:
        try:
            entries = BANK.list()
        except Exception:
            entries = []
        for e in entries:
            g = str(e.get("gloss") or "")
            if not g:
                continue
            rows[g.upper()] = {"gloss": g, "label": e.get("label", ""),
                               "n_exemplars": e.get("n_exemplars", 0),
                               "exemplar_count": e.get("n_exemplars", 0),
                               "tier": e.get("tier", 0), "taught": True}
            a = e.get("action")
            if isinstance(a, dict):
                rows[g.upper()]["action"] = a
                rows[g.upper()]["bound"] = True
    for g, r in cmds.items():
        row = rows.setdefault(g, {"gloss": g, "taught": False,
                                  "n_exemplars": 0, "exemplar_count": 0})
        row.update({"label": r["label"], "argv": r["raw"],
                    "args": r["argv"], "tier": r["tier"]})
    for g, r in rows.items():
        r["bound"] = r.get("bound") or g in cmds
    return sorted(rows.values(), key=lambda x: x["gloss"])


def bank_put(data):
    """Create/update a sign row. Fields the wizard sends:
      gloss,label               — annotate/rename
      exemplars:[{frames:[..]}] — add performances to the bank
      add_exemplar:{frames:..}  — one performance
      rename:{gloss,label}      — retitle (gloss rename retargets the row)
      action:{...} | argv       — bind: dict -> provider routing,
                                  str/list -> commands.tsv spawn row
    Dict actions live ONLY in the bank (provider routes them with the
    tier/argv gates); plain argv keeps the commands.tsv fast path."""
    if not isinstance(data, dict):
        return 400, {"ok": False, "error": "json object required"}
    gloss = _norm_gloss(data.get("gloss"))
    if not gloss:
        return 400, {"ok": False, "error": "gloss required"}
    label = str(data.get("label", ""))[:120]
    rows = dict(commands())
    changed = False

    # --- exemplars into the DTW bank --------------------------------
    if BANK is not None:
        exs = data.get("exemplars")
        if isinstance(exs, list):
            added = 0
            for e in exs:
                frames = e.get("frames") if isinstance(e, dict) else None
                if not frames:
                    continue
                try:
                    if BANK.add_exemplar(gloss, frames,
                                         label=label or gloss):
                        added += 1
                except Exception as ex:
                    print(f"[wfd] add_exemplar {gloss}: {ex!r}",
                          flush=True)
            if added:
                changed = True
                print(f"[wfd] teach {gloss}: +{added} exemplar(s)",
                      flush=True)
        ae = data.get("add_exemplar")
        if isinstance(ae, dict) and ae.get("frames"):
            try:
                if BANK.add_exemplar(gloss, ae["frames"],
                                     label=label or gloss):
                    changed = True
            except Exception as ex:
                print(f"[wfd] add_exemplar {gloss}: {ex!r}", flush=True)
        if data.get("remove_exemplar"):
            # not needed by the wizard; kept out deliberately
            pass

    # --- rename: {rename:{gloss,label}} ------------------------------
    rn = data.get("rename")
    if isinstance(rn, dict):
        ngl = _norm_gloss(rn.get("gloss")) or gloss
        nlb = str(rn.get("label", label or ""))[:120]
        if ngl != gloss and BANK is not None:
            try:
                t = BANK._find(gloss)          # same-repo internal
                if t is not None:
                    t["gloss"] = ngl
                    t["label"] = nlb or t.get("label", "")
                    changed = True
            except Exception as ex:
                print(f"[wfd] rename {gloss}: {ex!r}", flush=True)
            if gloss in rows:
                rows[ngl] = rows.pop(gloss)
                rows[ngl]["label"] = nlb or rows[ngl].get("label", "")
                _write_commands(rows)
        gloss, label = ngl, nlb or label

    # --- binding: dict action (provider) vs argv (tsv) ---------------
    argv_in = data.get("argv")
    action_in = data.get("action")
    if argv_in is not None and not isinstance(argv_in, (str, list)):
        argv_in = None
    if action_in is not None and not isinstance(action_in, dict):
        # teach.js legacy shape: action may BE the argv string
        if isinstance(action_in, str):
            argv_in = action_in if argv_in is None else argv_in
            action_in = None
    try:
        tier = int(data.get("tier")) if data.get("tier") is not None else None
    except (TypeError, ValueError):
        tier = None
    if isinstance(action_in, dict):
        act = dict(action_in)
        if tier is not None:
            act["tier"] = tier
        if label:
            act["label"] = label
        if BANK is not None:
            try:
                BANK.set_action(gloss, label=label or None, action=act,
                                tier=act.get("tier"))
                changed = True
            except Exception as ex:
                print(f"[wfd] set_action {gloss}: {ex!r}", flush=True)
        else:
            return 503, {"ok": False, "error": "signbank unavailable"}
        # NB: no commands.tsv mirror — a dict with argv must route
        # through the provider's first-use gate, not the direct spawn.
    elif argv_in is not None:
        if isinstance(argv_in, list):
            argv = [str(a) for a in argv_in]
            raw = " ".join(argv)
        else:
            raw = str(argv_in)
            argv = raw.split()
        if not argv:
            return 400, {"ok": False, "error": "empty argv"}
        tt = tier if tier is not None else rows.get(gloss, {}).get("tier", 0)
        rows[gloss] = {"label": label or rows.get(gloss, {}).get("label", ""),
                       "argv": argv, "raw": raw, "tier": max(int(tt), 0)}
        _write_commands(rows)
        if BANK is not None:
            try:
                BANK.set_action(gloss, label=label or None,
                                action={"argv": argv, "tier": int(tt),
                                        "label": rows[gloss]["label"]})
                changed = True
            except Exception as ex:
                print(f"[wfd] set_action {gloss}: {ex!r}", flush=True)
    elif label:
        # annotation only
        if gloss in rows:
            r = dict(rows[gloss])
            r["label"] = label
            rows[gloss] = r
            _write_commands(rows)
        if BANK is not None:
            try:
                BANK.set_action(gloss, label=label)
                changed = True
            except Exception as ex:
                print(f"[wfd] label {gloss}: {ex!r}", flush=True)
    elif not changed:
        return 400, {"ok": False, "error":
                     "nothing to do (need exemplars, action, argv or label)"}
    if changed:
        save_bank()
    row = [r for r in bank_rows() if r["gloss"].upper() == gloss.upper()]
    return 200, {"ok": True, "sign": row[0] if row else {"gloss": gloss}}


def bank_delete(gloss):
    gloss = _norm_gloss(gloss)
    removed = False
    if gloss:
        if BANK is not None:
            try:
                removed = bool(BANK.remove(gloss))
                if removed:
                    save_bank()
            except Exception as e:
                print(f"[wfd] bank remove: {e!r}", flush=True)
        rows = dict(commands())
        if gloss in rows:
            rows.pop(gloss)
            _write_commands(rows)
            removed = True
    if not removed:
        return 404, {"ok": False, "error": "no such gloss"}
    return 200, {"ok": True, "removed": gloss}


def act_catalog():
    """GET /act: commands.tsv rows (the executable table) + bank-carried
    bindings they don't shadow, each with its risk tier."""
    cmds = commands()
    taught = {r["gloss"] for r in bank_rows()}
    rows = [{"gloss": g, "label": r["label"], "argv": r["raw"],
             "args": r["argv"], "tier": r["tier"],
             "confirm": r["tier"] >= 2, "taught": g in taught, "via": "tsv"}
            for g, r in sorted(cmds.items())]
    if BANK is not None:
        try:
            entries = BANK.list()
        except Exception:
            entries = []
        for e in entries:
            g = str(e.get("gloss") or "")
            a = e.get("action")
            if not g or g.upper() in cmds or not a:
                continue
            av = a.get("argv") if isinstance(a, dict) else a
            av = (" ".join(str(x) for x in av) if isinstance(av, list)
                  else str(av or ""))
            tier = e.get("tier", 0)
            rows.append({"gloss": g, "label": e.get("label", ""),
                         "argv": av, "args": av.split(), "tier": tier,
                         "confirm": tier >= 2, "taught": True,
                         "via": "bank"})
    return sorted(rows, key=lambda x: x["gloss"])


def detect_engine():
    """PRD §5.3: report which sign engine is serving. Override order:
    $WF_ENGINE, ~/.config/wf/engine, a rust binary on PATH, then the
    pure-python bank, else 'none'."""
    forced = os.environ.get("WF_ENGINE", "").strip()
    if forced:
        return forced
    try:
        with open(ENGINE_FILE) as f:
            v = f.read().strip()
        if v:
            return v
    except OSError:
        pass
    for cand in ("alive-app", "sign-flow", "alive"):
        if shutil.which(cand):
            return "rust-kp53"
    if signbank is not None:
        return "python-dtw"
    return "none"


def _finite(o):
    """Non-finite floats -> None, recursively (for strict JSON)."""
    if isinstance(o, float):
        return o if o == o and o not in (float("inf"), float("-inf")) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def authed(headers):
    return hmac.compare_digest(headers.get("X-WF-Key", ""), SECRET)


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, body=b"", ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods",
                         "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _jreply(self, code, obj):
        # strict JSON: Python writes inf/nan as Infinity/NaN, which
        # JSON.parse rejects — one infinite margin (a bank with a single
        # sign) made the whole /live/state reply unreadable to the app.
        return self._reply(code, json.dumps(_finite(obj)).encode(),
                           "application/json")

    def _proxied(self):
        """Forwarded headers => the request came through a tunnel/proxy.
        Same rule /pair-info uses — the live surface is LAN/USB only."""
        return any(h.lower().startswith(("cf-", "x-forwarded"))
                   for h in self.headers)

    def _gate(self):
        """Shared-key auth + proxied rejection for the live surface."""
        if not authed(self.headers):
            self._reply(403, b'{"ok":false,"error":"unpaired"}',
                        "application/json")
            return False
        if self._proxied():
            self._reply(403, b'{"ok":false,"error":"local only"}',
                        "application/json")
            return False
        return True

    def _read_body(self, cap):
        """Content-Length -> body, or None after replying 400/413."""
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            self._reply(400, b"bad content-length")
            return None
        if n < 0:
            n = 0
        if n > cap:
            self.close_connection = True
            self._reply(413, b'{"ok":false,"error":"body too large"}',
                        "application/json")
            return None
        return self.rfile.read(n) if n else b""

    def log_message(self, *a):
        pass

    def do_HEAD(self):
        # Connectivity probes (the app's laptop-connected check) must not
        # 501. Headers only, same status the GET would give: /health is
        # open, the live surface still requires the pairing key.
        p = self.path.split("?")[0]
        if p == "/health":
            return self._reply(200, b"", "text/plain")
        if p in ("/status", "/live/state", "/act", "/bank"):
            if not self._gate():
                return
            return self._reply(200, b"", "application/json")
        return self._reply(404, b"")

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
            out = json.dumps({"ip": lan_ip(), "ips": all_ips(), "port": BOUND_PORT,
                              "key": SECRET, "code": PAIR_CODE,
                              "name": os.uname().nodename}).encode()
            return self._reply(200, out, "application/json")
        # live-sign surface: key + LAN/USB only, before the open statics
        if p == "/live/state":
            if not self._gate():
                return
            return self._jreply(200, live_state())
        if p == "/bank":
            if not self._gate():
                return
            if BANK is None:
                return self._jreply(503, {"ok": False,
                                          "error": "signbank unavailable"})
            return self._jreply(200, {"ok": True, "signs": bank_rows()})
        if p == "/packs":
            if not self._gate():
                return
            if packs is None:
                return self._jreply(503, {"ok": False,
                                          "error": "packs unavailable"})
            return self._jreply(200, {"ok": True,
                                      "packs": packs.list_packs(bank_rows())})
        if p == "/windows":
            if not authed(self.headers):
                return self._reply(403, b'{"ok":false,"error":"unpaired"}',
                                   "application/json")
            return self._jreply(200, {"ok": True,
                                      "windows": list_windows()})
        if p == "/status":
            if not self._gate():
                return
            return self._jreply(200, _status_payload())
        if p == "/act":
            if not self._gate():
                return
            out = {"ok": True, "actions": act_catalog(),
                   "pending": live_state()["pending"],
                   "display": _display_name()}
            if ACT_PROVIDER is not None:
                try:
                    cat = ACT_PROVIDER.catalog() or {}
                    out["apps"] = cat.get("apps", [])
                    out["tools"] = cat.get("tools", [])
                    out["capabilities"] = cat.get("capabilities")
                    out["provider"] = cat.get("provider",
                                              cat.get("name", "x11"))
                    out["display"] = cat.get("display") or out["display"]
                except Exception as e:
                    out["provider_error"] = str(e)
            # merged capability table: provider tools (apps, win, type,
            # key, url, command) + system_ctl features (sys.*, media.*,
            # net.*, clipboard.*) as the picker's features rows.
            sys_caps = {}
            if system_ctl is not None:
                try:
                    sys_caps = system_ctl.capabilities() or {}
                    out["capabilities_system"] = sys_caps
                except Exception:
                    pass
            feats, seen = [], set()
            for r in (out.get("tools") or []):
                tid = r.get("id") or r.get("type")
                if not tid or tid in seen:
                    continue
                seen.add(tid)
                cap = sys_caps.get(tid) or {}
                feats.append({"type": tid,
                              "label": r.get("label", tid),
                              "tier": r.get("tier", cap.get("tier", 1)),
                              "kind": r.get("kind", cap.get("kind", "")),
                              "arg": r.get("arg"),
                              "available": cap.get("available", True),
                              "via": cap.get("via", [])})
            for tid, cap in sorted(sys_caps.items()):
                if tid in seen:
                    continue
                feats.append({"type": tid,
                              "label": cap.get("label", tid),
                              "tier": cap.get("tier", 1),
                              "kind": cap.get("kind", "system"),
                              "available": bool(cap.get("available")),
                              "via": cap.get("via", []),
                              "note": cap.get("note", "")})
            out["features"] = feats
            out["commands"] = out["actions"]   # alias: bound gloss->argv
            return self._jreply(200, out)
        if p.startswith("/act/icons/"):
            if not self._gate():
                return
            aid = urllib.parse.unquote(p[len("/act/icons/"):])
            # desktop-file-id charset only — '/', '..', '%2f' and any
            # traversal die here before the resolver is ever asked.
            if not re.match(r"^[A-Za-z0-9._+-]{1,200}$", aid):
                return self._reply(404, b"nope")
            path = None
            if ACT_PROVIDER is not None:
                try:
                    path = ACT_PROVIDER.icon_path(aid)
                except Exception:
                    path = None
            if not path or not os.path.isfile(path):
                return self._reply(404, b"no icon")
            try:
                with open(path, "rb") as f:
                    data = f.read(4 * 1024 * 1024 + 1)
            except OSError:
                return self._reply(404, b"no icon")
            if len(data) > 4 * 1024 * 1024:
                return self._reply(413, b"icon too large")
            # content-sniffed type only — we serve icons, not files
            head = data[:512]
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                ct = "image/png"
            elif head.startswith(b"\xff\xd8\xff"):
                ct = "image/jpeg"
            elif head[:6] in (b"GIF87a", b"GIF89a"):
                ct = "image/gif"
            elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                ct = "image/webp"
            elif b"<svg" in head or b"XPM" in head[:64]:
                ct = "image/svg+xml" if b"<svg" in head else "image/x-xpixmap"
            else:
                return self._reply(404, b"not an image")
            return self._reply(200, data, ct)
        if p == "/engine":
            if not self._gate():
                return
            with LIVE_LOCK:
                lst = LIVE["listening"]
            return self._jreply(200, {"engine": detect_engine(),
                                      "bank_size": bank_size(),
                                      "listening": lst,
                                      "signbank": signbank is not None})
        name = p.lstrip("/") or "index.html"
        path = os.path.normpath(os.path.join(WEB, name))
        if not path.startswith(WEB) or not os.path.isfile(path):
            return self._reply(404, b"nope")
        MIME = {".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript",
                ".mjs": "application/javascript",
                ".json": "application/json",
                ".webmanifest": "application/manifest+json",
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".ico": "image/x-icon",
                ".wasm": "application/wasm",
                ".task": "application/octet-stream",
                ".woff2": "font/woff2",
                ".md": "text/plain; charset=utf-8"}
        ct = MIME.get(os.path.splitext(name)[1].lower(),
                      "application/octet-stream")
        with open(path, "rb") as f:
            self._reply(200, f.read(), ct)

    def do_POST(self):
        p = self.path.split("?")[0]
        raw = self._read_body(BODY_CAPS.get(p, MAX_BODY))
        if raw is None:
            return

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
                if self.headers.get("X-WF-Window"):
                    focus_window(self.headers.get("X-WF-Window"))
                cls, mime = send_image(raw, self.headers.get("Content-Type", ""))
                out = json.dumps({"ok": True, "into": cls, "mime": mime,
                                  "kb": round(len(raw) / 1024)}).encode()
                return self._reply(200, out, "application/json")
            return self._reply(200, b'{"ok":false}', "application/json")
        if p == "/send-file":
            if raw:
                if self.headers.get("X-WF-Window"):
                    focus_window(self.headers.get("X-WF-Window"))
                # bug fix: header was never URL-decoded — "a%20b.txt" saved
                # literally. Decode first, then the safe-name regex runs.
                name = urllib.parse.unquote(
                    self.headers.get("X-File-Name", "file"))
                cls, path = send_file(raw, name, self.headers.get("Content-Type", ""))
                out = json.dumps({"ok": True, "into": cls, "path": path,
                                  "mb": round(len(raw) / 1048576, 1)}).encode()
                return self._reply(200, out, "application/json")
            return self._reply(200, b'{"ok":false}', "application/json")
        if p == "/send":
            try:
                data = json.loads(raw)
                text, mode = data.get("text", ""), data.get("mode", "type")
                if data.get("window"):
                    focus_window(data.get("window"))
            except Exception:
                text, mode = raw.decode("utf-8", "replace"), "type"
            if text:
                send_text(text, mode)
            return self._reply(200, b'{"ok":true,"into":"' +
                               active_window_class().encode() + b'"}', "application/json")

        # live-sign surface: LAN/USB only — never reachable via tunnel
        if p in LIVE_POSTS and self._proxied():
            return self._jreply(403, {"ok": False, "error": "local only"})
        if p == "/live/kp":
            try:
                data = json.loads(raw)
            except Exception:
                return self._jreply(400, {"ok": False, "error": "bad json"})
            code, obj = live_kp(data, client=self.client_address[0])
            return self._jreply(code, obj)
        if p == "/teach/begin":
            try:
                data = json.loads(raw or b"{}")
            except Exception:
                return self._jreply(400, {"ok": False, "error": "bad json"})
            code, obj = teach_begin(data)
            return self._jreply(code, obj)
        if p == "/teach/end":
            try:
                data = json.loads(raw or b"{}")
            except Exception:
                data = {}
            code, obj = teach_end(data)
            return self._jreply(code, obj)
        if p == "/packs/install":
            if packs is None or BANK is None:
                return self._jreply(503, {"ok": False,
                                          "error": "packs unavailable"})
            try:
                data = json.loads(raw or b"{}")
            except Exception:
                return self._jreply(400, {"ok": False, "error": "bad json"})
            code, obj = packs.install_pack(
                str(data.get("id", "")), bank_rows(), bank_put,
                lambda g, f, l: BANK.add_exemplar(g, f, label=l))
            if code == 200 and obj.get("ok"):
                save_bank()
            return self._jreply(code, obj)
        if p == "/live/listen":
            try:
                data = json.loads(raw or b"{}")
            except Exception:
                data = {}
            with LIVE_LOCK:
                on = bool(data.get("on", True))
                LIVE["listening"] = on
                if not on and OPS is not None:
                    try:
                        OPS.flush()      # resolve any held prefix now
                    except Exception:
                        pass
                LIVE["last_match"] = {
                    "gloss": PALM_SIGN if on else NAMASTE_SIGN,
                    "dist": 0.0, "at_ms": now_ms(), "accepted": True,
                    "mode": True,
                    "label": "listening" if on else "stopped"}
            return self._jreply(200, {"ok": True, "listening": on})
        if p == "/live/event":
            # composer-side gesture-meter events: palm_open -> start
            # listening, namaste -> stop. Same semantics as the
            # endpointer's mode events; idempotent.
            try:
                data = json.loads(raw or b"{}")
            except Exception:
                data = {}
            typ = str(data.get("type", ""))
            with LIVE_LOCK:
                if typ in ("palm_open", "listen", "on", "start"):
                    LIVE["listening"] = True
                elif typ in ("namaste", "stop", "off", "palm_closed"):
                    LIVE["listening"] = False
                    if OPS is not None:
                        try:
                            OPS.flush()
                        except Exception:
                            pass
                lst = LIVE["listening"]
            return self._jreply(200, {"ok": True, "listening": lst})
        if p == "/live/mode":
            # {"mode":"live"|"observe"} — observe computes + reports
            # everything the live path would do but never executes.
            try:
                data = json.loads(raw or b"{}")
            except Exception:
                data = {}
            mode = str(data.get("mode", "")).lower()
            # Go Live toggle: "text" = signs type words into the laptop's
            # focused window; "control" (alias "live") = signs dispatch
            # their bound laptop actions. "observe" reports, never fires.
            text_mode = None
            if mode in ("observe", "rehearse", "rehearsal"):
                observe = True
            elif mode in ("text", "type"):
                observe, text_mode = False, True
            elif mode in ("control",):
                observe, text_mode = False, False
            elif mode in ("live", "fire", "on", ""):
                observe = bool(data.get("observe", False)) \
                    if mode == "" else False
            else:
                return self._jreply(400, {"ok": False,
                                        "error": "mode must be text, control, live or observe"})
            with LIVE_LOCK:
                LIVE["observe"] = observe
                if text_mode is not None:
                    LIVE["text_mode"] = text_mode
            if OPS is not None:
                try:
                    OPS.set_observe(observe)
                except Exception as e:
                    print(f"[wfd] set_observe: {e!r}", flush=True)
            mstr = "observe" if observe else \
                   ("text" if LIVE["text_mode"] else "control")
            print(f"[wfd] mode -> {mstr}", flush=True)
            return self._jreply(200, {"ok": True, "mode": mstr,
                                      "observe": observe,
                                      "text_mode": LIVE["text_mode"]})
        if p == "/teach/record/start":
            # Teach-by-demonstration: capture real keystrokes/clicks via
            # xinput test-xi2 --root; /teach/record/stop returns the macro.
            if record_action is None:
                return self._jreply(503, {"ok": False,
                                          "error": "record_action unavailable"})
            try:
                data = json.loads(raw or b"{}")
            except Exception:
                data = {}
            name = str(data.get("name") or "").strip() or \
                "rec-%d" % int(time.time())
            try:
                name = record_action.sanitize_name(name)
            except Exception:
                return self._jreply(400, {"ok": False, "error": "bad name"})
            try:
                r = record_action.start(name)
            except Exception as e:
                return self._jreply(500, {"ok": False, "error": str(e)})
            if not r.get("ok"):
                reason = r.get("reason", "refused")
                code = 409 if reason == "already-recording" else 503
                return self._jreply(code, {"ok": False, "error": reason,
                                           "display": r.get("display"),
                                           "recording": False})
            r["recording"] = True
            r["name"] = name
            return self._jreply(200, r)
        if p == "/teach/record/stop":
            if record_action is None:
                return self._jreply(503, {"ok": False,
                                          "error": "record_action unavailable"})
            try:
                r = record_action.stop(save=True)
            except Exception as e:
                return self._jreply(500, {"ok": False, "error": str(e)})
            if not r.get("ok"):
                return self._jreply(409, {"ok": False,
                                          "error": r.get("reason", "refused")})
            # steps are the bindable macro — the UI PUTs `action` onto
            # a sign's bank row and the recording replays through the
            # provider's macro engine (tier>=2 stays gated).
            r["action"] = {"kind": "macro", "tier": 2,
                           "label": r.get("name", "recorded"),
                           "steps": r.get("steps", []),
                           "duration_ms": r.get("duration_ms", 0),
                           "context": r.get("context", {})}
            r["recording"] = False
            return self._jreply(200, r)
        if p == "/act/confirm":
            try:
                data = json.loads(raw)
            except Exception:
                return self._jreply(400, {"ok": False, "error": "bad json"})
            with LIVE_LOCK:
                _purge_pending()
                pend = PENDING.pop(str(data.get("id", "")), None)
            if pend is None:
                return self._jreply(404, {"ok": False,
                                          "error": "no such confirm"})
            if data.get("cancel"):
                if pend.get("provider") and ACT_PROVIDER is not None:
                    try:
                        ACT_PROVIDER.deny(str(data.get("id", "")))
                    except Exception:
                        pass
                print(f"[wfd] confirm cancelled: {pend['gloss']}", flush=True)
                return self._jreply(200, {"ok": True, "cancelled": True,
                                          "gloss": pend["gloss"]})
            if pend.get("provider") and ACT_PROVIDER is not None:
                try:
                    res = ACT_PROVIDER.confirm(str(data.get("id", "")))
                    d = res.to_dict() if hasattr(res, "to_dict") else \
                        {"ok": res.ok, "detail": res.detail}
                    ok = bool(res.ok)
                    return self._jreply(
                        200 if ok else 409,
                        {"ok": ok, "fired": ok, "gloss": pend["gloss"],
                         "detail": d.get("detail"), "result": d})
                except Exception as e:
                    return self._jreply(500, {"ok": False,
                                              "error": str(e)})
            ok = _spawn(pend["argv"], pend["gloss"])
            return self._jreply(200 if ok else 500,
                                {"ok": ok, "fired": ok,
                                 "gloss": pend["gloss"], "argv": pend["argv"]})
        return self._reply(404, b"nope")

    def do_PUT(self):
        p = self.path.split("?")[0]
        if p != "/bank":
            return self._reply(404, b"nope")
        if not self._gate():
            return
        raw = self._read_body(BODY_CAPS["/bank"])
        if raw is None:
            return
        try:
            data = json.loads(raw)
        except Exception:
            return self._jreply(400, {"ok": False, "error": "bad json"})
        code, obj = bank_put(data)
        return self._jreply(code, obj)

    def do_DELETE(self):
        p = self.path.split("?")[0]
        if p != "/bank":
            return self._reply(404, b"nope")
        if not self._gate():
            return
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        gloss = (q.get("gloss") or [""])[0]
        if not gloss:
            raw = self._read_body(64 * 1024)
            if raw is None:
                return
            try:
                gloss = str(json.loads(raw).get("gloss", ""))
            except Exception:
                gloss = ""
        code, obj = bank_delete(gloss)
        return self._jreply(code, obj)

    def do_OPTIONS(self):
        self._reply(204)


def serve(host, preferred, handler, tries=12):
    """Bind preferred port, falling back to the next free one. Returns
    (server, port) or (None, preferred) when the whole window is busy."""
    for p in range(preferred, preferred + tries):
        try:
            return ThreadingHTTPServer((host, p), handler), p
        except OSError:
            continue
    return None, preferred


if __name__ == "__main__":
    srv, BOUND_PORT = serve("0.0.0.0", PORT, Handler)
    if srv is None:
        print(f"[wfd] ports {PORT}-{PORT + 11} all busy — giving up",
              file=sys.stderr, flush=True)
        sys.exit(1)
    print(f"[wfd] listening on http://0.0.0.0:{BOUND_PORT} (key: {KEY_FILE})", flush=True)
    if BOUND_PORT != PORT:
        print(f"[wfd] port {PORT} was busy — fell back to {BOUND_PORT}", flush=True)
    print(f"[wfd] pair code: {PAIR_CODE} (valid until stop)", flush=True)
    srv.serve_forever()
