#!/usr/bin/env python3
"""system_ctl — deep-OS Linux system actions for the merged product.

Founder addendum 17:04 ("go deep into the OS"): the @ picker's system set,
each with a REAL implementation plus honest capability detection — the box
may not have the tool, in which case we say `not available` and name what
would provide it, instead of faking a failure later at fire time.

Implements the sys/media/net/clip kinds of the /act catalog
(ops/APP_API_CONTRACT.md v2):

    sys.volume-up / sys.volume-down / sys.mute     pactl -> amixer
    sys.brightness-up / sys.brightness-down        brightnessctl -> /sys/class/backlight
    media.playpause / media.next / media.prev      playerctl -> raw dbus-send MPRIS
    sys.screenshot / sys.screenshot-region         import -> gnome-screenshot -> spectacle -> scrot
    sys.lock                                        loginctl -> xdg-screensaver
    sys.dnd                                         gsettings show-banners / freedesktop
                                                    Notifications Inhibited (DE-dependent)
    net.wifi / net.bluetooth (arg on|off|toggle)    nmcli -> rfkill / bluetoothctl -> rfkill
    clipboard.history (arg index)                   copyq -> clipster -> klipper(dbus)
                                                    -> live clipboard only, index 0

Spawn rules (PRD §6): argv only, never a shell; every subprocess.run has
timeout=5; stderr is captured into ActionResult.detail so a failed action
says WHY. Wayland is detected via XDG_SESSION_TYPE and each X11-bound action
carries the warning in its note/detail rather than failing silently.

The ActionProvider contract (server/act_iface.py, W9's lane — imported
guarded so this module works standalone and in tests):
    run(action, ctx) -> ActionResult(ok, detail, tier, confirm_id)
    TIERS = {0,1,2}; tier>=2 confirm + first-seen-argv ledger live in the
    dispatcher layer (act_linux), NOT here — we always surface the tier.

Run `python3 system_ctl.py` to print this box's real capability table.
"""
import json
import os
import re
import shutil
import subprocess
import time

try:                       # W9's lane lands concurrently — code against it
    from act_iface import ActionResult, TIERS
except ImportError:        # standalone fallback, same field contract
    TIERS = frozenset((0, 1, 2))

    class ActionResult:
        __slots__ = ("ok", "detail", "tier", "confirm_id")

        def __init__(self, ok=False, detail="", tier=0, confirm_id=None):
            self.ok, self.detail, self.tier, self.confirm_id = (
                ok, detail, tier, confirm_id)

        def as_dict(self):
            return {"ok": self.ok, "detail": self.detail,
                    "tier": self.tier, "confirm_id": self.confirm_id}

SPAWN_TIMEOUT = 5
BACKLIGHT_DIR = os.environ.get("WF_BACKLIGHT_DIR", "/sys/class/backlight")

VOLUME_STEP_PCT = 5
BRIGHTNESS_STEP_PCT = 10

# tool-id suffix -> (implementation, default tier). Tier matches contract v2.
DISPATCH = {}  # filled after the impls; keys are the part after the last dot
TOOL_TIERS = {
    "sys.volume-up": 1, "sys.volume-down": 1, "sys.mute": 1,
    "sys.brightness-up": 1, "sys.brightness-down": 1,
    "sys.screenshot": 1, "sys.screenshot-region": 1,
    "sys.lock": 2, "sys.dnd": 1,
    "media.playpause": 1, "media.play": 1, "media.pause": 1,
    "media.next": 1, "media.prev": 1,
    "net.wifi": 2, "net.bluetooth": 2,
    "clipboard.history": 1, "clipboard.targets": 1,
}

WAYLAND_NOTE = ("wayland session: X11 tools (import/scrot/xclip/xdotool/"
                "xdg-screensaver) degrade or fail there — prefer portal/"
                "dbus paths when this fires on Wayland")


# ------------------------------------------------------------------
# spawn + session helpers
# ------------------------------------------------------------------

def _which(name):
    return shutil.which(name)


def _run(argv, input_text=None):
    """argv-only spawn, timeout=SPAWN_TIMEOUT, stderr -> detail.

    Returns (ok, detail). `detail` is the captured stderr (or stdout when
    stderr is empty) trimmed to 400 chars — enough for pactl/nmcli errors.
    """
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           input=input_text, timeout=SPAWN_TIMEOUT)
    except FileNotFoundError:
        return False, "%s: not installed" % argv[0]
    except subprocess.TimeoutExpired:
        return False, "%s: timed out after %ds" % (argv[0], SPAWN_TIMEOUT)
    except OSError as e:
        return False, "%s: spawn failed: %s" % (argv[0], e)
    detail = (p.stderr.strip() or p.stdout.strip() or "ok")[:400]
    return p.returncode == 0, detail


def _out(argv):
    """Like _run but returns (ok, stdout, detail) for query commands."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           timeout=SPAWN_TIMEOUT)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        return False, "", str(e)[:200]
    return p.returncode == 0, p.stdout, (p.stderr.strip() or "")[:200]


def session_type():
    return os.environ.get("XDG_SESSION_TYPE") or "unknown"


def on_wayland():
    return session_type().lower() == "wayland"


def _wayland_suffix():
    return " | " + WAYLAND_NOTE if on_wayland() else ""


# ------------------------------------------------------------------
# volume / mute — pactl, amixer fallback
# ------------------------------------------------------------------

def _volume(direction, step=VOLUME_STEP_PCT):
    pactl = _which("pactl")
    if pactl:
        delta = "+%d%%" % step if direction == "up" else "-%d%%" % step
        ok, det = _run([pactl, "set-sink-volume", "@DEFAULT_SINK@", delta])
        return (ok, "pactl %s" % delta) if ok else (False, det)
    amixer = _which("amixer")
    if amixer:
        delta = "%d%%+" % step if direction == "up" else "%d%%-" % step
        ok, det = _run([amixer, "-q", "set", "Master", delta])
        return (ok, "amixer Master %s" % delta) if ok else (False, det)
    return False, "not available: need pactl (PipeWire/PulseAudio) or amixer (ALSA)"


def _mute():
    pactl = _which("pactl")
    if pactl:
        ok, det = _run([pactl, "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
        return (ok, "pactl mute toggle") if ok else (False, det)
    amixer = _which("amixer")
    if amixer:
        ok, det = _run([amixer, "-q", "set", "Master", "toggle"])
        return (ok, "amixer Master toggle") if ok else (False, det)
    return False, "not available: need pactl or amixer"


# ------------------------------------------------------------------
# brightness — brightnessctl, /sys/class/backlight fallback
# ------------------------------------------------------------------

def _backlight_path(name):
    return os.path.join(BACKLIGHT_DIR, name)


def _brightness(direction, step=BRIGHTNESS_STEP_PCT):
    bctl = _which("brightnessctl")
    if bctl:
        delta = "+%d%%" % step if direction == "up" else "%d%%-" % step
        ok, det = _run([bctl, "set", delta])
        return (ok, "brightnessctl %s" % delta) if ok else (False, det)
    try:
        devs = sorted(d for d in os.listdir(BACKLIGHT_DIR)
                      if os.path.isdir(_backlight_path(d)))
    except OSError:
        devs = []
    if not devs:
        return False, ("not available: need brightnessctl or a writable "
                       "%s/<dev>/brightness" % BACKLIGHT_DIR)
    dev = devs[0]
    try:
        maxb = int(open(_backlight_path(os.path.join(dev, "max_brightness"))).read())
        cur = int(open(_backlight_path(os.path.join(dev, "brightness"))).read())
    except (OSError, ValueError) as e:
        return False, "backlight %s unreadable: %s" % (dev, e)
    delta = max(1, maxb * step // 100)
    new = cur + delta if direction == "up" else cur - delta
    new = max(1, min(maxb, new))
    try:
        with open(_backlight_path(os.path.join(dev, "brightness")), "w") as f:
            f.write(str(new))
    except OSError as e:
        # honest: most distros need udev rules/root for raw sysfs writes
        return False, "backlight %s write failed: %s (needs udev rule or root)" % (dev, e)
    return True, "sysfs %s %d -> %d (of %d)" % (dev, cur, new, maxb)


# ------------------------------------------------------------------
# media keys — MPRIS. playerctl preferred, raw dbus-send fallback.
# ------------------------------------------------------------------

_MPRIS = {"playpause": "PlayPause", "play": "Play", "pause": "Pause",
          "next": "Next", "prev": "Previous"}
_PLAYERCTL_ARG = {"playpause": "play-pause", "play": "play", "pause": "pause",
                  "next": "next", "prev": "previous"}


def _mpris_bus_name(dbus_send):
    """First org.mpris.MediaPlayer2.* name on the session bus, or ''."""
    ok, out, _ = _out([dbus_send, "--session", "--print-reply",
                       "--dest=org.freedesktop.DBus",
                       "/org/freedesktop/DBus",
                       "org.freedesktop.DBus.ListNames"])
    if not ok:
        return ""
    names = sorted(re.findall(r'"(org\.mpris\.MediaPlayer2\.[^"]+)"', out))
    return names[0] if names else ""


def _media(sub):
    pc = _which("playerctl")
    if pc:
        ok, det = _run([pc, _PLAYERCTL_ARG[sub]])
        return (ok, "playerctl %s" % _PLAYERCTL_ARG[sub]) if ok else (False, det)
    dbus = _which("dbus-send")
    if not dbus:
        return False, "not available: need playerctl or dbus-send (MPRIS)"
    name = _mpris_bus_name(dbus)
    if not name:
        return False, ("dbus-send present but no org.mpris.MediaPlayer2.* "
                       "player on the session bus")
    ok, det = _run([dbus, "--session", "--print-reply", "--dest=" + name,
                    "/org/mpris/MediaPlayer2",
                    "org.mpris.MediaPlayer2.Player." + _MPRIS[sub]])
    return (ok, "mpris %s -> %s" % (_MPRIS[sub], name)) if ok else (False, det)


# ------------------------------------------------------------------
# screenshot — import / gnome-screenshot / spectacle / scrot
# ------------------------------------------------------------------

def _shot_dir():
    pics = os.path.expanduser("~/Pictures")
    return pics if os.path.isdir(pics) else os.path.expanduser("~")


def _screenshot_tool(region):
    """First available tool -> (name, argv_template) with {} for the path."""
    table = [
        ("import", ["-window", "root", "{p}"] if not region else ["{p}"]),
        ("gnome-screenshot", ["-a", "-f", "{p}"] if region else ["-f", "{p}"]),
        ("spectacle", ["-r", "-b", "-n", "-o", "{p}"] if region
                      else ["-b", "-n", "-o", "{p}"]),
        ("scrot", ["-s", "{p}"] if region else ["{p}"]),
    ]
    for name, tmpl in table:
        path = _which(name)
        if path:
            return path, [path] + tmpl
    return None, None


def _screenshot(region):
    path, tmpl = _screenshot_tool(region)
    if not path:
        return False, ("not available: need one of import, gnome-screenshot, "
                       "spectacle, scrot" + _wayland_suffix())
    out = os.path.join(_shot_dir(),
                       time.strftime("screenshot-%Y%m%d-%H%M%S.png"))
    argv = [out if t == "{p}" else t for t in tmpl]
    ok, det = _run(argv)
    if ok:
        return True, "%s -> %s%s" % (os.path.basename(path), out,
                                     _wayland_suffix())
    return False, det + _wayland_suffix()


# ------------------------------------------------------------------
# screen lock — loginctl lock-session, xdg-screensaver fallback
# ------------------------------------------------------------------

def _lock():
    li = _which("loginctl")
    if li:
        argv = [li, "lock-session"]
        sess = os.environ.get("XDG_SESSION_ID")
        if sess:
            argv.append(sess)
        ok, det = _run(argv)
        if ok:
            return True, "loginctl lock-session"
        # fall through — e.g. session not under logind
        last = det
    else:
        last = ""
    xss = _which("xdg-screensaver")
    xdot = _which("xdotool")
    if xss and xdot:
        ok2, wid, _ = _out([xdot, "getactivewindow"])
        if ok2 and wid.strip().isdigit():
            ok, det = _run([xss, "lock", wid.strip()])
            if ok:
                return True, "xdg-screensaver lock" + _wayland_suffix()
            last = det
        else:
            last = "xdg-screensaver needs an active X11 window id; xdotool gave none"
    elif xss:
        last = "xdg-screensaver present but needs xdotool for the window id"
    if last:
        return False, last
    return False, "not available: need loginctl or xdg-screensaver+xdotool"


# ------------------------------------------------------------------
# do-not-disturb — gsettings show-banners / freedesktop Notifications
# Inhibited. Honest caveat: this is DE-dependent.
# ------------------------------------------------------------------

def _dnd(arg=None):
    gs = _which("gsettings")
    if gs:
        ok, out, _ = _out([gs, "get", "org.gnome.desktop.notifications",
                           "show-banners"])
        if ok and out.strip() in ("true", "false"):
            want = arg if arg in ("on", "off") else None
            cur_on = out.strip() == "true"
            # DND on  <=>  show-banners false
            new_banners = "false" if (want == "on" or
                                      (want is None and cur_on)) else "true"
            ok, det = _run([gs, "set", "org.gnome.desktop.notifications",
                            "show-banners", new_banners])
            if ok:
                return True, ("gnome show-banners -> %s (dnd %s)"
                              % (new_banners, "on" if new_banners == "false" else "off"))
    dbus = _which("dbus-send")
    if dbus:
        ok, out, _ = _out([dbus, "--session", "--print-reply",
                           "--dest=org.freedesktop.Notifications",
                           "/org/freedesktop/Notifications",
                           "org.freedesktop.DBus.Properties.Get",
                           'string:org.freedesktop.Notifications',
                           'string:Inhibited'])
        m = re.search(r"boolean\s+(true|false)", out)
        if ok and m:
            want = arg if arg in ("on", "off") else None
            cur = m.group(1) == "true"
            new = want == "on" if want else not cur
            ok, det = _run([dbus, "--session",
                            "--dest=org.freedesktop.Notifications",
                            "/org/freedesktop/Notifications",
                            "org.freedesktop.DBus.Properties.Set",
                            'string:org.freedesktop.Notifications',
                            'string:Inhibited',
                            'variant:boolean:%s' % ("true" if new else "false")])
            if ok:
                return True, "freedesktop Notifications.Inhibited -> %s" % new
            return False, det
    return False, ("not available / DE-dependent: tried gsettings "
                   "org.gnome.desktop.notifications and freedesktop "
                   "Notifications.Inhibited — this DE exposes neither "
                   "(KDE uses its own plasmashell toggle)")


# ------------------------------------------------------------------
# wifi / bluetooth — nmcli / bluetoothctl, rfkill fallback
# ------------------------------------------------------------------

def _rfkill_toggle(kind, mode):
    rf = _which("rfkill")
    if not rf:
        return False, ""
    if mode == "toggle":
        ok, out, _ = _out([rf, "list", kind])
        blocked = "Soft blocked: yes" in out
        mode = "off" if not blocked else "on"
    op = "block" if mode == "off" else "unblock"
    ok, det = _run([rf, op, kind])
    return (ok, "rfkill %s %s" % (op, kind)) if ok else (False, det)


def _wifi(mode):
    nm = _which("nmcli")
    if nm:
        if mode == "toggle":
            ok, out, _ = _out([nm, "radio", "wifi"])
            mode = "off" if "enabled" in out.lower() else "on"
        ok, det = _run([nm, "radio", "wifi", mode])
        return (ok, "nmcli radio wifi %s" % mode) if ok else (False, det)
    ok, det = _rfkill_toggle("wifi", mode)
    if ok:
        return ok, det
    return False, ("not available: need nmcli (NetworkManager) or rfkill"
                   + ("; rfkill said: " + det if det else ""))


def _bluetooth(mode):
    bt = _which("bluetoothctl")
    if bt:
        if mode == "toggle":
            ok, out, _ = _out([bt, "show"])
            powered = re.search(r"Powered:\s*yes", out or "")
            mode = "off" if powered else "on"
        ok, det = _run([bt, "power", mode])
        return (ok, "bluetoothctl power %s" % mode) if ok else (False, det)
    ok, det = _rfkill_toggle("bluetooth", mode)
    if ok:
        return ok, det
    return False, ("not available: need bluetoothctl (bluez) or rfkill"
                   + ("; rfkill said: " + det if det else ""))


# ------------------------------------------------------------------
# clipboard history — copyq / clipster / klipper; else honest fallback
# ------------------------------------------------------------------

def _clip_targets():
    xc = _which("xclip")
    if not xc:
        return []
    ok, out, _ = _out([xc, "-selection", "clipboard", "-t", "TARGETS", "-o"])
    return out.split() if ok else []


def _clip_read_index(index):
    """Fetch history item `index` (0 = newest) into the live clipboard.

    Returns (ok, detail)."""
    index = int(index or 0)
    copyq = _which("copyq")
    if copyq:
        ok, out, det = _out([copyq, "read", str(index)])
        if ok:
            return _clip_in(out.rstrip("\n"), "copyq item %d" % index)
        return False, "copyq: " + det
    clipster = _which("clipster")
    if clipster:
        # clipster -o prints history newest-first, one entry per line block;
        # -n N limits to N entries
        ok, out, det = _out([clipster, "-s", "clipboard", "-o",
                             "-n", str(index + 1)])
        if ok:
            entries = out.rstrip("\n").split("\n")
            if index < len(entries):
                return _clip_in(entries[-1], "clipster item %d" % index)
            return False, "clipster: history has %d item(s)" % len(entries)
        return False, "clipster: " + det
    dbus = _which("dbus-send")
    if dbus:
        ok, out, det = _out([dbus, "--session", "--print-reply",
                             "--dest=org.kde.klipper", "/klipper",
                             "org.kde.klipper.klipper.getClipboardHistoryItem",
                             "int32:%d" % index])
        if ok:
            m = re.search(r'string\s"(.*)"', out, re.S)
            if m:
                return _clip_in(m.group(1), "klipper item %d" % index)
            return False, "klipper: empty history item %d" % index
    if index == 0:
        xc = _which("xclip")
        if xc:
            ok, out, det = _out([xc, "-selection", "clipboard", "-o"])
            if ok:
                tgts = _clip_targets()
                return True, ("live clipboard (no manager — only index 0 "
                              "exists); targets=%s" % (",".join(tgts) or "?"))
            return False, "xclip read failed: " + det + _wayland_suffix()
    return False, ("not available: no clipboard manager (copyq/clipster/"
                   "klipper) — only the live clipboard exists; install "
                   "copyq or clipster for history")


def _clip_in(text, via):
    xc = _which("xclip")
    if not xc:
        return False, ("%s fetched but xclip missing — cannot load clipboard"
                       % via + _wayland_suffix())
    try:
        subprocess.run([xc, "-selection", "clipboard", "-i"], input=text,
                       text=True, timeout=SPAWN_TIMEOUT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, "xclip load failed: %s" % e
    return True, "clipboard <- %s (press ctrl+v)%s" % (via, _wayland_suffix())


# ------------------------------------------------------------------
# dispatch + capability table
# ------------------------------------------------------------------

def _arg(args, *names, default=None):
    for n in names:
        if isinstance(args, dict) and n in args:
            return args[n]
    return default


def dispatch(tool, args=None):
    """tool = catalog id ('sys.mute') or bare suffix ('mute'). -> (ok, detail)."""
    args = args or {}
    key = str(tool).split(".")[-1]
    fn = DISPATCH.get(key)
    if not fn:
        return False, "unknown system tool: %s" % tool
    return fn(args)


DISPATCH.update({
    "volume-up":        lambda a: _volume("up", int(_arg(a, "step", default=VOLUME_STEP_PCT))),
    "volume-down":      lambda a: _volume("down", int(_arg(a, "step", default=VOLUME_STEP_PCT))),
    "mute":             lambda a: _mute(),
    "brightness-up":    lambda a: _brightness("up", int(_arg(a, "step", default=BRIGHTNESS_STEP_PCT))),
    "brightness-down":  lambda a: _brightness("down", int(_arg(a, "step", default=BRIGHTNESS_STEP_PCT))),
    "playpause":        lambda a: _media("playpause"),
    "play":             lambda a: _media("play"),
    "pause":            lambda a: _media("pause"),
    "next":             lambda a: _media("next"),
    "prev":             lambda a: _media("prev"),
    "screenshot":       lambda a: _screenshot(False),
    "screenshot-region": lambda a: _screenshot(True),
    "lock":             lambda a: _lock(),
    "dnd":              lambda a: _dnd(_arg(a, "mode", "state")),
    "wifi":             lambda a: _wifi(str(_arg(a, "mode", "state", "arg", default="toggle")).lower()),
    "bluetooth":        lambda a: _bluetooth(str(_arg(a, "mode", "state", "arg", default="toggle")).lower()),
    "history":          lambda a: _clip_read_index(_arg(a, "index", default=0)),
    "targets":          lambda a: (True, "targets=%s" % ",".join(_clip_targets()))
                                  if _which("xclip") else
                                  (False, "not available: need xclip"),
})

# which backends each action could use, for the `available:[...]` report
_PROBES = {
    "sys.volume-up":      ("pactl", "amixer"),
    "sys.volume-down":    ("pactl", "amixer"),
    "sys.mute":           ("pactl", "amixer"),
    "sys.brightness-up":  ("brightnessctl", "/sys/class/backlight"),
    "sys.brightness-down": ("brightnessctl", "/sys/class/backlight"),
    "media.playpause":    ("playerctl", "dbus-send"),
    "media.play":         ("playerctl", "dbus-send"),
    "media.pause":        ("playerctl", "dbus-send"),
    "media.next":         ("playerctl", "dbus-send"),
    "media.prev":         ("playerctl", "dbus-send"),
    "sys.screenshot":     ("import", "gnome-screenshot", "spectacle", "scrot"),
    "sys.screenshot-region": ("import", "gnome-screenshot", "spectacle", "scrot"),
    "sys.lock":           ("loginctl", "xdg-screensaver"),
    "sys.dnd":            ("gsettings", "dbus-send"),
    "net.wifi":           ("nmcli", "rfkill"),
    "net.bluetooth":      ("bluetoothctl", "rfkill"),
    "clipboard.history":  ("copyq", "clipster", "xclip"),
    "clipboard.targets":  ("xclip",),
}


def _probe_ok(name):
    """A probe is a binary name, or an absolute filesystem path that must
    be non-empty. The backlight path follows BACKLIGHT_DIR (overridable)."""
    if name.startswith("/"):
        path = BACKLIGHT_DIR if name == "/sys/class/backlight" else name
        try:
            return bool(os.listdir(path))
        except OSError:
            return False
    return _which(name) is not None


def capabilities():
    """Honest per-action capability table for THIS box.

    -> {tool_id: {"available": bool, "via": [backends present], "tier": int,
                  "note": str}}. `via` lists every backend found, not just
    the preferred one — the picker can show 'works through amixer'."""
    table = {}
    for tool, probes in _PROBES.items():
        via = [p for p in probes if _probe_ok(p)]
        note = ""
        if tool == "sys.dnd":
            note = "DE-dependent: gsettings show-banners or freedesktop Inhibited"
        elif tool.startswith("media.") and "playerctl" not in via and "dbus-send" in via:
            note = "dbus-send path needs a running org.mpris.MediaPlayer2.* player"
        elif tool == "clipboard.history" and via == ["xclip"]:
            note = "no clipboard manager — only the live clipboard (index 0)"
        elif tool in ("sys.screenshot", "sys.screenshot-region",
                      "sys.lock", "clipboard.history", "clipboard.targets") \
                and on_wayland():
            note = WAYLAND_NOTE
        table[tool] = {"available": bool(via), "via": via,
                       "tier": TOOL_TIERS.get(tool, 1), "note": note}
    return table


def available():
    """Flat list of catalog ids runnable on this box right now."""
    return sorted(t for t, c in capabilities().items() if c["available"])


# ------------------------------------------------------------------
# ActionProvider-compatible executor
# ------------------------------------------------------------------

class SystemCtlProvider:
    """Runs sys/media/net/clip actions. Confirm gating is the dispatcher's
    job (act_linux): we always report the catalog tier; tier>=2 + first-seen
    argv get held by that layer, never bypassed here."""

    def capabilities(self):
        return capabilities()

    def verify_focus(self, ctx=None):
        """Focus-guard hook (contract for type/keys paths). System actions
        don't type, but macros routing through a merged provider may land
        here — answer honestly via xdotool instead of guessing.

        ctx may carry expected_class / expected_title; returns a dict so a
        mismatch is inspectable."""
        xdot = _which("xdotool")
        if not xdot:
            return {"ok": False, "class": "", "title": "",
                    "note": "xdotool absent — focus unverifiable"}
        okc, cls, _ = _out([xdot, "getactivewindow", "getwindowclassname"])
        okt, title, _ = _out([xdot, "getactivewindow", "getwindowname"])
        cls, title = cls.strip(), title.strip()
        if not okc and not okt:
            return {"ok": False, "class": "", "title": "",
                    "note": "no active window (X11?)" + _wayland_suffix()}
        want = (ctx or {}).get("expected_class")
        ok = bool(cls or title) and (not want or want.lower() in cls.lower())
        return {"ok": ok, "class": cls, "title": title,
                "note": _wayland_suffix().lstrip(" |")}

    def run(self, action, ctx=None):
        tool = action.get("tool") or action.get("id") or ""
        args = action.get("args") or {}
        if "arg" in action and not args:
            args = {"arg": action["arg"]}
        suffix_tiers = {k.split(".")[-1]: v for k, v in TOOL_TIERS.items()}
        tier = action.get("tier", TOOL_TIERS.get(
            tool, suffix_tiers.get(str(tool).split(".")[-1], 1)))
        ok, detail = dispatch(tool, args)
        return ActionResult(ok=ok, detail=detail, tier=tier, confirm_id=None)


if __name__ == "__main__":
    caps = capabilities()
    print(json.dumps({"display": session_type(), "actions": caps},
                     indent=2, sort_keys=True))
    print("available:[%s]" % ",".join(available()))
