#!/usr/bin/env python3
"""act_linux — the Linux/X11 ActionProvider.

Deep-OS reach, honest degradation. Every capability is probed — nothing is
assumed:

  * Tool detection at import (TOOLS): wmctrl/xdotool/xprop/xrandr/xdg-open/
    pactl/loginctl/notify-send/... Missing tool -> the op degrades to a
    refused ActionResult naming what to install, never a crash.
  * Optional python-xlib: when importable it is the PREFERRED backend for
    EWMH work — window list, props, activation, _NET_WM_STATE (maximize),
    _NET_WM_DESKTOP (workspace move), configure (tile/monitor) all go over
    one X connection with zero subprocesses. xprop/xdotool/wmctrl are the
    fallback path (this exact code also runs where python-xlib is absent —
    the codebase's stdlib-only rule is honored by treating Xlib as a bonus).
  * .desktop catalog: $XDG_DATA_HOME + $XDG_DATA_DIRS (+flatpak exports)
    applications dirs, desktop-file-id dedupe (user dir wins), locale-aware
    Name, Exec field-code stripping per freedesktop spec, NoDisplay/Hidden/
    Type/TryExec honored, Terminal=true wraps in a detected emulator.
  * Icons: index.theme-parsed search (detected session theme -> hicolor ->
    Adwaita -> every other theme -> /usr/share/pixmaps), size-preference
    sorted, .png/.svg/.xpm. Misses resolve to None — the picker draws a
    letter tile; we never invent a path.
  * Wayland: detected and reported ("display":"wayland"); X ops may still
    work through Xwayland — every refusal says so plainly.

Wayland/multi-seat caveat: the EWMH path targets the DISPLAY the daemon
runs under. A nested/remote X server sees its own windows — that is the
honest semantic of "the session's apps".
"""

import configparser
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time

from act_iface import (ActionPlan, ActionProvider, ActionResult, AppInfo,
                       FocusInfo, _as_int, describe_action, wm_match)

try:
    from Xlib import X as _X, display as _xdisplay, protocol as _xproto
    HAVE_XLIB = True
except Exception:                                # stdlib-only install path
    HAVE_XLIB = False

# ---------------------------------------------------------------------
# Tool detection — probed once at import (per spec), reported via
# capabilities(); every user of a tool also copes with it being absent.
# ---------------------------------------------------------------------

_TOOL_NAMES = (
    "wmctrl", "xdotool", "xprop", "xrandr", "xdpyinfo", "xclip", "xsel",
    "xdg-open", "gio", "pactl", "amixer", "playerctl", "gdbus", "busctl",
    "brightnessctl", "light", "nmcli", "bluetoothctl", "rfkill",
    "loginctl", "xflock4", "dm-tool", "xdg-screensaver", "xset",
    "notify-send", "xfconf-query", "gsettings", "kreadconfig5",
    "scrot", "maim", "import", "xwd", "gnome-screenshot",
    "xfce4-screenshooter", "flameshot", "spectacle",
    "x-terminal-emulator", "xfce4-terminal", "gnome-terminal", "konsole",
    "kgx", "xterm", "alacritty", "kitty", "st", "wezterm", "foot",
)
TOOLS = {t: shutil.which(t) for t in _TOOL_NAMES}

# terminal emulators and how each takes "run this argv": list-form arg
# (['-e'] means argv follows the flag, ['--'] the gnome style, ['-x'] the
# xfce4-terminal execute-remainder form)
_TERM_ARGV = {"gnome-terminal": ["--"], "kgx": ["--"],
              "xfce4-terminal": ["-x"], "konsole": ["-e"],
              "xterm": ["-e"], "alacritty": ["-e"], "kitty": ["--"],
              "st": ["-e"], "wezterm": ["-e"], "foot": ["--"],
              "x-terminal-emulator": ["-e"]}

SUBPROC_TIMEOUT = 6.0
EWMH_SOURCE = 1            # 1 = normal app requester per EWMH spec


def _sh(*args, timeout=SUBPROC_TIMEOUT, input_text=None):
    """argv-only subprocess.run; returns CompletedProcess, never raises —
    errors surface as rc<0 objects with the reason in .stderr."""
    try:
        return subprocess.run([str(a) for a in args], capture_output=True,
                              text=True, timeout=timeout, input=input_text)
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, "", f"{args[0]}: not found")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "timed out")


def _spawn(argv, cwd=None):
    """Detached spawn — the action must not die with the daemon request.
    Returns (pid, None) or (None, error-string)."""
    try:
        p = subprocess.Popen([str(a) for a in argv],
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             cwd=cwd or os.path.expanduser("~"),
                             start_new_session=True)
        return p.pid, None
    except (OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------
# freedesktop .desktop parsing
# ---------------------------------------------------------------------

def application_dirs():
    """applications/ dirs in XDG precedence order (first wins on
    duplicate desktop-file-ids)."""
    dirs = []
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser(
        "~/.local/share")
    for base in [home] + (os.environ.get("XDG_DATA_DIRS") or
                          "/usr/local/share:/usr/share").split(":"):
        d = os.path.join(base, "applications")
        if d and os.path.isdir(d):
            dirs.append(d)
    for d in (os.path.expanduser(
            "~/.local/share/flatpak/exports/share/applications"),
              "/var/lib/flatpak/exports/share/applications"):
        if os.path.isdir(d):
            dirs.append(d)
    return dirs


def _locale_langs():
    """['en_US','en'] style preference list from LC_MESSAGES/LANG."""
    out = []
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var) or ""
        v = v.split(".")[0].split("@")[0]
        if v:
            out.append(v)
            if "_" in v:
                out.append(v.split("_")[0])
    return out


def exec_split(exec_str, app_name="", icon="", desktop_path=""):
    """Exec= line -> argv. freedesktop field codes:

      %f %F %u %U %d %D %n %N %v %m   dropped (file/URL args we don't pass)
      %i                            --icon <Icon> when the app has one
      %c                            the (localized) app name
      %k                            the .desktop file path
      %%                            a literal %
    Quoting: "..." groups an arg; \\ escapes (\\, \", \s, \n, \t honored).
    A token that is only field codes drops; embedded codes are stripped.
    """
    # tokenizer: honors double-quoted spans + backslash escapes
    raw, cur, q, i = [], [], False, 0
    s = exec_str or ""
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            cur.append({"s": " ", "n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
            i += 2
            continue
        if ch == '"':
            q = not q
            i += 1
            continue
        if ch.isspace() and not q:
            if cur:
                raw.append("".join(cur))
                cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    if cur:
        raw.append("".join(cur))

    argv = []
    for tok in raw:
        if "%" not in tok:
            argv.append(tok)
            continue
        out, j, drop = [], 0, False
        while j < len(tok):
            if tok[j] == "%" and j + 1 < len(tok):
                code = tok[j + 1]
                if code == "%":
                    out.append("%")
                elif code == "i":
                    # %i expands to TWO argv words: --icon <icon>
                    if icon:
                        if "".join(out).strip():
                            argv.append("".join(out).strip())
                        out = []
                        argv.extend(["--icon", icon])
                elif code == "c":
                    out.append(app_name or "")
                elif code == "k":
                    out.append(desktop_path or "")
                elif code in "fFuUdDnNvm":
                    pass                       # drop
                else:
                    drop = True                # unknown %X: drop whole token
                    break                      #   (spec: implementation-defined)
                j += 2
            else:
                out.append(tok[j])
                j += 1
        if drop:
            continue
        tok2 = "".join(out).strip()
        if tok2:
            argv.append(tok2)
    return argv


def _parse_desktop(path, app_id):
    """-> AppInfo | None (skipped files return None)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    entry = {}
    section = None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if section != "Desktop Entry" or "=" not in line:
            continue
        k, _, v = line.partition("=")
        entry[k.strip()] = v.strip()

    def truthy(k):
        return entry.get(k, "").lower() == "true"

    if entry.get("Type", "Application") != "Application":
        return None
    if truthy("Hidden") or truthy("NoDisplay"):
        return None
    exec_raw = entry.get("Exec", "")
    if not exec_raw:
        return None
    tryexec = entry.get("TryExec", "")
    if tryexec and shutil.which(tryexec) is None and \
            not os.path.exists(tryexec):
        return None
    # localized Name
    name = ""
    langs = _locale_langs()
    for lang in langs:
        if entry.get("Name[%s]" % lang):
            name = entry["Name[%s]" % lang]
            break
    if not name:
        name = entry.get("Name", app_id)
    generic = entry.get("GenericName", "")
    icon = entry.get("Icon", "")
    argv = exec_split(exec_raw, name, icon, path)
    if not argv:
        return None
    wm = entry.get("StartupWMClass", "") or \
        os.path.basename(argv[0])
    cats = [c for c in entry.get("Categories", "").split(";") if c]
    return AppInfo(id=app_id, name=name, generic=generic, icon=icon,
                   exec=exec_raw, argv=argv, wm_class=wm,
                   terminal=truthy("Terminal"), categories=cats, file=path,
                   workdir=entry.get("Path", ""))


class DesktopDB:
    """The installed-apps catalog. Scan is cached; rescan() refreshes
    (the @ picker can call it on catalog staleness)."""

    def __init__(self, dirs=None):
        self.dirs = list(dirs) if dirs is not None else application_dirs()
        self._apps = None
        self._lock = threading.RLock()

    def scan(self):
        with self._lock:
            if self._apps is None:
                self._apps = self._load()
            return self._apps

    def rescan(self):
        with self._lock:
            self._apps = self._load()
            return self._apps

    def get(self, app_id):
        if not app_id:
            return None
        apps = self.scan()
        want = str(app_id)
        if want in apps:
            return apps[want]
        # accept 'foo.desktop' spellings + case-insensitive
        stem = want[:-8] if want.endswith(".desktop") else want
        if stem in apps:
            return apps[stem]
        low = want.lower()
        for k, app in apps.items():
            if k.lower() == low:
                return app
        return None

    def _load(self):
        apps = {}
        for d in self.dirs:                      # precedence order
            for root, _sub, files in os.walk(d):
                for fn in sorted(files):
                    if not fn.endswith(".desktop"):
                        continue
                    rel = os.path.relpath(os.path.join(root, fn), d)
                    fid = rel[:-8].replace("/", "-")   # desktop-file-id
                    if fid in apps:
                        continue                   # higher-precedence dir won
                    info = _parse_desktop(os.path.join(root, fn), fid)
                    if info is not None:
                        apps[fid] = info
        return apps


# ---------------------------------------------------------------------
# Icon resolution — index.theme parsed, honest misses
# ---------------------------------------------------------------------

_ICON_EXTS = (".png", ".svg", ".xpm")


def _parse_theme_index(path):
    """index.theme -> [(size, scale, type, subdir)] — only what we need."""
    cp = configparser.RawConfigParser(strict=False)
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8")
    except Exception:
        return []
    out = []
    for sec in cp.sections():
        if sec == "Icon Theme" or not cp.has_option(sec, "Size"):
            continue
        try:
            size = cp.getint(sec, "Size")
        except ValueError:
            continue
        scale = cp.getint(sec, "Scale", fallback=1)
        typ = cp.get(sec, "Type", fallback="Threshold")
        out.append((int(size), int(scale), typ, sec))
    return out


class IconResolver:
    """icon name -> file. Search order: session theme (xfconf/gsettings
    detected), hicolor, Adwaita, remaining themes alphabetically, then the
    pixmap dirs — spec order "hicolor -> pixmap -> fallback"."""

    def __init__(self, icon_roots=None, pixmap_dirs=None, prefer=96):
        if icon_roots is None:
            home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser(
                "~/.local/share")
            roots = [os.path.join(home, "icons"),
                     os.path.expanduser("~/.icons")]
            for base in (os.environ.get("XDG_DATA_DIRS") or
                         "/usr/local/share:/usr/share").split(":"):
                roots.append(os.path.join(base, "icons"))
            icon_roots = [r for r in roots if os.path.isdir(r)]
        if pixmap_dirs is None:
            pixmap_dirs = [d for d in ("/usr/share/pixmaps",
                                       "/usr/local/share/pixmaps")
                           if os.path.isdir(d)]
        self.roots = icon_roots
        self.pixmaps = pixmap_dirs
        self.prefer = int(prefer)
        self._themes = None      # name -> (root, [(size,scale,type,dir)])
        self._cache = {}

    def _detect_theme(self):
        for probe in (["xfconf-query", "-c", "xsettings", "-p",
                       "/Net/IconThemeName"],
                      ["gsettings", "get", "org.gnome.desktop.interface",
                       "icon-theme"]):
            if TOOLS.get(probe[0]):
                r = _sh(*probe, timeout=3)
                v = (r.stdout or "").strip().strip("'\"")
                if r.returncode == 0 and v and "not exist" not in v.lower():
                    return v
        return None

    def _theme_map(self):
        if self._themes is None:
            themes = {}
            for root in self.roots:
                for name in sorted(os.listdir(root)):
                    idx = os.path.join(root, name, "index.theme")
                    if os.path.isfile(idx):
                        themes[name] = (os.path.join(root, name),
                                        _parse_theme_index(idx))
            self._themes = themes
        return self._themes

    def find(self, icon):
        """icon = Icon= value (name or abs path) -> existing file | None."""
        if not icon:
            return None
        if "/" in icon:
            return icon if os.path.isfile(icon) else None
        if icon in self._cache:
            return self._cache[icon]
        themes = self._theme_map()
        order = []
        for t in (self._detect_theme(), "hicolor", "Adwaita", "default"):
            if t and t in themes and t not in order:
                order.append(t)
        order += [t for t in sorted(themes) if t not in order]
        for name in order:
            root, dirs = themes[name]
            hit = self._find_in_theme(root, dirs, icon)
            if hit:
                self._cache[icon] = hit
                return hit
            # unthemed loose files directly under the theme root
            for ext in _ICON_EXTS:
                p = os.path.join(root, icon + ext)
                if os.path.isfile(p):
                    self._cache[icon] = p
                    return p
        for d in self.pixmaps:
            for ext in _ICON_EXTS:
                p = os.path.join(d, icon + ext)
                if os.path.isfile(p):
                    self._cache[icon] = p
                    return p
        self._cache[icon] = None
        return None

    def _find_in_theme(self, root, dirs, icon):
        # prefer scale-1 dirs at/below `prefer`, largest first; then any
        # scale-1 dir largest-first; then scaled dirs. svg beats nothing.
        def rank(ent):
            size, scale, typ, _d = ent
            under = 0 if size <= self.prefer else 1
            return (scale != 1, under, -size)
        for size, scale, typ, sub in sorted(dirs, key=rank):
            d = os.path.join(root, sub)
            for ext in _ICON_EXTS:
                p = os.path.join(d, icon + ext)
                if os.path.isfile(p):
                    return p
        return None


# ---------------------------------------------------------------------
# X11 plumbing — Xlib preferred, xprop/xdotool/wmctrl fallback
# ---------------------------------------------------------------------

class _XConn:
    """One shared python-xlib connection (locked — the daemon threads)."""

    def __init__(self, display_name=None):
        self._name = display_name or None
        self._disp = None
        self._tried = False
        self.lock = threading.RLock()

    def disp(self):
        with self.lock:
            if self._disp is None and not self._tried and HAVE_XLIB:
                self._tried = True
                try:
                    self._disp = _xdisplay.Display(self._name)
                except Exception:
                    self._disp = None
            return self._disp

    def atom(self, name):
        return self.disp().intern_atom(name)

    def root_prop(self, atom_name):
        d = self.disp()
        if d is None:
            return None
        try:
            with self.lock:
                p = d.screen().root.get_full_property(
                    self.atom(atom_name), _X.AnyPropertyType)
            return list(p.value) if p and p.value is not None else None
        except Exception:
            return None

    def win_prop(self, wid, atom_name):
        d = self.disp()
        if d is None:
            return None
        try:
            win = d.create_resource_object("window", int(wid))
            with self.lock:
                p = win.get_full_property(self.atom(atom_name),
                                          _X.AnyPropertyType)
            if p is None or p.value is None:
                return None
            if p.format == 8:
                # keep interior NULs — WM_CLASS is "instance\0class\0"
                return bytes(p.value).decode("utf-8", "replace")
            return list(p.value)
        except Exception:
            return None

    def client_msg(self, wid, atom_name, data32):
        """EWMH client message -> root, SubstructureRedirect — the real
        mechanism wmctrl wraps."""
        d = self.disp()
        if d is None:
            return False
        try:
            root = d.screen().root
            win = d.create_resource_object("window", int(wid))
            mask = (_X.SubstructureRedirectMask |
                    _X.SubstructureNotifyMask)
            data = (list(data32) + [0] * 5)[:5]
            ev = _xproto.event.ClientMessage(
                window=win, client_type=self.atom(atom_name),
                data=(32, data))
            with self.lock:
                root.send_event(ev, event_mask=mask)
                d.flush()
            return True
        except Exception:
            return False

    def configure(self, wid, x=None, y=None, w=None, h=None):
        d = self.disp()
        if d is None:
            return False
        try:
            win = d.create_resource_object("window", int(wid))
            kw = {}
            if x is not None:
                kw["x"] = int(x)
            if y is not None:
                kw["y"] = int(y)
            if w is not None:
                kw["width"] = int(w)
            if h is not None:
                kw["height"] = int(h)
            with self.lock:
                win.configure(**kw)
                d.sync()
            return True
        except Exception:
            return False

    def geometry(self, wid):
        d = self.disp()
        if d is None:
            return None
        try:
            win = d.create_resource_object("window", int(wid))
            with self.lock:
                g = win.get_geometry()
            return {"x": g.x, "y": g.y, "w": g.width, "h": g.height}
        except Exception:
            return None


# ----------------------------------------------------------------------
# The provider
# ----------------------------------------------------------------------

class LinuxX11Provider(ActionProvider):
    """X11 action provider. `display` may override os.environ['DISPLAY']
    (tests run on Xvfb); every subprocess gets it via env."""

    name = "linux-x11"

    def __init__(self, config_dir=None, ledger_path=None, desktop_dirs=None,
                 icon_roots=None, display=None):
        if ledger_path is None and config_dir:
            ledger_path = os.path.join(config_dir, "approved.json")
        super().__init__(ledger_path=ledger_path)
        self.db = DesktopDB(desktop_dirs)
        self.icons = IconResolver(icon_roots)
        self.display = display if display is not None else \
            os.environ.get("DISPLAY", "")
        self._x = _XConn(self.display)
        self.register("open_app", self._h_open_app)
        self.register("focus_app", self._h_focus_app)
        self.register("close_window", self._h_close_window)
        self.register("move_workspace", self._h_move_workspace)
        self.register("workspace", self._h_workspace)
        self.register("tile", self._h_tile)
        self.register("maximize", self._h_maximize)
        self.register("move_monitor", self._h_move_monitor)
        self.register("key_press", self._h_key_press)
        self.register("alt_tab", self._h_alt_tab)
        self.register("minimize", self._h_minimize)
        self.register("show_desktop", self._h_show_desktop)
        self.register("scroll", self._h_scroll)
        self.register("type_text", self._h_type_text)
        self.register("open_url", self._h_open_url)
        self.register("run_argv", self._h_run_argv)
        self._tool_rows += [
            {"id": "app.open", "kind": "app", "icon": "application-x-executable", "label": "Open app",
             "tier": 1, "arg": "app"},
            {"id": "app.focus", "kind": "app", "label": "Focus app window",
             "tier": 1, "arg": "app"},
            {"id": "app.close", "kind": "app", "label": "Close app window",
             "tier": 2, "arg": "app"},
            {"id": "win.tile-left", "kind": "win", "label": "Tile window left",
             "tier": 1},
            {"id": "win.tile-right", "kind": "win", "label": "Tile window right",
             "tier": 1},
            {"id": "win.maximize", "kind": "win", "icon": "window-maximize", "label": "Maximize window",
             "tier": 1},
            {"id": "win.move-monitor", "kind": "win",
             "label": "Move to other monitor", "tier": 1, "arg": "index"},
            {"id": "win.workspace-next", "kind": "win",
             "label": "Next workspace", "tier": 1},
            {"id": "win.to-workspace", "kind": "win",
             "label": "Move window to workspace", "tier": 1, "arg": "index"},
            # quick actions
            {"id": "win.alt-tab", "icon": "window-switch", "kind": "win", "label": "Switch window",
             "tier": 1},
            {"id": "win.minimize", "icon": "window-minimize", "kind": "win", "label": "Minimize window",
             "tier": 1},
            {"id": "win.close", "icon": "window-close", "kind": "win", "label": "Close window",
             "tier": 2},
            {"id": "desktop.show", "icon": "user-desktop", "kind": "win", "label": "Show desktop",
             "tier": 1},
            {"id": "browser.new-tab", "icon": "tab-new", "kind": "nav", "label": "New tab",
             "tier": 1},
            {"id": "browser.close-tab", "icon": "window-close", "kind": "nav", "label": "Close tab",
             "tier": 1},
            {"id": "nav.scroll-up", "icon": "go-up", "kind": "nav", "label": "Scroll up",
             "tier": 1},
            {"id": "nav.scroll-down", "icon": "go-down", "kind": "nav", "label": "Scroll down",
             "tier": 1},
            {"id": "edit.copy", "icon": "edit-copy", "kind": "edit", "label": "Copy", "tier": 1},
            {"id": "edit.paste", "icon": "edit-paste", "kind": "edit", "label": "Paste", "tier": 1},
            {"id": "edit.undo", "icon": "edit-undo", "kind": "edit", "label": "Undo", "tier": 1},
            {"id": "key.enter", "icon": "key-enter", "kind": "edit", "label": "Enter", "tier": 1},
            {"id": "open.url", "kind": "builtin", "label": "Open URL",
             "tier": 1, "arg": "url"},
            {"id": "type.text", "kind": "builtin", "icon": "input-keyboard", "label": "Type text",
             "tier": 1, "arg": "text"},
            {"id": "key.press", "kind": "builtin", "label": "Key press",
             "tier": 1, "arg": "keys"},
            {"id": "command", "kind": "command", "label": "Terminal command",
             "tier": 2, "arg": "argv"},
        ]

    # -- environment -------------------------------------------------------

    def _env(self):
        env = dict(os.environ)
        if self.display:
            env["DISPLAY"] = self.display
        return env

    def _sh(self, *args, timeout=SUBPROC_TIMEOUT, input_text=None):
        env = self._env()
        try:
            return subprocess.run([str(a) for a in args], env=env,
                                  capture_output=True, text=True,
                                  timeout=timeout, input=input_text)
        except FileNotFoundError:
            return subprocess.CompletedProcess(args, 127, "",
                                               f"{args[0]}: not found")
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(args, 124, "", "timed out")

    def _spawn(self, argv, cwd=None):
        try:
            p = subprocess.Popen([str(a) for a in argv], env=self._env(),
                                 stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 cwd=cwd or os.path.expanduser("~"),
                                 start_new_session=True)
            return p.pid, None
        except (OSError, ValueError) as e:
            return None, f"{type(e).__name__}: {e}"

    def session(self):
        if self.display or os.environ.get("DISPLAY"):
            return "x11"
        if os.environ.get("WAYLAND_DISPLAY"):
            return "wayland"
        return "none"

    def capabilities(self):
        ewmh = "xlib" if HAVE_XLIB else (
            "wmctrl" if TOOLS.get("wmctrl") else
            "xdotool" if TOOLS.get("xdotool") else "none")
        return {
            "provider": self.name,
            "display": self.session(),
            "xlib": bool(HAVE_XLIB and self.display),
            "tools": {k: v for k, v in TOOLS.items() if v},
            "features": {
                "apps": bool(self.db.dirs),
                "icons": True,
                "ewmh": ewmh,
                "typing": bool(TOOLS.get("xdotool")),
                "open_url": bool(TOOLS.get("xdg-open") or TOOLS.get("gio")),
                "monitors": bool(TOOLS.get("xrandr")),
            },
        }

    # -- contract: apps / icons / focus --------------------------------------

    def list_apps(self):
        apps = sorted(self.db.scan().values(),
                      key=lambda a: (a.name or "").lower())
        for a in apps:
            if a.icon_path is None and a.icon:
                a.icon_path = self.icons.find(a.icon)
        return apps

    def icon_path(self, app_id):
        app = self.db.get(app_id)
        if app is not None:
            if app.icon_path is None and app.icon:
                app.icon_path = self.icons.find(app.icon)
            return app.icon_path
        return self.icons.find(app_id)   # raw icon name fallback

    def focused_app(self):
        wid = self._active_wid()
        if not wid:
            return None
        info = self._win_info(wid)
        return FocusInfo(wid=wid, wm_class=info.get("class", ""),
                         instance=info.get("instance", ""),
                         title=info.get("title", ""), pid=info.get("pid", 0))

    # -- X plumbing ----------------------------------------------------------

    def _active_wid(self):
        if HAVE_XLIB and self.display:
            v = self._x.root_prop("_NET_ACTIVE_WINDOW")
            if v:
                return int(v[0])
        if TOOLS.get("xdotool") and self.display:
            r = self._sh(TOOLS["xdotool"], "getactivewindow")
            if r.returncode == 0 and r.stdout.strip().isdigit():
                return int(r.stdout.strip())
        if TOOLS.get("xprop") and self.display:
            r = self._sh(TOOLS["xprop"], "-root", "_NET_ACTIVE_WINDOW")
            m = re.search(r"0x[0-9a-fA-F]+", r.stdout or "")
            if m:
                return int(m.group(0), 16)
        return 0

    def _client_list(self):
        if HAVE_XLIB and self.display:
            v = self._x.root_prop("_NET_CLIENT_LIST")
            if v:
                return [int(x) for x in v]
        if TOOLS.get("xprop") and self.display:
            r = self._sh(TOOLS["xprop"], "-root", "_NET_CLIENT_LIST")
            ids = re.findall(r"0x[0-9a-fA-F]+", r.stdout or "")
            if ids:
                return [int(x, 16) for x in ids]
        if TOOLS.get("wmctrl") and self.display:
            r = self._sh(TOOLS["wmctrl"], "-l")
            out = []
            for line in (r.stdout or "").splitlines():
                p = line.split()
                if p and p[0].startswith("0x"):
                    out.append(int(p[0], 16))
            return out
        return []

    def _win_info(self, wid):
        """{instance,class,title,pid} — Xlib per-prop or ONE xprop call."""
        info = {"instance": "", "class": "", "title": "", "pid": 0}
        if HAVE_XLIB and self.display:
            cls = self._x.win_prop(wid, "WM_CLASS")
            if isinstance(cls, str) and cls:
                parts = [p for p in cls.split("\0") if p] or [cls]
                info["instance"], info["class"] = parts[0], parts[-1]
            name = self._x.win_prop(wid, "_NET_WM_NAME") or \
                self._x.win_prop(wid, "WM_NAME")
            if isinstance(name, str):
                info["title"] = name
            pid = self._x.win_prop(wid, "_NET_WM_PID")
            if isinstance(pid, list) and pid:
                info["pid"] = int(pid[0])
            return info
        if TOOLS.get("xprop") and self.display:
            r = self._sh(TOOLS["xprop"], "-id", str(wid), "WM_CLASS",
                         "WM_NAME", "_NET_WM_NAME", "_NET_WM_PID")
            for line in (r.stdout or "").splitlines():
                if line.startswith("WM_CLASS"):
                    q = re.findall(r'"((?:[^"\\]|\\.)*)"', line)
                    if q:
                        info["instance"], info["class"] = q[0], q[-1]
                elif line.startswith("_NET_WM_NAME") or \
                        line.startswith("WM_NAME"):
                    m = re.search(r'"(.*)"\s*$', line)
                    if m:
                        info["title"] = m.group(1)
                elif line.startswith("_NET_WM_PID"):
                    m = re.search(r"=\s*(\d+)", line)
                    if m:
                        info["pid"] = int(m.group(1))
            return info
        return info

    def _find_window(self, pattern):
        """wid of the client whose WM_CLASS/instance/title matches, else 0."""
        for wid in self._client_list():
            info = self._win_info(wid)
            if wm_match(pattern, info["class"], info["instance"],
                        info["title"]):
                return wid
        return 0

    def _n_desktops(self):
        v = self._x.root_prop("_NET_NUMBER_OF_DESKTOPS") \
            if HAVE_XLIB and self.display else None
        if v:
            return int(v[0])
        if TOOLS.get("xdotool"):
            r = self._sh(TOOLS["xdotool"], "get_num_desktops")
            if r.stdout.strip().isdigit():
                return int(r.stdout.strip())
        if TOOLS.get("xprop"):
            r = self._sh(TOOLS["xprop"], "-root", "_NET_NUMBER_OF_DESKTOPS")
            m = re.search(r"=\s*(\d+)", r.stdout or "")
            if m:
                return int(m.group(1))
        return 1

    def _cur_desktop(self):
        v = self._x.root_prop("_NET_CURRENT_DESKTOP") \
            if HAVE_XLIB and self.display else None
        if v is not None:
            return int(v[0])
        if TOOLS.get("xdotool"):
            r = self._sh(TOOLS["xdotool"], "get_desktop")
            if r.stdout.strip().isdigit():
                return int(r.stdout.strip())
        return 0

    def _workarea(self):
        v = self._x.root_prop("_NET_WORKAREA") \
            if HAVE_XLIB and self.display else None
        if not v and TOOLS.get("xprop"):
            r = self._sh(TOOLS["xprop"], "-root", "_NET_WORKAREA")
            v = [int(n) for n in re.findall(r"\d+", r.stdout or "")]
        if v and len(v) >= 4:
            i = min(self._cur_desktop(), len(v) // 4 - 1) * 4
            return {"x": v[i], "y": v[i + 1], "w": v[i + 2], "h": v[i + 3]}
        mons = self._monitors()
        if mons:
            m = mons[0]
            return {"x": m["x"], "y": m["y"], "w": m["w"], "h": m["h"]}
        return {"x": 0, "y": 0, "w": 1280, "h": 720}

    def _monitors(self):
        if TOOLS.get("xrandr") and self.display:
            r = self._sh(TOOLS["xrandr"], "--listmonitors")
            mons = []
            for line in (r.stdout or "").splitlines():
                m = re.search(r"(\d+)/\d+x(\d+)/\d+\+(\d+)\+(\d+)", line)
                if m:
                    mons.append({"w": int(m.group(1)), "h": int(m.group(2)),
                                 "x": int(m.group(3)), "y": int(m.group(4))})
            if mons:
                return mons
            r = self._sh(TOOLS["xrandr"], "--query")
            m = re.search(r"current (\d+) x (\d+)", r.stdout or "")
            if m:
                return [{"x": 0, "y": 0, "w": int(m.group(1)),
                         "h": int(m.group(2))}]
        if HAVE_XLIB and self.display:
            s = self._x.disp().screen()
            return [{"x": 0, "y": 0, "w": s.width_in_pixels,
                     "h": s.height_in_pixels}]
        return []

    # -- EWMH ops (backend chain per op) -------------------------------------

    def _ewmh_ok(self):
        if not self.display and not os.environ.get("DISPLAY"):
            return "no X display (DISPLAY unset)"
        return None

    def _activate(self, wid):
        if HAVE_XLIB and self.display and self._x.client_msg(
                wid, "_NET_ACTIVE_WINDOW", [EWMH_SOURCE, 0, 0]):
            return None
        if TOOLS.get("wmctrl"):
            r = self._sh(TOOLS["wmctrl"], "-i", "-a", hex(wid))
            if r.returncode == 0:
                return None
        if TOOLS.get("xdotool"):
            r = self._sh(TOOLS["xdotool"], "windowactivate", str(wid))
            if r.returncode == 0:
                return None
            return (r.stderr or "xdotool windowactivate failed").strip()
        return "need wmctrl or xdotool (or python-xlib) to focus a window"

    def _close(self, wid):
        if HAVE_XLIB and self.display and self._x.client_msg(
                wid, "_NET_CLOSE_WINDOW", [int(time.time()), EWMH_SOURCE]):
            return None
        if TOOLS.get("wmctrl"):
            r = self._sh(TOOLS["wmctrl"], "-i", "-c", hex(wid))
            if r.returncode == 0:
                return None
        if TOOLS.get("xdotool"):
            r = self._sh(TOOLS["xdotool"], "windowclose", str(wid))
            if r.returncode == 0:
                return None
            return (r.stderr or "xdotool windowclose failed").strip()
        return "need wmctrl or xdotool (or python-xlib) to close a window"

    def _wm_state(self, wid, op, *atom_names):
        """op in {'add','remove','toggle'} on _NET_WM_STATE atoms."""
        act = {"add": 1, "remove": 0, "toggle": 2}[op]
        if HAVE_XLIB and self.display:
            atoms = [self._x.atom(n) for n in atom_names]
            if self._x.client_msg(wid, "_NET_WM_STATE",
                                  [act] + atoms + [EWMH_SOURCE]):
                return None
        if TOOLS.get("wmctrl"):
            prop = ",".join(op if False else n.replace("_NET_WM_STATE_", "")
                            .lower() for n in atom_names)
            r = self._sh(TOOLS["wmctrl"], "-i", "-r", hex(wid), "-b",
                         "%s,%s" % (op, prop))
            if r.returncode == 0:
                return None
            return (r.stderr or "wmctrl -b failed").strip()
        return "need wmctrl (or python-xlib) to change _NET_WM_STATE"

    def _set_desktop_for(self, wid, n):
        if HAVE_XLIB and self.display and self._x.client_msg(
                wid, "_NET_WM_DESKTOP", [int(n), EWMH_SOURCE]):
            return None
        if TOOLS.get("wmctrl"):
            r = self._sh(TOOLS["wmctrl"], "-i", "-r", hex(wid), "-t", str(n))
            if r.returncode == 0:
                return None
        if TOOLS.get("xdotool"):
            r = self._sh(TOOLS["xdotool"], "set_desktop_for_window",
                         str(wid), str(n))
            if r.returncode == 0:
                return None
            return (r.stderr or "set_desktop_for_window failed").strip()
        return "need wmctrl/xdotool/python-xlib to move a workspace"

    def _switch_desktop(self, n):
        if HAVE_XLIB and self.display and self._x.client_msg(
                self._x.disp().screen().root, "_NET_CURRENT_DESKTOP",
                [int(n), int(time.time())]):
            return None
        if TOOLS.get("wmctrl"):
            r = self._sh(TOOLS["wmctrl"], "-s", str(n))
            if r.returncode == 0:
                return None
        if TOOLS.get("xdotool"):
            r = self._sh(TOOLS["xdotool"], "set_desktop", str(n))
            if r.returncode == 0:
                return None
            return (r.stderr or "set_desktop failed").strip()
        return "need wmctrl/xdotool/python-xlib to switch workspace"

    def _move_resize(self, wid, x, y, w, h):
        if HAVE_XLIB and self.display and self._x.configure(wid, x, y, w, h):
            return None
        if TOOLS.get("wmctrl"):
            r = self._sh(TOOLS["wmctrl"], "-i", "-r", hex(wid), "-e",
                         "0,%d,%d,%d,%d" % (x, y, w, h))
            if r.returncode == 0:
                return None
        if TOOLS.get("xdotool"):
            r1 = self._sh(TOOLS["xdotool"], "windowmove", str(wid),
                          str(x), str(y))
            r2 = self._sh(TOOLS["xdotool"], "windowsize", str(wid),
                          str(w), str(h))
            if r1.returncode == 0 and r2.returncode == 0:
                return None
            return ((r1.stderr or "") + (r2.stderr or "")).strip() or \
                "windowmove/windowsize failed"
        return "need wmctrl/xdotool/python-xlib to move/resize"

    def _unmax(self, wid):
        return self._wm_state(wid, "remove", "_NET_WM_STATE_MAXIMIZED_VERT",
                              "_NET_WM_STATE_MAXIMIZED_HORZ")

    # -- action handlers ------------------------------------------------------

    def _need_display(self):
        if not (self.display or os.environ.get("DISPLAY")):
            return ActionResult.refused("no X display (DISPLAY unset)")
        return None

    def _h_open_app(self, a, ctx):
        app = self.db.get(a.get("id") or a.get("desktop") or a.get("app"))
        argv, gated, what = None, [], ""
        if app is not None:
            argv = list(app.argv)
            what = app.name or app.id
            if app.terminal:
                term = next((t for t in _TERM_ARGV if TOOLS.get(t)), None)
                if not term:
                    return ActionResult.refused(
                        f"'{what}' needs a terminal; none of "
                        f"{sorted(_TERM_ARGV)} installed")
                argv = [TOOLS[term]] + _TERM_ARGV[term] + argv
        elif a.get("argv"):
            argv = [str(x) for x in a["argv"]]
            gated = [argv]
            what = " ".join(argv)
        elif a.get("exec"):
            argv = shlex.split(str(a["exec"])) if " " in str(a["exec"]) \
                else [str(a["exec"])]
            gated = [argv]
            what = a["exec"]
        if not argv:
            return ActionResult.refused(
                "open_app: no such app '%s'" %
                (a.get("id") or a.get("desktop") or a.get("argv") or "?"))
        if not shutil.which(argv[0]) and not os.path.exists(argv[0]):
            return ActionResult.refused(f"open_app: '{argv[0]}' not on PATH")
        cwd = (app.workdir if app and app.workdir else None)

        def fn(_ctx):
            pid, err = self._spawn(argv, cwd=cwd)
            if err:
                return ActionResult.refused(f"open_app: {err}")
            time.sleep(0.25)
            rc = None
            try:
                import os as _o
                rc = _o.waitpid(pid, _o.WNOHANG)[1]
            except (ChildProcessError, OSError):
                pass                       # reaped elsewhere or not ours
            if rc:
                return ActionResult.refused(
                    f"open_app: '{argv[0]}' exited immediately (rc status)")
            return ActionResult.done(f"started {what} (pid {pid})",
                                     extra={"pid": pid})
        return ActionPlan(fn, describe=f"open app: {what}", argv=gated)

    def _h_focus_app(self, a, ctx):
        pat = a.get("wm_class") or a.get("class") or a.get("pattern")
        if not pat and (a.get("id") or a.get("desktop") or a.get("app")):
            app = self.db.get(a.get("id") or a.get("desktop") or a.get("app"))
            if app is not None:
                pat = app.wm_class or os.path.basename(app.argv[0])
        wid = _as_int(a.get("wid"), 0)
        if not pat and not wid:
            return ActionResult.refused("focus_app: need wm_class, id, or wid")

        def fn(_ctx):
            err = self._ewmh_ok()
            if err:
                return ActionResult.refused(err)
            target = wid or self._find_window(pat)
            if not target:
                return ActionResult.refused(
                    f"no window matching '{pat}'")
            err = self._activate(target)
            if err:
                return ActionResult.refused(err)
            time.sleep(0.08)
            if self._active_wid() == target:
                t = self._win_info(target).get("title") or pat
                return ActionResult.done(f"focused '{t}'",
                                         extra={"wid": target})
            return ActionResult.done(
                "activate sent; focus switch not confirmed by the WM",
                extra={"wid": target, "confirmed": False})
        return ActionPlan(fn, describe=f"focus window '{pat or wid}'")

    def _h_close_window(self, a, ctx):
        def fn(_ctx):
            err = self._ewmh_ok()
            if err:
                return ActionResult.refused(err)
            wid = _as_int(a.get("wid"), 0)
            if not wid and a.get("wm_class"):
                wid = self._find_window(a["wm_class"])
            elif not wid and (a.get("id") or a.get("app")):
                app = self.db.get(a.get("id") or a.get("app"))
                if app is not None and app.wm_class:
                    wid = self._find_window(app.wm_class)
            if not wid:
                wid = self._active_wid()
            if not wid:
                return ActionResult.refused("no window to close")
            title = self._win_info(wid).get("title") or hex(wid)
            err = self._close(wid)
            if err:
                return ActionResult.refused(err)
            return ActionResult.done(f"close requested for '{title}'",
                                     extra={"wid": wid})
        return ActionPlan(fn, describe=describe_action(a) if a.get("label")
                          else "close window")

    def _h_move_workspace(self, a, ctx):
        n = a.get("n", a.get("workspace"))
        if n is None:
            return ActionResult.refused("move_workspace: missing n")

        def fn(_ctx):
            err = self._ewmh_ok()
            if err:
                return ActionResult.refused(err)
            nd = self._n_desktops()
            nn = (self._cur_desktop() + 1) % nd if str(n) in ("next",) else \
                (self._cur_desktop() - 1) % nd if str(n) in ("prev",) else \
                _as_int(n, -1)
            if nn < 0 or nn >= nd:
                return ActionResult.refused(
                    f"workspace {n} out of range (0..{nd - 1})")
            wid = self._active_wid()
            if not wid:
                return ActionResult.refused("no focused window to move")
            err = self._set_desktop_for(wid, nn)
            if err:
                return ActionResult.refused(err)
            return ActionResult.done(f"window -> workspace {nn}",
                                     extra={"wid": wid, "workspace": nn})
        return ActionPlan(fn, describe=f"move window to workspace {n}")

    def _h_workspace(self, a, ctx):
        n = a.get("n", a.get("workspace", "next"))

        def fn(_ctx):
            err = self._ewmh_ok()
            if err:
                return ActionResult.refused(err)
            nd = self._n_desktops()
            cur = self._cur_desktop()
            nn = (cur + 1) % nd if str(n) == "next" else \
                (cur - 1) % nd if str(n) == "prev" else _as_int(n, -1)
            if nn < 0 or nn >= nd:
                return ActionResult.refused(
                    f"workspace {n} out of range (0..{nd - 1})")
            err = self._switch_desktop(nn)
            if err:
                return ActionResult.refused(err)
            return ActionResult.done(f"workspace {nn}",
                                     extra={"workspace": nn})
        return ActionPlan(fn, describe=f"switch to workspace {n}")

    def _h_tile(self, a, ctx):
        side = str(a.get("side") or a.get("dir") or "left").lower()
        if side not in ("left", "right"):
            return ActionResult.refused(f"tile: bad side '{side}'")

        def fn(_ctx):
            err = self._ewmh_ok()
            if err:
                return ActionResult.refused(err)
            wid = _as_int(a.get("wid"), 0) or self._active_wid()
            if not wid:
                return ActionResult.refused("no focused window to tile")
            wa = self._workarea()
            self._unmax(wid)             # maximized windows ignore resize
            time.sleep(0.05)
            half = wa["w"] // 2
            x = wa["x"] if side == "left" else wa["x"] + half
            err = self._move_resize(wid, x, wa["y"], half, wa["h"])
            if err:
                return ActionResult.refused(err)
            return ActionResult.done(
                f"tiled {side} ({half}x{wa['h']}+{x}+{wa['y']})",
                extra={"wid": wid})
        return ActionPlan(fn, describe=f"tile window {side}")

    def _h_maximize(self, a, ctx):
        on = a.get("on", True)

        def fn(_ctx):
            err = self._ewmh_ok()
            if err:
                return ActionResult.refused(err)
            wid = _as_int(a.get("wid"), 0) or self._active_wid()
            if not wid:
                return ActionResult.refused("no focused window")
            err = self._wm_state(wid, "add" if on else "remove",
                                 "_NET_WM_STATE_MAXIMIZED_VERT",
                                 "_NET_WM_STATE_MAXIMIZED_HORZ")
            if err:
                return ActionResult.refused(err)
            return ActionResult.done(
                ("maximized" if on else "unmaximized"), extra={"wid": wid})
        return ActionPlan(fn, describe="maximize" if on else "unmaximize")

    def _h_move_monitor(self, a, ctx):
        n = a.get("n", a.get("monitor", "next"))

        def fn(_ctx):
            err = self._ewmh_ok()
            if err:
                return ActionResult.refused(err)
            mons = self._monitors()
            if len(mons) < 2:
                return ActionResult.done(
                    "single monitor — nothing to move to",
                    extra={"monitors": len(mons)})
            wid = _as_int(a.get("wid"), 0) or self._active_wid()
            if not wid:
                return ActionResult.refused("no focused window")
            g = self._x.geometry(wid) if HAVE_XLIB else None
            if g is None and TOOLS.get("xdotool"):
                r = self._sh(TOOLS["xdotool"], "getwindowgeometry",
                             "--shell", str(wid))
                vals = dict(re.findall(r"(X|Y|WIDTH|HEIGHT)=(\d+)",
                                       r.stdout or ""))
                if vals:
                    g = {"x": int(vals["X"]), "y": int(vals["Y"]),
                         "w": int(vals["WIDTH"]), "h": int(vals["HEIGHT"])}
            if str(n) == "next":
                cx = (g["x"] + g["w"] // 2) if g else -1
                cur = next((i for i, m in enumerate(mons)
                            if m["x"] <= cx < m["x"] + m["w"]), 0)
                idx = (cur + 1) % len(mons)
            else:
                idx = _as_int(n, -1)
                if idx < 0 or idx >= len(mons):
                    return ActionResult.refused(
                        f"monitor {n} out of range (0..{len(mons) - 1})")
            m = mons[idx]
            w = g["w"] if g else min(800, m["w"])
            h = g["h"] if g else min(600, m["h"])
            w, h = min(w, m["w"]), min(h, m["h"])
            x = m["x"] + (m["w"] - w) // 2
            y = m["y"] + (m["h"] - h) // 2
            self._unmax(wid)
            time.sleep(0.05)
            err = self._move_resize(wid, x, y, w, h)
            if err:
                return ActionResult.refused(err)
            return ActionResult.done(f"window -> monitor {idx}",
                                     extra={"wid": wid, "monitor": idx})
        return ActionPlan(fn, describe=f"move window to monitor {n}")

    def _h_key_press(self, a, ctx):
        keys = str(a.get("keys") or a.get("key") or "").strip()
        if not keys:
            return ActionResult.refused("key_press: missing keys")
        if not TOOLS.get("xdotool"):
            return ActionResult.refused(
                "key_press: xdotool not installed (apt install xdotool)")

        def fn(_ctx):
            r = self._sh(TOOLS["xdotool"], "key", "--clearmodifiers",
                         "--", *keys.split(), timeout=5)
            if r.returncode != 0:
                return ActionResult.refused(
                    (r.stderr or "xdotool key failed").strip())
            return ActionResult.done(f"keys: {keys}")
        return ActionPlan(fn, describe=f"press keys {keys}", guarded=True)

    # -- quick actions -------------------------------------------------------
    # Non-destructive by design: alt-tab cycles focus, minimize hides a
    # window, show-desktop toggles the WM desktop view, scroll is wheel
    # clicks. Close is deliberately absent here — the existing
    # close_window handler covers it, tier-2 gated for confirm.

    def _h_alt_tab(self, a, ctx):
        if not TOOLS.get("xdotool"):
            return ActionResult.refused(
                "alt_tab: xdotool not installed (apt install xdotool)")

        def fn(_ctx):
            r = self._sh(TOOLS["xdotool"], "key", "--clearmodifiers",
                         "--", "alt+Tab", timeout=5)
            if r.returncode != 0:
                return ActionResult.refused(
                    (r.stderr or "xdotool alt+Tab failed").strip())
            return ActionResult.done("switched window")
        return ActionPlan(fn, describe="switch window (Alt-Tab)",
                          guarded=True)

    def _h_minimize(self, a, ctx):
        if not TOOLS.get("xdotool"):
            return ActionResult.refused(
                "minimize: xdotool not installed (apt install xdotool)")

        def fn(_ctx):
            wid = self._active_wid()
            if not wid:
                return ActionResult.refused("no active window to minimize")
            title = self._win_info(wid).get("title") or hex(wid)
            r = self._sh(TOOLS["xdotool"], "windowminimize",
                         str(wid), timeout=5)
            if r.returncode != 0:
                return ActionResult.refused(
                    (r.stderr or "windowminimize failed").strip())
            return ActionResult.done(f"minimized '{title}'",
                                     extra={"wid": wid})
        return ActionPlan(fn, describe="minimize the active window")

    def _h_show_desktop(self, a, ctx):
        if not TOOLS.get("wmctrl"):
            return ActionResult.refused(
                "show_desktop: wmctrl not installed (apt install wmctrl)")

        def fn(_ctx):
            r = self._sh(TOOLS["wmctrl"], "-k", "on", timeout=5)
            if r.returncode != 0:
                return ActionResult.refused(
                    (r.stderr or "wmctrl -k failed").strip())
            return ActionResult.done("show desktop on")
        return ActionPlan(fn, describe="show desktop")

    def _h_scroll(self, a, ctx):
        down = str(a.get("dir", "down")).lower() != "up"
        btn = "5" if down else "4"
        clicks = max(1, min(int(a.get("n", 3) or 3), 10))
        if not TOOLS.get("xdotool"):
            return ActionResult.refused(
                "scroll: xdotool not installed (apt install xdotool)")

        def fn(_ctx):
            r = self._sh(TOOLS["xdotool"], "click", "--repeat",
                         str(clicks), "--delay", "60", btn, timeout=10)
            if r.returncode != 0:
                return ActionResult.refused(
                    (r.stderr or "xdotool scroll failed").strip())
            return ActionResult.done(
                f"scrolled {'down' if down else 'up'} x{clicks}")
        return ActionPlan(fn, describe="scroll down" if down
                          else "scroll up", guarded=True)

    def _h_type_text(self, a, ctx):
        text = a.get("text")
        if text is None or str(text) == "":
            return ActionResult.refused("type_text: empty text")
        text = str(text)
        if not TOOLS.get("xdotool"):
            return ActionResult.refused(
                "type_text: xdotool not installed (apt install xdotool)")

        def fn(_ctx):
            r = self._sh(TOOLS["xdotool"], "type", "--clearmodifiers",
                         "--delay", "12", "--", text, timeout=30)
            if r.returncode != 0:
                return ActionResult.refused(
                    (r.stderr or "xdotool type failed").strip())
            f = self.focused_app()
            return ActionResult.done(
                f"typed {len(text)} chars",
                extra={"focus": f.wm_class if f else ""})
        return ActionPlan(fn, describe=describe_action(a), guarded=True)

    def _h_open_url(self, a, ctx):
        url = str(a.get("url") or "").strip()
        if not url:
            return ActionResult.refused("open_url: missing url")
        tool = TOOLS.get("xdg-open") or TOOLS.get("gio")
        if not tool:
            return ActionResult.refused("open_url: no xdg-open/gio")
        argv = [tool, "open", url] if os.path.basename(tool) == "gio" \
            else [tool, url]

        def fn(_ctx):
            pid, err = self._spawn(argv)
            if err:
                return ActionResult.refused(f"open_url: {err}")
            return ActionResult.done(f"opened {url}")
        return ActionPlan(fn, describe=f"open {url}")

    def _h_run_argv(self, a, ctx):
        argv = a.get("argv")
        if isinstance(argv, str):
            argv = shlex.split(argv)
        if not isinstance(argv, list) or not argv:
            return ActionResult.refused("run_argv: empty argv")
        argv = [str(x) for x in argv]
        wait = _as_int(a.get("wait"), 0)

        def fn(_ctx):
            if wait:
                r = self._sh(*argv, timeout=min(max(wait, 1), 120))
                if r.returncode != 0:
                    return ActionResult.refused(
                        (r.stderr or r.stdout or
                         f"exit {r.returncode}").strip()[:300])
                return ActionResult.done(
                    (r.stdout or "ok").strip()[:300] or "ok")
            pid, err = self._spawn(argv)
            if err:
                return ActionResult.refused(f"run_argv: {err}")
            return ActionResult.done(f"spawned {argv[0]} (pid {pid})",
                                     extra={"pid": pid})
        return ActionPlan(fn, describe=describe_action(a), argv=[argv])


# ----------------------------------------------------------------------
# Module default — wfd/system_ctl do `from act_linux import provider` or
# construct their own with config_dir=...
# ----------------------------------------------------------------------

def provider(**kw):
    return LinuxX11Provider(**kw)
