#!/usr/bin/env python3
"""act_platform — action provider for macOS and Windows laptops.

act_linux is X11-only; on darwin/win32 wfd constructs this provider
instead. It implements the same catalog ids where the stock toolchain
honestly supports them:

  macOS    osascript keystroke / key code (System Events), `open -a`,
           `open <url>`, F11 show-desktop, screencapture (system_ctl)
  Windows  powershell SendKeys, WScript.Shell AppActivate, Shell
           ToggleDesktop, user32 ShowWindowAsync, os.startfile for URLs

Every other tool refuses cleanly — 'not supported on this platform' —
so the wire result is always {ok, fired, detail}, never a traceback or
a missing-binary stderr. The focus guard still runs: focused_app() is
implemented via the frontmost process / foreground window, and returns
None when the platform probe fails (guard then refuses).
"""
import os
import re
import shlex
import subprocess
import sys
import time

from act_iface import (ActionPlan, ActionProvider, ActionResult, AppInfo,
                       FocusInfo, _as_int, describe_action)

SUBPROC_TIMEOUT = 15


def host_platform():
    """'macos' | 'windows' | 'linux' | 'other' — the provider switch."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith(("win32", "cygwin")):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def _which(name):
    from shutil import which
    return which(name)


def _run(argv, timeout=SUBPROC_TIMEOUT):
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode == 0, (r.stderr or r.stdout or "").strip()
    except FileNotFoundError:
        return False, "not found: %s" % argv[0]
    except subprocess.TimeoutExpired:
        return False, "%s timed out" % os.path.basename(str(argv[0]))
    except UnicodeDecodeError:
        return False, "%s output was not utf-8 text" % os.path.basename(
            str(argv[0]))
    except OSError as e:
        return False, str(e)


def _spawn(argv):
    try:
        p = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        return p.pid, ""
    except (FileNotFoundError, OSError) as e:
        return None, str(e)


def _osa(script, timeout=SUBPROC_TIMEOUT):
    return _run(["osascript", "-e", script], timeout=timeout)


def _ps(script, timeout=SUBPROC_TIMEOUT, sta=False):
    """Run a PowerShell -Command script. sta=True adds -Sta, which the
    System.Windows.Forms clipboard APIs require (powershell.exe and
    pwsh>=7 accept -Sta on Windows; callers only pass it there)."""
    ps = _which("powershell") or _which("powershell.exe") \
        or _which("pwsh") or _which("pwsh.exe")
    if not ps:
        return False, "powershell not found"
    argv = [ps, "-NoProfile", "-NonInteractive"]
    if sta:
        argv.append("-Sta")
    return _run(argv + ["-Command", script], timeout=timeout)


def _osa_text(s):
    """A string safe to embed inside an AppleScript "..." literal."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _psq(s):
    """A string safe inside a PowerShell '...' literal."""
    return str(s).replace("'", "''")


# C# user32 block shared by the window-management PowerShell calls below:
# EnumWindows/IsWindowVisible/GetWindowText (real hwnd listing), focus
# (GetForegroundWindow/SetForegroundWindow/IsIconic/ShowWindowAsync/
# keybd_event — the menu-key tap satisfies the foreground-lock rule so
# SetForegroundWindow can steal), and close (PostMessageW WM_CLOSE).
_WIN32_CS = r'''
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class WFU {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern int GetWindowTextLengthW(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint c);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool PostMessageW(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, IntPtr extra);
  public static List<string> Wins() {
    var L = new List<string>();
    EnumWindows((h, l) => {
      if (!IsWindowVisible(h)) return true;
      if (GetWindow(h, 4) != IntPtr.Zero) return true;
      int n = GetWindowTextLengthW(h);
      if (n < 1) return true;
      var sb = new StringBuilder(n + 1);
      GetWindowTextW(h, sb, n + 1);
      uint p = 0; GetWindowThreadProcessId(h, out p);
      string pn;
      try { pn = System.Diagnostics.Process.GetProcessById((int)p).ProcessName; }
      catch { pn = ""; }
      L.Add(h.ToInt64().ToString("x") + "\t" + pn + "\t" + sb.ToString());
      return true;
    }, IntPtr.Zero);
    return L;
  }
}
'''


def _win32_ps(body):
    """PowerShell script = embedded C# user32 class + caller statements."""
    return "Add-Type -TypeDefinition @'\n%s\n'@\n%s" % (_WIN32_CS, body)


def _is_hwnd(v):
    """hex hwnd (from list_windows rows), e.g. 'a1b2c3' / '0x000a01f2'."""
    s = str(v or "").strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    return bool(s) and len(s) <= 16 and \
        all(c in "0123456789abcdef" for c in s)


def _hwnd_script(want, stmts):
    """Win32 focus/close body for a hwnd literal."""
    h = str(want).strip().lower()
    h = h[2:] if h.startswith("0x") else h
    return _win32_ps(
        '$h = [IntPtr]::new([Convert]::ToInt64("%s", 16))\n' % h +
        'if (-not [WFU]::IsWindow($h)) { "no-window"; exit 2 }\n' + stmts)


# hwnd -> bring to front. A zero-delay VK_MENU tap is the standard trick
# to lift the foreground lock (same thing WScript.Shell.AppActivate
# does), then restore-if-minimized + SetForegroundWindow, verified by
# reading GetForegroundWindow back.
_HWND_FOCUS = (
    '[WFU]::keybd_event(0x12, 0, 0, [IntPtr]::Zero)\n'
    '[WFU]::keybd_event(0x12, 0, 2, [IntPtr]::Zero)\n'
    'if ([WFU]::IsIconic($h)) { [void][WFU]::ShowWindowAsync($h, 9) }\n'
    '[void][WFU]::ShowWindowAsync($h, 5)\n'
    '[void][WFU]::SetForegroundWindow($h)\n'
    'Start-Sleep -Milliseconds 150\n'
    'if ([WFU]::GetForegroundWindow() -eq $h) { "ok" } else { exit 1 }\n')

_HWND_CLOSE = (
    '[void][WFU]::PostMessageW($h, 0x10, [IntPtr]::Zero, [IntPtr]::Zero)\n'
    'Start-Sleep -Milliseconds 300\n'
    'if (-not [WFU]::IsWindow($h)) { "ok" } else { "sent" }\n')


def clip_get():
    """Clipboard text for THIS host -> str or None.

    Windows: Get-Clipboard -Raw. macOS: pbpaste. Linux: xclip (returns
    None on failure — callers already tolerate a missing clipboard)."""
    h = host_platform()
    if h == "windows":
        ok, out = _ps('Get-Clipboard -Raw -ErrorAction SilentlyContinue')
        return out if ok else None
    if h == "macos":
        ok, out = _run(["pbpaste"])
        return out if ok else None
    if h == "linux":
        ok, out = _run(["xclip", "-selection", "clipboard", "-o"])
        return out if ok else None
    return None


def clip_set_text(text):
    """Put text on the host clipboard. -> (ok, detail)."""
    h = host_platform()
    text = str(text)
    if h == "windows":
        # a single-quoted PS literal safely spans lines; _psq doubles '
        return _ps("Set-Clipboard -Value '%s'" % _psq(text))
    if h == "macos":
        try:
            r = subprocess.run(["pbcopy"], input=text, text=True,
                               capture_output=True, timeout=SUBPROC_TIMEOUT)
            return r.returncode == 0, (r.stderr or "").strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, str(e)
    if h == "linux":
        try:
            r = subprocess.run(["xclip", "-selection", "clipboard", "-i"],
                               input=text, text=True, capture_output=True,
                               timeout=SUBPROC_TIMEOUT)
            return r.returncode == 0, (r.stderr or "").strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, str(e)
    return False, _UNSUPPORTED


def clip_set_image(path):
    """Put image bytes on the host clipboard. -> (ok, detail).

    Windows: System.Drawing.Image -> Clipboard.SetImage (STA). macOS:
    osascript POSIX file -> Finder-style file copy; image-capable apps
    (Preview, most chats) accept it. Linux: caller owns X11 — this only
    reports _UNSUPPORTED (the resident wfclip.py owner does it)."""
    h = host_platform()
    if h == "windows":
        esc = str(path).replace("'", "''")
        return _ps(
            'Add-Type -AssemblyName System.Windows.Forms,System.Drawing; '
            '$img = [System.Drawing.Image]::FromFile(\'%s\'); '
            '[System.Windows.Forms.Clipboard]::SetImage($img); '
            '$img.Dispose()' % esc, sta=True)
    if h == "macos":
        return _osa('set the clipboard to (POSIX file "%s")' % _osa_text(path))
    return False, _UNSUPPORTED


def clip_set_files(paths):
    """Put a file-drop list on the host clipboard. -> (ok, detail)."""
    h = host_platform()
    if isinstance(paths, str):
        paths = [paths]
    if h == "windows":
        items = ",".join("'%s'" % str(p).replace("'", "''") for p in paths)
        return _ps(
            'Add-Type -AssemblyName System.Windows.Forms; '
            '$c = New-Object System.Collections.Specialized.StringCollection; '
            '$c.AddRange([string[]](%s)); '
            '[System.Windows.Forms.Clipboard]::SetFileDropList($c)' % items,
            sta=True)
    if h == "macos":
        if len(paths) == 1:
            return _osa('set the clipboard to (POSIX file "%s")'
                        % _osa_text(paths[0]))
        return _osa('set the clipboard to {%s}' % ", ".join(
            '(POSIX file "%s")' % _osa_text(p) for p in paths))
    return False, _UNSUPPORTED


# 'ctrl+shift+t' -> AppleScript modifier set + key, or Windows SendKeys
_MAC_MODS = {"ctrl": "control", "control": "control", "alt": "option",
             "option": "option", "shift": "shift", "super": "command",
             "meta": "command", "cmd": "command", "command": "command",
             "win": "command"}
_MAC_CODES = {"return": 36, "enter": 36, "tab": 48, "space": 49,
              "backspace": 51, "delete": 51, "escape": 53, "esc": 53,
              "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96,
              "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
              "f11": 103, "f12": 111, "home": 115, "end": 119,
              "pageup": 116, "pagedown": 121, "forwarddelete": 117,
              "left": 123, "right": 124, "down": 125, "up": 126}
_WIN_MODS = {"ctrl": "^", "control": "^", "alt": "%", "shift": "+",
             "meta": "^", "cmd": "^"}
_WIN_KEYS = {"return": "{ENTER}", "enter": "{ENTER}", "tab": "{TAB}",
             "space": " ", "escape": "{ESC}", "esc": "{ESC}",
             "backspace": "{BKSP}", "delete": "{DEL}", "home": "{HOME}",
             "end": "{END}", "pageup": "{PGUP}", "pagedown": "{PGDN}",
             "left": "{LEFT}", "right": "{RIGHT}", "up": "{UP}",
             "down": "{DOWN}",
             "f1": "{F1}", "f2": "{F2}", "f3": "{F3}", "f4": "{F4}",
             "f5": "{F5}", "f6": "{F6}", "f7": "{F7}", "f8": "{F8}",
             "f9": "{F9}", "f10": "{F10}", "f11": "{F11}",
             "f12": "{F12}"}
_SENDKEYS_SPECIAL = set("+^%~()[]{}")


def _sendkeys_escape(text):
    out = []
    for ch in str(text):
        out.append("{%s}" % ch if ch in _SENDKEYS_SPECIAL else ch)
    return "".join(out)


def _sendkeys_text(text):
    """Literal text -> a SendKeys string. Newlines become {ENTER} (a raw
    \\r/\\n in SendWait is not a reliable Enter across apps); everything
    else goes through _sendkeys_escape."""
    parts = re.split(r"(\r\n|\r|\n)", str(text))
    return "".join("{ENTER}" if p in ("\r\n", "\r", "\n")
                   else _sendkeys_escape(p) for p in parts)


def _win_key_combo(keys):
    """'ctrl+t' -> SendKeys chord '^t', or None when a key has no
    SendKeys spelling (super/win — SendKeys cannot emit the logo key)."""
    mods, rest = "", []
    for part in str(keys).split():
        for tok in part.split("+"):
            tok = tok.strip()
            if not tok:
                continue
            low = tok.lower()
            if low in _WIN_MODS:
                mods += _WIN_MODS[low]
            elif low in ("super", "win", "meta"):
                return None
            elif low in _WIN_KEYS:
                rest.append(_WIN_KEYS[low])
            elif len(tok) == 1:
                rest.append(_sendkeys_escape(tok))
            else:
                rest.append(_sendkeys_escape(tok))
    if not rest:
        return None
    return mods + "".join(rest)


def _mac_keystroke(keys):
    """'ctrl+t' -> 'keystroke "t" using {control down}' or 'key code 36
    using {...}' for named keys. None on untranslatable input."""
    mods, key = [], None
    for tok in str(keys).replace("+", " ").split():
        low = tok.lower()
        if low in _MAC_MODS:
            m = _MAC_MODS[low]
            if m + " down" not in mods:
                mods.append(m + " down")
        else:
            key = low if low in _MAC_CODES else tok
    if key is None:
        return None
    use = (" using {%s}" % ", ".join(mods)) if mods else ""
    if key.lower() in _MAC_CODES:
        return 'key code %d%s' % (_MAC_CODES[key.lower()], use)
    if len(key) == 1:
        return 'keystroke "%s"%s' % (_osa_text(key), use)
    return 'keystroke "%s"%s' % (_osa_text(key), use)


_UNSUPPORTED = ("not supported on this platform")


# macOS window rows: id = "<pid>:<window index>" — pid focuses the app,
# the index AXRaise's the exact window (AX exposes no stable window id).
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


def _mac_window_rows(out):
    rows = []
    for line in (out or "").splitlines():
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


def _mac_windows_ax():
    ok, out = _osa(_MAC_AX_LIST, timeout=20)
    return _mac_window_rows(out) if ok else []


class CrossPlatformProvider(ActionProvider):
    """The macOS/Windows act provider — same catalog surface, honest
    coverage. `platform` is injectable so the unit tests exercise both
    OS branches on this Linux box."""

    def __init__(self, config_dir=None, ledger_path=None, platform=None):
        if ledger_path is None and config_dir:
            ledger_path = os.path.join(config_dir, "approved.json")
        super().__init__(ledger_path=ledger_path)
        self.platform = platform or host_platform()
        self.name = self.platform
        self.register("type_text", self._h_type_text)
        self.register("key_press", self._h_key_press)
        self.register("open_app", self._h_open_app)
        self.register("focus_app", self._h_focus_app)
        self.register("close_window", self._h_close_window)
        self.register("alt_tab", self._h_alt_tab)
        self.register("minimize", self._h_minimize)
        self.register("maximize", self._h_maximize)
        self.register("show_desktop", self._h_show_desktop)
        self.register("open_url", self._h_open_url)
        self.register("run_argv", self._h_run_argv)
        # no honest stock mechanism: refuse cleanly, never silently no-op
        for t in ("scroll", "move_workspace", "workspace", "tile",
                  "move_monitor"):
            self.register(t, self._refuse(t))
        self._tool_rows += [
            {"id": "app.open", "kind": "app", "label": "Open app",
             "tier": 1, "arg": "app"},
            {"id": "app.focus", "kind": "app", "label": "Focus app window",
             "tier": 1, "arg": "app"},
            {"id": "app.close", "kind": "app", "label": "Close app window",
             "tier": 2, "arg": "app"},
            {"id": "win.alt-tab", "kind": "win", "label": "Switch window",
             "tier": 1},
            {"id": "win.minimize", "kind": "win", "label": "Minimize window",
             "tier": 1},
            {"id": "win.close", "kind": "win", "label": "Close window",
             "tier": 2},
            {"id": "desktop.show", "kind": "win", "label": "Show desktop",
             "tier": 1},
            {"id": "browser.new-tab", "kind": "nav", "label": "New tab",
             "tier": 1},
            {"id": "browser.close-tab", "kind": "nav", "label": "Close tab",
             "tier": 1},
            {"id": "edit.copy", "kind": "edit", "label": "Copy", "tier": 1},
            {"id": "edit.paste", "kind": "edit", "label": "Paste",
             "tier": 1},
            {"id": "edit.undo", "kind": "edit", "label": "Undo", "tier": 1},
            {"id": "key.enter", "kind": "edit", "label": "Enter", "tier": 1},
            {"id": "open.url", "kind": "builtin", "label": "Open URL",
             "tier": 1, "arg": "url"},
            {"id": "type.text", "kind": "builtin", "label": "Type text",
             "tier": 1, "arg": "text"},
            {"id": "key.press", "kind": "builtin", "label": "Key press",
             "tier": 1, "arg": "keys"},
            {"id": "command", "kind": "command", "label": "Terminal command",
             "tier": 2, "arg": "argv"},
        ]

    def _refuse(self, t):
        def h(a, ctx):
            return ActionResult.refused("%s: %s" % (t, _UNSUPPORTED))
        return h

    # -- the keystroke backends -------------------------------------------

    def _keys_supported(self):
        if self.platform == "macos":
            return bool(_which("osascript"))
        if self.platform == "windows":
            return bool(_which("powershell") or _which("powershell.exe")
                        or _which("pwsh") or _which("pwsh.exe"))
        return False

    def _type(self, text):
        if self.platform == "macos":
            return _osa('tell application "System Events" to keystroke "%s"'
                        % _osa_text(text))
        return _ps('Add-Type -AssemblyName System.Windows.Forms; '
                   '[System.Windows.Forms.SendKeys]::SendWait(\'%s\')'
                   % _psq(_sendkeys_text(text)))

    def _press(self, keys):
        if self.platform == "macos":
            ks = _mac_keystroke(keys)
            if ks is None:
                return False, "keys not translatable"
            return _osa('tell application "System Events" to %s' % ks)
        combo = _win_key_combo(keys)
        if combo is None:
            return False, "keys not translatable"
        return _ps('Add-Type -AssemblyName System.Windows.Forms; '
                   '[System.Windows.Forms.SendKeys]::SendWait(\'%s\')'
                   % _psq(combo))

    # -- contract ----------------------------------------------------------

    def focused_app(self):
        if self.platform == "macos":
            ok, out = _osa('tell application "System Events" to get name '
                           'of first application process whose frontmost '
                           'is true')
            if ok and out.strip():
                return FocusInfo(wm_class=out.strip(), title=out.strip())
            return None
        if self.platform == "windows":
            ok, out = _ps(
                'Add-Type "using System; using System.Runtime.InteropServices;'
                'public class WF{[DllImport(\\"user32.dll\\")]public static '
                'extern IntPtr GetForegroundWindow();[DllImport(\\"user32.dll\\")]'
                'public static extern uint GetWindowThreadProcessId(IntPtr h, '
                'out uint p);}"; $h=[WF]::GetForegroundWindow(); $p=0;'
                '[void][WF]::GetWindowThreadProcessId($h,[ref]$p);'
                '$pr=Get-Process -Id $p -ErrorAction SilentlyContinue;'
                'if($pr){\"{0}`t{1}\" -f $pr.ProcessName,$pr.MainWindowTitle}')
            if ok and "\t" in out:
                name, title = out.split("\t", 1)
                return FocusInfo(wm_class=name.strip(), title=title.strip())
            return None
        return None

    def list_windows(self):
        """Rows for a /windows-style @ picker: [{id,title,app}].

        macOS: real windows via System Events (pid + window index, needs
        Accessibility); without it, visible app names — which focus_app
        can still activate via `open -a`."""
        if self.platform == "macos":
            rows = _mac_windows_ax()
            if rows:
                return rows
            ok, out = _osa('tell application "System Events" to get name of '
                           'every application process whose visible is true')
            if not ok:
                return []
            return [{"id": n.strip(), "title": n.strip(), "app": n.strip()}
                    for n in out.split(",") if n.strip()]
        if self.platform == "windows":
            # EnumWindows: every visible, titled, top-level window —
            # catches real windows a bare Get-Process.MainWindowTitle
            # misses (some apps keep none), and the id is the hwnd a
            # SetForegroundWindow path can target precisely.
            ok, out = _ps(_win32_ps('[WFU]::Wins() | ForEach-Object {$_}'))
            if not ok:
                return []
            rows = []
            for line in out.splitlines():
                p = line.split("\t")
                if len(p) >= 3:
                    rows.append({"id": p[0].strip(), "app": p[1].strip(),
                                 "title": "\t".join(p[2:]).strip()})
            return rows
        return []

    def list_apps(self):
        apps = []
        if self.platform == "macos":
            for d in ("/Applications", "/System/Applications",
                      os.path.expanduser("~/Applications")):
                try:
                    names = os.listdir(d)
                except OSError:
                    continue
                for n in names:
                    if n.endswith(".app"):
                        name = n[:-4]
                        apps.append(AppInfo(
                            id=name, name=name,
                            exec='open -a "%s"' % name,
                            argv=["open", "-a", name],
                            wm_class=name,
                            file=os.path.join(d, n)))
        elif self.platform == "windows":
            ok, out = _ps('Get-StartApps | ForEach-Object '
                          '{\"{0}`t{1}\" -f $_.Name,$_.AppID}')
            if ok:
                for line in out.splitlines():
                    p = line.split("\t")
                    if len(p) == 2 and p[0].strip() and p[1].strip():
                        apps.append(AppInfo(
                            id=p[1].strip(), name=p[0].strip(),
                            exec='shell:AppsFolder\\%s' % p[1].strip(),
                            argv=["explorer.exe",
                                  "shell:AppsFolder\\" + p[1].strip()],
                            wm_class=p[0].strip()))
        return sorted(apps, key=lambda a: (a.name or "").lower())

    def icon_path(self, app_id):
        return None              # no .desktop/theme icon model here

    def capabilities(self):
        keys = self._keys_supported()
        return {
            "provider": self.name,
            "display": self.platform,
            "tools": {},
            "features": {
                "apps": bool(self.list_apps()),
                "icons": False,
                "typing": keys,
                "open_url": True,
                "monitors": False,
                "windows": self.platform in ("macos", "windows"),
            },
        }

    def catalog(self):
        cat = super().catalog()
        cat["windows"] = self.list_windows()
        return cat

    # -- handlers ------------------------------------------------------------

    def _h_type_text(self, a, ctx):
        text = a.get("text")
        if text is None or str(text) == "":
            return ActionResult.refused("type_text: empty text")
        text = str(text)
        if not self._keys_supported():
            return ActionResult.refused(
                "type_text: %s" % _UNSUPPORTED)

        def fn(_ctx):
            ok, det = self._type(text)
            if not ok:
                return ActionResult.refused(det or _UNSUPPORTED)
            return ActionResult.done("typed %d chars" % len(text))
        return ActionPlan(fn, describe=describe_action(a), guarded=True)

    def _h_key_press(self, a, ctx):
        keys = str(a.get("keys") or a.get("key") or "").strip()
        if not keys:
            return ActionResult.refused("key_press: missing keys")
        if not self._keys_supported():
            return ActionResult.refused("key_press: %s" % _UNSUPPORTED)

        def fn(_ctx):
            ok, det = self._press(keys)
            if not ok:
                return ActionResult.refused(det or _UNSUPPORTED)
            return ActionResult.done("keys: %s" % keys)
        return ActionPlan(fn, describe="press keys %s" % keys, guarded=True)

    def _h_open_app(self, a, ctx):
        want = str(a.get("id") or a.get("app") or a.get("exec") or "").strip()
        argv = a.get("argv")
        if isinstance(argv, list) and argv:
            argv = [str(x) for x in argv]
            def fn(_ctx):
                pid, err = _spawn(argv)
                return (ActionResult.done("spawned %s (pid %d)" % (argv[0], pid))
                        if pid else ActionResult.refused(err or "spawn failed"))
            return ActionPlan(fn, describe=describe_action(a), argv=[argv])
        if not want:
            return ActionResult.refused("open_app: missing app")
        if self.platform == "macos":
            cmd = ["open", "-a", want]
            argv_show = cmd
        elif self.platform == "windows":
            cmd = None
            argv_show = ["explorer.exe", "shell:AppsFolder\\" + want]
        else:
            return ActionResult.refused("open_app: %s" % _UNSUPPORTED)

        def fn(_ctx):
            if self.platform == "macos":
                ok, det = _run(cmd)
                if not ok:
                    return ActionResult.refused(
                        det or "could not open '%s'" % want)
                return ActionResult.done("opened %s" % want)
            pid, err = _spawn(["explorer.exe",
                               "shell:AppsFolder\\" + want])
            if not pid:
                return ActionResult.refused(err or "spawn failed")
            return ActionResult.done("opened %s" % want)
        return ActionPlan(fn, describe="open %s" % want, argv=[argv_show])

    def _h_focus_app(self, a, ctx):
        want = str(a.get("app") or a.get("id") or a.get("wm_class") or "").strip()
        if not want:
            return ActionResult.refused("focus_app: missing app")

        def fn(_ctx):
            if self.platform == "macos":
                # `open -a` is NSWorkspace.activate — no Automation consent
                # prompt, unlike `tell app X to activate`. AppleScript is
                # the fallback for apps `open` can't resolve by name.
                ok, det = _run(["open", "-a", want])
                if not ok:
                    ok, det = _osa('tell application "%s" to activate'
                                   % _osa_text(want))
            elif self.platform == "windows":
                if _is_hwnd(want):
                    ok, det = _ps(_hwnd_script(want, _HWND_FOCUS))
                else:
                    ok, det = _ps('$w=New-Object -ComObject WScript.Shell;'
                                  'if($w.AppActivate(\'%s\')){\"ok\"}else{'
                                  'exit 1}' % _psq(want))
            else:
                return ActionResult.refused(_UNSUPPORTED)
            if not ok:
                return ActionResult.refused(det or _UNSUPPORTED)
            return ActionResult.done("focused %s" % want)
        return ActionPlan(fn, describe="focus %s" % want)

    def _h_close_window(self, a, ctx):
        want = str(a.get("app") or a.get("id") or "").strip()

        def fn(_ctx):
            if self.platform == "macos":
                if want:
                    ok, det = _osa('tell application "%s" to quit'
                                   % _osa_text(want))
                else:
                    ok, det = _osa('tell application "System Events" to '
                                   'keystroke "w" using {command down}')
            elif self.platform == "windows":
                if _is_hwnd(want):
                    ok, det = _ps(_hwnd_script(want, _HWND_CLOSE))
                elif want:
                    ok, det = _ps('$p=Get-Process | Where-Object '
                                  '{$_.MainWindowTitle -like \'*%s*\'} | '
                                  'Select-Object -First 1; if($p)'
                                  '{$p.CloseMainWindow()}else{exit 1}'
                                  % _psq(want))
                else:
                    ok, det = self._press("alt+f4")
            else:
                return ActionResult.refused(_UNSUPPORTED)
            if not ok:
                return ActionResult.refused(det or _UNSUPPORTED)
            return ActionResult.done("close requested")
        return ActionPlan(fn, describe=describe_action(a) if a.get("label")
                          else "close window")

    def _h_alt_tab(self, a, ctx):
        def fn(_ctx):
            if self.platform == "macos":
                ok, det = _osa('tell application "System Events" to key '
                               'code 48 using {command down}')
            elif self.platform == "windows":
                ok, det = self._press("alt+tab")
            else:
                return ActionResult.refused(_UNSUPPORTED)
            if not ok:
                return ActionResult.refused(det or _UNSUPPORTED)
            return ActionResult.done("switched window")
        return ActionPlan(fn, describe="switch window (Alt-Tab)",
                          guarded=True)

    def _h_minimize(self, a, ctx):
        def fn(_ctx):
            if self.platform == "macos":
                ok, det = _osa('tell application "System Events" to '
                               'keystroke "m" using {command down}')
            elif self.platform == "windows":
                ok, det = _ps(
                    'Add-Type "using System; using System.Runtime.InteropServices;'
                    'public class WF{[DllImport(\\"user32.dll\\")]public static '
                    'extern IntPtr GetForegroundWindow();[DllImport(\\"user32.dll\\")]'
                    'public static extern bool ShowWindowAsync(IntPtr h,int c);}";'
                    '[void][WF]::ShowWindowAsync([WF]::GetForegroundWindow(),6)')
            else:
                return ActionResult.refused(_UNSUPPORTED)
            if not ok:
                return ActionResult.refused(det or _UNSUPPORTED)
            return ActionResult.done("minimized")
        return ActionPlan(fn, describe="minimize window", guarded=True)

    def _h_maximize(self, a, ctx):
        if self.platform == "macos":
            # no stock 'maximize' — cmd-ctrl-f is fullscreen, a different thing
            def fn(_ctx):
                return ActionResult.refused(_UNSUPPORTED)
            return ActionPlan(fn, describe="maximize window")
        def fn(_ctx):
            ok, det = _ps(
                'Add-Type "using System; using System.Runtime.InteropServices;'
                'public class WF{[DllImport(\\"user32.dll\\")]public static '
                'extern IntPtr GetForegroundWindow();[DllImport(\\"user32.dll\\")]'
                'public static extern bool ShowWindowAsync(IntPtr h,int c);}";'
                '[void][WF]::ShowWindowAsync([WF]::GetForegroundWindow(),3)')
            if not ok:
                return ActionResult.refused(det or _UNSUPPORTED)
            return ActionResult.done("maximized")
        return ActionPlan(fn, describe="maximize window", guarded=True)

    def _h_show_desktop(self, a, ctx):
        def fn(_ctx):
            if self.platform == "macos":
                ok, det = _osa('tell application "System Events" to key '
                               'code 103')
            elif self.platform == "windows":
                ok, det = _ps('(New-Object -ComObject Shell.Application)'
                              '.ToggleDesktop()')
            else:
                return ActionResult.refused(_UNSUPPORTED)
            if not ok:
                return ActionResult.refused(det or _UNSUPPORTED)
            return ActionResult.done("desktop toggled")
        return ActionPlan(fn, describe="show desktop")

    def _h_open_url(self, a, ctx):
        url = str(a.get("url") or "").strip()
        if not url:
            return ActionResult.refused("open_url: missing url")

        def fn(_ctx):
            if self.platform == "macos":
                ok, det = _run(["open", url])
            elif self.platform == "windows":
                try:
                    os.startfile(url)          # noqa: S606 — URL open is the API
                    ok, det = True, ""
                except (AttributeError, OSError) as e:
                    ok, det = False, str(e)
            else:
                return ActionResult.refused(_UNSUPPORTED)
            if not ok:
                return ActionResult.refused(det or "could not open URL")
            return ActionResult.done("opened URL")
        return ActionPlan(fn, describe="open URL")

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
                ok, det = _run(argv, timeout=min(max(wait, 1), 120))
                if not ok:
                    return ActionResult.refused(det[:300])
                return ActionResult.done(det[:300] or "ok")
            pid, err = _spawn(argv)
            if not pid:
                return ActionResult.refused(err)
            return ActionResult.done("spawned %s (pid %d)" % (argv[0], pid),
                                     extra={"pid": pid})
        return ActionPlan(fn, describe=describe_action(a), argv=[argv])


def provider(**kw):
    return CrossPlatformProvider(**kw)
