#!/usr/bin/env python3
"""wfd — Flow Composer daemon. Receives text/images/files/sign keypoints
from the phone app, injects them at the laptop cursor, and fires taught
signs' bound actions. Auth via shared pairing key; the live-sign surface
is LAN/USB only (never through a tunnel)."""
import hashlib, hmac, json, os, queue, re, secrets, shutil, string, subprocess, sys, tempfile, threading, time, urllib.parse
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

PORT = int(os.environ.get("WF_PORT", "8741"))
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
             "/send": 8 * 1024 * 1024, "/teach/begin": 64 * 1024,
             "/teach/end": 64 * 1024, "/act/confirm": 64 * 1024,
             "/bank": 256 * 1024}
# live pipeline knobs — env-tunable, not hidden constants
LIVE_MAX_FPS = int(os.environ.get("WF_LIVE_MAX_FPS", "45"))
LIVE_IDLE_S = float(os.environ.get("WF_LIVE_IDLE_S", "120"))
CONFIRM_TTL_S = float(os.environ.get("WF_CONFIRM_TTL", "120"))
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
    def job():
        subprocess.run(["xdotool", "type", "--clearmodifiers",
                        "--delay", "12", "--", text], timeout=600)
    _INJECT_Q.put(job)


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

    _INJECT_Q.put(job)


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

    _INJECT_Q.put(job)


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
        r = subprocess.run(["convert", src, png],
                           capture_output=True, timeout=15)
        if r.returncode == 0:
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


# ---------------------------------------------------------------------
# Live sign engine glue (PRD §5.2):
#   /live/kp frames -> endpointer -> signbank match -> commands.tsv argv.
# All live/teach/confirm state sits behind LIVE_LOCK. The daemon still
# runs as a pure text bridge when endpointer/signbank are absent — the
# imports are guarded and every use degrades honestly.
# ---------------------------------------------------------------------

LIVE_LOCK = threading.RLock()
LIVE = {"session": None, "listening": False, "ep": None,
        "last_match": None, "seen": 0.0, "rate": deque()}
TEACH = {"active": False, "name": "", "takes": 0, "got": 0,
         "label": "", "action": None}
PENDING = {}                    # confirm_id -> {gloss,label,argv,at}
_cmd_cache = {"key": None, "rows": {}}
LIVE_POSTS = ("/live/kp", "/teach/begin", "/teach/end", "/act/confirm")


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


def bank_size():
    if BANK is None:
        return 0
    try:
        return len(BANK.list())
    except Exception:
        return 0


def live_reset():
    """New listen session / idle expiry: clean segmenter + session id.
    A dangling teach is cancelled — a rotated session means the composer
    was re-opened."""
    LIVE.update(session=None, listening=False, last_match=None, seen=0.0)
    LIVE["rate"].clear()
    LIVE["ep"] = endpointer.Endpointer(
        palm_sign=PALM_SIGN, namaste_sign=NAMASTE_SIGN) if endpointer else None
    TEACH.update(active=False, name="", takes=0, got=0, label="", action=None)


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


def route_gloss(gloss, m_action=None, m_tier=0):
    """An accepted match -> bound action. The commands.tsv row wins; a
    bank-stored action dict is the fallback so imported banks carry
    bindings. tier>=2 parks as a pending confirm — enforced HERE,
    laptop-side, not by the composer."""
    row = commands().get(gloss)
    argv, tier, label = None, m_tier or 0, ""
    if row:
        argv, tier, label = row["argv"], row["tier"], row["label"]
    elif isinstance(m_action, dict):
        a = m_action.get("argv")
        argv = ([str(x) for x in a] if isinstance(a, list)
                else str(a).split() if a else None)
        try:
            tier = int(m_action.get("tier", tier))
        except (TypeError, ValueError):
            pass
        label = str(m_action.get("label", ""))
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
        out = {"teach": {"name": TEACH["name"], "got": TEACH["got"],
                         "takes": TEACH["takes"], "ok": ok}}
        if TEACH["got"] >= TEACH["takes"]:
            save_bank()
            TEACH["active"] = False
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
    last = {"gloss": m.gloss, "dist": m.dist, "at_ms": t_ms,
            "accepted": bool(m.accepted),
            "label": getattr(m, "label", "") or "",
            "score": getattr(m, "score", None),
            "margin": getattr(m, "margin", None),
            "second": getattr(m, "second_gloss", None),
            "reason": getattr(m, "reason", "")}
    if m.accepted and m.gloss:
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


def live_kp(data):
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
        if (sid and sid != LIVE["session"]) or \
           (LIVE["seen"] and now - LIVE["seen"] > LIVE_IDLE_S):
            live_reset()
        rate = LIVE["rate"]
        rate.append(now)
        while rate and now - rate[0] > 1.0:
            rate.popleft()
        if len(rate) > LIVE_MAX_FPS:
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
                if name == PALM_SIGN:
                    LIVE["listening"] = True
                elif name == NAMASTE_SIGN:
                    LIVE["listening"] = False
                LIVE["last_match"] = {
                    "gloss": name, "dist": 0.0, "at_ms": t_ms,
                    "accepted": True, "mode": True,
                    "label": "listening" if LIVE["listening"] else "stopped"}
                out["mode"] = name
            elif ev.get("type") == "segment":
                out.update(_on_segment(ev.get("frames") or [], t_ms))
        return 200, out


def live_state():
    with LIVE_LOCK:
        ep = LIVE["ep"]
        return {"listening": LIVE["listening"],
                "session": LIVE["session"],
                "last_match": LIVE["last_match"],
                "segment_len": ep.current_len if ep else 0,
                "signing": bool(ep and ep.active),
                "teach": {"active": TEACH["active"], "name": TEACH["name"],
                          "got": TEACH["got"], "takes": TEACH["takes"]},
                "pending": pending_rows(),
                "bank_size": bank_size()}


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
                     label=label, action=action)
    return 200, {"ok": True, "name": name, "takes": takes}


def teach_end(_data):
    with LIVE_LOCK:
        was, got, name = TEACH["active"], TEACH["got"], TEACH["name"]
        TEACH.update(active=False, name="", takes=0, got=0,
                     label="", action=None)
    if got:
        save_bank()
    if not was and not got:
        return 409, {"ok": False, "error": "not teaching"}
    return 200, {"ok": True, "name": name, "exemplars": got,
                 "done": got > 0}


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
                               "tier": e.get("tier", 0), "taught": True}
    for g, r in cmds.items():
        row = rows.setdefault(g, {"gloss": g, "taught": False,
                                  "n_exemplars": 0})
        row.update({"label": r["label"], "argv": r["raw"],
                    "args": r["argv"], "tier": r["tier"]})
    for g, r in rows.items():
        r["bound"] = g in cmds
    return sorted(rows.values(), key=lambda x: x["gloss"])


def bank_put(data):
    """Bind/annotate a sign: writes the commands.tsv row (the live
    routing source) and mirrors it into the bank via set_action so an
    exported signbank.json carries its bindings."""
    if not isinstance(data, dict):
        return 400, {"ok": False, "error": "json object required"}
    gloss = _norm_gloss(data.get("gloss"))
    if not gloss:
        return 400, {"ok": False, "error": "gloss required"}
    label = str(data.get("label", ""))[:120]
    argv_in = data.get("argv", data.get("action"))
    rows = dict(commands())
    if argv_in is None:
        if gloss not in rows and not label:
            return 400, {"ok": False, "error": "argv required to bind"}
    else:
        if isinstance(argv_in, list):
            argv = [str(a) for a in argv_in]
            raw = " ".join(argv)
        else:
            raw = str(argv_in)
            argv = raw.split()
        if not argv:
            return 400, {"ok": False, "error": "empty argv"}
        try:
            tier = int(data.get("tier")) if data.get("tier") is not None \
                else rows.get(gloss, {}).get("tier", 0)
        except (TypeError, ValueError):
            tier = 0
        rows[gloss] = {"label": label or rows.get(gloss, {}).get("label", ""),
                       "argv": argv, "raw": raw, "tier": max(tier, 0)}
        _write_commands(rows)
    if label and argv_in is None and gloss in rows:
        r = dict(rows[gloss])
        r["label"] = label
        rows[gloss] = r
        _write_commands(rows)
    if BANK is not None:
        try:
            rr = commands().get(gloss)
            act = ({"argv": rr["argv"], "tier": rr["tier"],
                    "label": rr["label"]} if rr else None)
            BANK.set_action(gloss, label=label or None, action=act)
            save_bank()
        except Exception as e:
            print(f"[wfd] set_action: {e!r}", flush=True)
    row = [r for r in bank_rows() if r["gloss"] == gloss]
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
        return self._reply(code, json.dumps(obj).encode(), "application/json")

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
        if p == "/act":
            if not self._gate():
                return
            return self._jreply(200, {"ok": True, "actions": act_catalog(),
                                      "pending": pending_rows()})
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
        ct = "text/html; charset=utf-8" if name.endswith(".html") else \
             "application/javascript" if name.endswith(".js") else \
             "application/manifest+json" if name.endswith(".webmanifest") else \
             "image/svg+xml" if name.endswith(".svg") else "application/octet-stream"
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
                cls, mime = send_image(raw, self.headers.get("Content-Type", ""))
                out = json.dumps({"ok": True, "into": cls, "mime": mime,
                                  "kb": round(len(raw) / 1024)}).encode()
                return self._reply(200, out, "application/json")
            return self._reply(200, b'{"ok":false}', "application/json")
        if p == "/send-file":
            if raw:
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
            code, obj = live_kp(data)
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
                print(f"[wfd] confirm cancelled: {pend['gloss']}", flush=True)
                return self._jreply(200, {"ok": True, "cancelled": True,
                                          "gloss": pend["gloss"]})
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


if __name__ == "__main__":
    print(f"[wfd] listening on http://0.0.0.0:{PORT} (key: {KEY_FILE})", flush=True)
    print(f"[wfd] pair code: {PAIR_CODE} (valid until stop)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
