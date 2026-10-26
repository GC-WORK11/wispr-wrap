#!/usr/bin/env python3
"""act_iface — the action-layer contract for the merged product.

One provider per OS (`LinuxX11Provider` in act_linux.py today; Windows/macOS
later). The daemon (wfd.py) and the system_ctl lane code against THIS file —
keep it small, honest, and stable.

    provider = act_linux.LinuxX11Provider()
    provider.list_apps()          -> [AppInfo]         (the @ picker catalog)
    provider.icon_path(app_id)    -> str | None        (resolved icon file)
    provider.focused_app()        -> FocusInfo | None  (focused window)
    provider.verify_focus(cls)    -> bool              (send-typing guard)
    provider.run(action, ctx)     -> ActionResult      (dispatch by type)
    provider.confirm(confirm_id)  -> ActionResult      (release a pending gate)
    provider.deny(confirm_id)     -> ActionResult
    provider.pending()            -> [{id, detail, tier, age_s}]
    provider.capabilities()       -> {provider, display, tools, features}
    provider.catalog()            -> {apps, tools, ...}  (GET /act shape)
    provider.register(type, fn, tier=, catalog=)       (extension point —
        system_ctl mounts volume/MPRIS/screenshot/lock/dnd/wifi/bt/macro here)

ACTION SHAPE — two forms are accepted by run(); normalize_action() folds the
APP_API_CONTRACT wire form into the canonical one:

    canonical: {"type": "open_app", "id": "google-chrome", "tier": 1, ...}
    wire:      {"kind": "builtin", "tool": "key.press",
                "args": {"keys": "Right"}, "tier": 1}
    context:   {"kind": "context", "by_wm_class": {"firefox": {...}},
                "default": {...}}                -> context_dispatch

Canonical types implemented by act_linux (this lane):
    open_app{id|argv|exec}  focus_app{wm_class|id|wid}  close_window{wid?|wm_class?}
    move_workspace{n}  workspace{n|next|prev}  tile{side:left|right}
    maximize{on?}  move_monitor{n|next}
    key_press{keys}  type_text{text}          (focus-guarded — see below)
    open_url{url}  run_argv{argv}             (argv = list or string)
    context_dispatch{per_app:{wm_class:action}, default:action}
    wait{ms}

THE GATE (honest security semantics, enforced provider-side — a malicious
composer cannot bypass it):

  * tier>=2 ALWAYS pends: run() returns ActionResult(ok=False,
    confirm_id="c..."); nothing executes until confirm(confirm_id).
    confirm_id is one-shot (popped on use — replay can't re-fire) and
    expires after CONFIRM_TTL_S.
  * first-use argv: an action carrying a user-supplied `argv` list
    (run_argv, open_app{argv}, and any registered type that declares
    plan.argv) is gated the FIRST time that exact argv is seen; confirm()
    records sha256(argv) in ~/.config/wf/approved.json (mode 600) and it
    fires directly forever after. Argv we build ourselves (xdotool args)
    and .desktop Exec resolved from `id` are NOT gated — they are
    host-controlled, not composer-controlled.
  * risk_floor(): a small basename heuristic (rm/dd/mkfs/shutdown/sudo/
    `sh -c`...) bumps any gated argv to tier>=2 — first-use AND every use.
  * focus guard: guarded types (key_press, type_text) call verify_focus()
    at fire time; expect_wm_class (action or ctx) mismatch, or an
    undeterminable focus, REFUSES — we never type into the wrong window
    (demo-review requirement).

Confirm UX (notify-send / pill) is the daemon's lane; this file only
produces pending results + the pending() list that GET /status exposes.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import tempfile
import threading
import time

TIERS = {0: "safe", 1: "normal", 2: "confirm"}
CONFIRM_TTL_S = 300          # pending confirm lifetime
MAX_PENDING = 64             # unbounded pending = memory DoS surface
MAX_DISPATCH_DEPTH = 4       # context_dispatch (and macro) recursion cap
APPROVED_PATH = os.path.expanduser("~/.config/wf/approved.json")

# Contract tool ids (APP_API_CONTRACT v2) -> canonical type + fixed args.
# Rows whose types act_linux does NOT implement are for the system_ctl lane —
# they normalize fine and fail honestly ("unknown action type") until W10
# registers a handler for them.
TOOL_MAP = {
    "app.open": ("open_app", {}),
    "app.focus": ("focus_app", {}),
    "app.close": ("close_window", {}),
    "win.tile-left": ("tile", {"side": "left"}),
    "win.tile-right": ("tile", {"side": "right"}),
    "win.maximize": ("maximize", {}),
    "win.unmaximize": ("maximize", {"on": False}),
    "win.move-monitor": ("move_monitor", {"n": "next"}),
    "win.workspace-next": ("workspace", {"n": "next"}),
    "win.workspace-prev": ("workspace", {"n": "prev"}),
    "win.to-workspace": ("move_workspace", {}),
    "media.playpause": ("media", {"op": "play_pause"}),
    "media.next": ("media", {"op": "next"}),
    "media.prev": ("media", {"op": "prev"}),
    "media.stop": ("media", {"op": "stop"}),
    "sys.volume-up": ("volume", {"op": "up"}),
    "sys.volume-down": ("volume", {"op": "down"}),
    "sys.mute": ("volume", {"op": "mute"}),
    "sys.volume": ("volume", {}),
    "sys.brightness-up": ("brightness", {"op": "up"}),
    "sys.brightness-down": ("brightness", {"op": "down"}),
    "sys.screenshot": ("screenshot", {"mode": "full"}),
    "sys.screenshot-window": ("screenshot", {"mode": "window"}),
    "sys.screenshot-region": ("screenshot", {"mode": "region"}),
    "sys.lock": ("lock", {}),
    "sys.dnd": ("dnd", {"op": "toggle"}),
    "net.wifi": ("wifi", {}),
    "net.bluetooth": ("bluetooth", {}),
    "clipboard.history": ("clipboard_history", {}),
    # quick actions: one-tap laptop controls.
    # close window parks for confirm via its tier; the rest are
    # non-destructive and fire immediately.
    "win.alt-tab": ("alt_tab", {}),
    "win.minimize": ("minimize", {}),
    "win.close": ("close_window", {}),
    "desktop.show": ("show_desktop", {}),
    "browser.new-tab": ("key_press", {"keys": "ctrl+t"}),
    "browser.close-tab": ("key_press", {"keys": "ctrl+w"}),
    "nav.scroll-up": ("scroll", {"dir": "up"}),
    "nav.scroll-down": ("scroll", {"dir": "down"}),
    "edit.copy": ("key_press", {"keys": "ctrl+c"}),
    "edit.paste": ("key_press", {"keys": "ctrl+v"}),
    "edit.undo": ("key_press", {"keys": "ctrl+z"}),
    "key.enter": ("key_press", {"keys": "Return"}),
    "open.url": ("open_url", {}),
    "type.text": ("type_text", {}),
    "key.press": ("key_press", {}),
    "command": ("run_argv", {}),
    "macro": ("macro", {}),
}

# Safety floor per canonical type — the tier an action can never go BELOW.
# (The catalog shows contract defaults; bound rows carry their own tier, and
# effective tier = max(bound, floor, risk_floor) — floors are the last line.)
TYPE_TIERS = {
    "open_app": 1, "focus_app": 1, "close_window": 1,
    "move_workspace": 1, "workspace": 1, "tile": 1, "maximize": 1,
    "move_monitor": 1,
    "key_press": 1, "type_text": 1, "open_url": 1,
    "alt_tab": 1, "minimize": 1, "show_desktop": 1, "scroll": 1,
    "run_argv": 1, "context_dispatch": 0, "wait": 0, "macro": 0,
}

# argv[0] basenames that force tier>=2 no matter what the row declares.
# Deliberately short and obvious — a floor, not a sandbox.
RISKY_CMDS = {
    "rm", "rmdir", "shred", "dd", "fdisk", "sfdisk", "parted", "wipefs",
    "mkswap", "swapon", "swapoff", "cryptsetup",
    "shutdown", "reboot", "poweroff", "halt", "init", "telinit", "kexec",
    "sudo", "doas", "pkexec", "su",
    "chmod", "chown", "chgrp",
    "kill", "killall", "pkill", "xkill",
    "mount", "umount", "losetup",
    "useradd", "userdel", "usermod", "passwd",
    "iptables", "nft", "ufw", "firewall-cmd",
    "systemctl",   # gated on subcommand below, not blanket
    "curl", "wget",  # pipe-to-shell shape — see _risky_args
}
# systemctl only counts with these subcommands; `systemctl --user status` is
# benign. Same for curl/wget: only flagged when combined with a pipe into a
# shell — argv form has no pipe, so in practice these get flagged by the
# caller-visible rule "downloads stay tier>=1 unless piped, which argv can't
# express" — i.e. curl/wget plain are NOT tier 2 (removed at exec time).
RISKY_CMDS -= {"systemctl", "curl", "wget"}
RISKY_SYSTEMCTL = {"poweroff", "reboot", "halt", "kexec", "suspend",
                   "hibernate", "hybrid-sleep", "suspend-then-hibernate",
                   "isolate", "rescue", "emergency"}
SHELL_CMDS = {"sh", "bash", "dash", "zsh", "ksh", "fish", "csh", "tcsh",
              "python", "python3", "perl", "ruby", "node", "osascript",
              "eval", "expect"}
_WRAPPERS = {"env", "nice", "ionice", "nohup", "setsid", "time",
             "stdbuf", "taskset", "chrt", "timeout", "xargs"}


# ----------------------------------------------------------------------
# Value types
# ----------------------------------------------------------------------

class AppInfo:
    """One installed .desktop app. to_dict() emits the GET /act app row."""
    __slots__ = ("id", "name", "generic", "icon", "icon_path", "exec",
                 "argv", "wm_class", "terminal", "categories", "file",
                 "workdir")

    def __init__(self, id="", name="", generic="", icon="", icon_path=None,
                 exec="", argv=None, wm_class="", terminal=False,
                 categories=None, file="", workdir=""):
        self.id = id                  # desktop-file-id (filename stem)
        self.name = name
        self.generic = generic
        self.icon = icon              # icon name as written (or abs path)
        self.icon_path = icon_path    # resolved file, or None
        self.exec = exec              # raw Exec= line (display only)
        self.argv = argv or []        # field-codes stripped, spawn-ready
        self.wm_class = wm_class      # StartupWMClass or argv[0] guess
        self.terminal = terminal
        self.categories = categories or []
        self.file = file              # the .desktop path it came from
        self.workdir = workdir        # Path= key

    def __getitem__(self, k):
        return getattr(self, k)

    def to_dict(self):
        return {"id": self.id, "desktop": os.path.basename(self.file) or
                (self.id + ".desktop"), "name": self.name,
                "generic": self.generic, "icon": self.icon,
                "icon_path": self.icon_path, "exec": self.exec,
                "wm_class": self.wm_class, "terminal": self.terminal,
                "categories": list(self.categories)}


class FocusInfo:
    """The focused window. wm_class is the (second) WM_CLASS string — the
    stable app id on X11; `instance` is the first (usually a variant)."""
    __slots__ = ("wid", "wm_class", "instance", "title", "pid")

    def __init__(self, wid=0, wm_class="", instance="", title="", pid=0):
        self.wid = int(wid or 0)
        self.wm_class = wm_class or ""
        self.instance = instance or ""
        self.title = title or ""
        self.pid = int(pid or 0)

    def __getitem__(self, k):
        return getattr(self, k)

    def to_dict(self):
        return {"wid": self.wid, "wm_class": self.wm_class,
                "instance": self.instance, "title": self.title,
                "pid": self.pid}


class ActionResult:
    """run()/confirm() outcome.

      ok=True                          fired; detail says what happened
      ok=False, confirm_id=None        refused (bad args, no target,
                                       missing tool, focus-guard, expired)
      ok=False, confirm_id="c..."      PENDING — gated; daemon must surface
                                       it and call confirm()/deny().
    """
    __slots__ = ("ok", "detail", "tier", "confirm_id", "extra")

    def __init__(self, ok, detail="", tier=0, confirm_id=None, extra=None):
        self.ok = bool(ok)
        self.detail = detail or ""
        self.tier = int(tier)
        self.confirm_id = confirm_id
        self.extra = dict(extra or {})

    @classmethod
    def done(cls, detail, tier=0, extra=None):
        return cls(True, detail, tier=tier, extra=extra)

    @classmethod
    def refused(cls, detail, tier=0, extra=None):
        return cls(False, detail, tier=tier, extra=extra)

    @classmethod
    def pending(cls, confirm_id, detail, tier, reason):
        return cls(False, detail, tier=tier, confirm_id=confirm_id,
                   extra={"pending": True, "reason": reason})

    def __getitem__(self, k):
        return getattr(self, k)

    def to_dict(self):
        d = {"ok": self.ok, "detail": self.detail, "tier": self.tier,
             "confirm_id": self.confirm_id}
        d.update(self.extra)
        return d

    def __repr__(self):
        return (f"ActionResult(ok={self.ok}, detail={self.detail!r}, "
                f"tier={self.tier}, confirm_id={self.confirm_id!r})")


class ActionPlan:
    """A resolved action ready to fire — handlers return this, run() gates
    then executes it.

      fn(ctx)   -> ActionResult | str (a bare string is wrapped as ok=True)
      tier      handler-declared floor
      describe  human text for the confirm prompt ("run command: make -C x")
      argv      user-supplied argv lists that need the first-use ledger
      guarded   enforce verify_focus() at fire time (keystrokes)
    """
    __slots__ = ("fn", "tier", "describe", "argv", "guarded")

    def __init__(self, fn, tier=0, describe="", argv=None, guarded=False):
        self.fn = fn
        self.tier = int(tier)
        self.describe = describe or "action"
        self.argv = [list(a) for a in (argv or [])]
        self.guarded = bool(guarded)


# ----------------------------------------------------------------------
# Matching + argv helpers (shared semantics — context_dispatch and the
# focus guard MUST agree on what counts as the same wm_class)
# ----------------------------------------------------------------------

def _norm(s):
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def wm_match(expected, *candidates):
    """wm_class match: normalized case-insensitive, substring either way —
    'chrome' matches 'google-chrome', 'terminal' matches 'xfce4-terminal'.
    Returns the candidate that matched, else None."""
    e = _norm(expected)
    if not e:
        return None
    for cand in candidates:
        c = _norm(cand)
        if c and (c == e or e in c or c in e):
            return cand
    return None


def _argv_hash(argv):
    """Canonical identity of an argv list for the approval ledger."""
    canon = json.dumps([str(x) for x in argv], separators=(",", ":"),
                       ensure_ascii=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _unwrap(argv):
    """Strip leading env-assignment / wrapper commands for risk inspection —
    `nice -10 rm -rf x` is still rm."""
    a = [str(x) for x in argv]
    while a:
        head = a[0]
        if "=" in head and not head.startswith(("-", "/")) and \
                head.split("=", 1)[0].isidentifier():
            a = a[1:]
            continue
        base = os.path.basename(head)
        if base in _WRAPPERS:
            a = a[1:]
            # skip the wrapper's own flag args; `timeout 5 cmd` also takes
            # one bare duration arg before the real command
            while a and a[0].startswith("-"):
                a = a[1:]
            if base == "timeout" and a and not a[0].startswith("-"):
                a = a[1:]
            continue
        break
    return a


def risk_floor(argv):
    """argv list -> minimum tier. `sh -c ...` is opaque code (argv-only is
    our no-shell guarantee, but the user typing it must always confirm);
    destructors and priv-escalators are tier 2 forever."""
    a = _unwrap(argv)
    if not a:
        return 0
    base = os.path.basename(a[0])
    if base in RISKY_CMDS:
        return 2
    if base == "systemctl" and any(x in RISKY_SYSTEMCTL for x in a[1:3]):
        return 2
    if base in SHELL_CMDS and any(x in ("-c", "-e", "-E") for x in a[1:3]):
        return 2                      # interpreter running inline code
    return 0


def describe_action(a):
    """Short human description for confirm prompts. Never echoes typed text
    into logs — only its length."""
    t = a.get("type", "?")
    label = a.get("label") or a.get("name")
    if t == "open_app":
        what = a.get("id") or " ".join(a.get("argv") or []) or a.get("exec")
    elif t == "focus_app":
        what = a.get("wm_class") or a.get("id") or "window"
    elif t == "run_argv":
        argv = a.get("argv")
        what = " ".join(argv) if isinstance(argv, list) else str(argv)
    elif t == "key_press":
        what = a.get("keys")
    elif t == "type_text":
        what = "%d chars" % len(a.get("text") or "")
    elif t == "open_url":
        what = a.get("url")
    elif t == "context_dispatch":
        what = "per-app dispatch"
    else:
        what = " ".join(str(a.get(k)) for k in ("op", "side", "n", "mode")
                        if a.get(k) is not None)
    base = f"{t} {what}".strip()
    return f"{label} ({base})" if label else base


def normalize_action(action):
    """Fold the APP_API_CONTRACT wire shape into canonical {"type": ...}.

    Returns a NEW dict or None when unrecognizable. `args` merges under
    top-level keys (top-level wins); `by_wm_class` -> `per_app`; an `argv`
    string is shlex-split (splitting for convenience — it is spawned as
    argv, never through a shell)."""
    if not isinstance(action, dict):
        return None
    a = dict(action)
    for k, v in (a.get("args") or {}).items():
        a.setdefault(k, v)
    if "type" not in a:
        tool = str(a.get("tool") or "").strip()
        kind = str(a.get("kind") or "").strip().lower()
        if a.get("per_app") is not None or a.get("by_wm_class") is not None \
                or kind == "context":
            a["type"] = "context_dispatch"
        elif a.get("steps") is not None or kind == "macro":
            a["type"] = "macro"
        elif tool and tool in TOOL_MAP:
            a["type"], fixed = TOOL_MAP[tool]
            for k, v in fixed.items():
                a.setdefault(k, v)
        elif tool:
            a["type"] = tool            # registered contract ids pass through
        elif kind == "app" or a.get("id") or a.get("desktop"):
            a["type"] = "open_app"
        elif a.get("argv") is not None or kind == "command":
            a["type"] = "run_argv"
        else:
            return None
    a["per_app"] = a.get("per_app") if a.get("per_app") is not None \
        else a.get("by_wm_class")
    if isinstance(a.get("argv"), str):
        try:
            a["argv"] = shlex.split(a["argv"])
        except ValueError:
            return None
    if "keys" in a and isinstance(a["keys"], (list, tuple)):
        a["keys"] = " ".join(str(k) for k in a["keys"])
    return a


# ----------------------------------------------------------------------
# Approval ledger + confirm gate
# ----------------------------------------------------------------------

class ConfirmLedger:
    """~/.config/wf/approved.json — sha256(argv) -> record. mode 600,
    atomic write (same discipline as signbank.save). A corrupt file is
    moved aside and treated as EMPTY: the failure direction is "re-ask",
    never "allow"."""

    def __init__(self, path=APPROVED_PATH):
        self.path = path
        self.rows = {}
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("argv"), dict):
                self.rows = d["argv"]
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            # fail closed: nothing is approved; keep the evidence
            try:
                os.replace(self.path, self.path + ".corrupt-%d" %
                           int(time.time()))
            except OSError:
                pass
            self.rows = {}

    def approved(self, argv):
        with self._lock:
            return _argv_hash(argv) in self.rows

    def record(self, argv):
        """Mark argv approved (idempotent) + bump use count. Returns hash."""
        h = _argv_hash(argv)
        with self._lock:
            r = self.rows.get(h)
            if r is None:
                r = {"argv": [str(x) for x in argv],
                     "first_seen": time.time(), "uses": 0}
                self.rows[h] = r
            r["uses"] = int(r.get("uses", 0)) + 1
            self._save()
        return h

    def _save(self):
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d or ".", prefix=".approved.",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"format": "wf-approved/1", "argv": self.rows},
                          f, indent=1, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


class ConfirmGate:
    """Pending-confirm registry + ledger. Thread-safe (the daemon serves
    many HTTP threads)."""

    def __init__(self, ledger=None, ttl=CONFIRM_TTL_S):
        self.ledger = ledger or ConfirmLedger()
        self.ttl = float(ttl)
        self._pend = {}                # cid -> {action, detail, tier, ts, argv}
        self._lock = threading.RLock()

    def needs(self, tier, argv_lists):
        """None when the action may fire now; else the reason string."""
        if tier >= 2:
            return "tier-%d always confirms" % tier
        unapproved = [a for a in argv_lists if not self.ledger.approved(a)]
        if unapproved:
            return "first use of command"
        return None

    def pend(self, action, describe, tier, argv_lists):
        self._sweep()
        with self._lock:
            if len(self._pend) >= MAX_PENDING:
                oldest = min(self._pend, key=lambda k: self._pend[k]["ts"])
                del self._pend[oldest]
            cid = "c" + secrets.token_hex(6)
            self._pend[cid] = {"action": action, "detail": describe,
                               "tier": tier, "ts": time.time(),
                               "argv": [list(x) for x in argv_lists]}
            return cid

    def pop(self, cid):
        """One-shot. Returns the pending entry or None (unknown/expired)."""
        self._sweep()
        with self._lock:
            return self._pend.pop(cid, None)

    def approve(self, p):
        for argv in p.get("argv") or []:
            self.ledger.record(argv)

    def pending(self):
        self._sweep()
        with self._lock:
            now = time.time()
            return [{"id": cid, "detail": p["detail"], "tier": p["tier"],
                     "age_s": round(now - p["ts"], 1)}
                    for cid, p in sorted(self._pend.items(),
                                         key=lambda kv: kv[1]["ts"])]

    def _sweep(self):
        now = time.time()
        with self._lock:
            for cid in [c for c, p in self._pend.items()
                        if now - p["ts"] > self.ttl]:
                del self._pend[cid]


# ----------------------------------------------------------------------
# The provider base — run() orchestration lives here so every OS port and
# every registered extension shares ONE gate.
# ----------------------------------------------------------------------

class ActionProvider:
    """Abstract base + the gate. Subclasses (act_linux) implement the five
    contract methods and install handlers via register()."""

    name = "abstract"

    def __init__(self, gate=None, ledger_path=None):
        self.gate = gate or ConfirmGate(
            ledger=ConfirmLedger(ledger_path) if ledger_path else None)
        self._handlers = {}
        self._tool_rows = []
        self.register("wait", self._plan_wait, tier=0)
        self.register("context_dispatch", self._plan_context, tier=0)

    # -- contract surface (subclass implements) ---------------------------

    def list_apps(self):
        return []

    def icon_path(self, app_id):
        return None

    def focused_app(self):
        return None

    def verify_focus(self, expect_wm_class=None):
        # ctx-dict callers (macro step guard) carry the expectation under
        # expect_wm_class/expected_class — unwrap either shape.
        if isinstance(expect_wm_class, dict):
            expect_wm_class = (expect_wm_class.get("expect_wm_class") or
                               expect_wm_class.get("expected_class"))
        f = self.focused_app()
        # WF_TYPE_ALLOW=<regex>: test rigs pin every guarded action
        # (keystrokes, typed text, window ops) to a dedicated scratch
        # window. Match runs against class/instance/title — some windows
        # (Chrome headless, some toolkits) report no WM_CLASS, so the title
        # is what carries the scratch name. Unset on real laptops ->
        # behavior unchanged. Bad pattern refuses safe.
        allow = os.environ.get("WF_TYPE_ALLOW", "").strip()
        if allow and f is not None:
            hay = " ".join(x for x in (f.wm_class, f.instance, f.title) if x)
            try:
                if not re.search(allow, hay, re.I):
                    return False
            except re.error:
                return False
            # matched the rig's window — an expect_wm_class expectation
            # still applies, otherwise focus is proven
            if expect_wm_class is None:
                return True
        if f is None or not (f.wm_class or f.instance):
            return False                       # can't see focus -> refuse
        # WF_TYPE_ALLOW=<regex>: test rigs pin every guarded action
        # (keystrokes, typed text, window ops) to a dedicated scratch
        # window. Match runs against class/instance/title so a titled
        # sink like "wispr-wrap-merge-sink" is enough. Unset on real
        # laptops -> behavior unchanged. Bad pattern refuses safe.
        allow = os.environ.get("WF_TYPE_ALLOW", "").strip()
        if allow:
            hay = " ".join(x for x in (f.wm_class, f.instance, f.title) if x)
            try:
                if not re.search(allow, hay, re.I):
                    return False
            except re.error:
                return False
        if expect_wm_class is None:
            return True
        return wm_match(expect_wm_class, f.wm_class, f.instance) is not None

    # -- extension point ---------------------------------------------------

    def register(self, type_name, handler, tier=None, catalog=None):
        """Mount an action type. handler(action_dict, ctx) -> ActionPlan or
        ActionResult(early refusal). `catalog` = one GET /act tool row."""
        self._handlers[str(type_name)] = handler
        if tier is not None:
            TYPE_TIERS[str(type_name)] = int(tier)
        if catalog:
            self._tool_rows.append(dict(catalog))

    def _tier_floor(self, t):
        return TYPE_TIERS.get(t, 1)    # unknown/registered types: normal

    # -- the gate ----------------------------------------------------------

    def run(self, action, ctx=None):
        """normalize -> plan -> gate -> execute. Never raises to the wire."""
        ctx = dict(ctx or {})
        try:
            a = normalize_action(action)
        except Exception:
            a = None
        if a is None:
            return ActionResult.refused("malformed action")
        t = str(a.get("type") or "")
        h = self._handlers.get(t)
        if h is None:
            return ActionResult.refused(f"unknown action type '{t}'")
        if int(ctx.get("depth", 0)) >= MAX_DISPATCH_DEPTH:
            return ActionResult.refused("dispatch depth exceeded")
        try:
            plan = h(a, ctx)
        except Exception as e:
            return ActionResult.refused(f"{t}: {type(e).__name__}: {e}")
        if isinstance(plan, ActionResult):
            return plan
        tier = max(plan.tier, self._tier_floor(t),
                   _as_int(a.get("tier"), 0))
        for argv in plan.argv:
            tier = max(tier, risk_floor(argv))
        if not ctx.get("_confirmed"):
            reason = self.gate.needs(tier, plan.argv)
            if reason:
                cid = self.gate.pend(a, plan.describe, tier, plan.argv)
                return ActionResult.pending(
                    cid, f"needs confirm ({reason}): {plan.describe}",
                    tier, reason)
        return self._exec(t, plan, a, ctx, tier)

    def _exec(self, t, plan, a, ctx, tier):
        """Focus guard, then run the plan. Guard reads focus at FIRE time —
        a window that changed between plan and exec is the bug this exists
        for."""
        try:
            if plan.guarded:
                expect = a.get("expect_wm_class") or ctx.get("expect_wm_class")
                if not self.verify_focus(expect):
                    f = self.focused_app()
                    got = (f.wm_class or f.instance) if f else "?"
                    return ActionResult.refused(
                        f"focus-refused: expected "
                        f"'{expect or 'any-window'}', focused '{got}'",
                        tier, extra={"focus": got})
            out = plan.fn(ctx)
        except Exception as e:
            return ActionResult.refused(f"{t}: {type(e).__name__}: {e}")
        if isinstance(out, ActionResult):
            if out.tier == 0 and tier:
                out.tier = tier
            return out
        return ActionResult.done(str(out) if out else plan.describe, tier)

    def confirm(self, confirm_id, ctx=None):
        """Release a pending action. The confirm_id binds the exact pending
        action — the daemon confirms an id, never a re-sent action."""
        p = self.gate.pop(confirm_id)
        if p is None:
            return ActionResult.refused(
                f"no pending confirm '{confirm_id}' (expired/denied/bogus)")
        self.gate.approve(p)
        ctx = dict(ctx or {})
        ctx["_confirmed"] = True
        return self.run(p["action"], ctx)

    def deny(self, confirm_id):
        p = self.gate.pop(confirm_id)
        if p is None:
            return ActionResult.refused(f"no pending confirm '{confirm_id}'")
        return ActionResult.done(f"denied: {p['detail']}")

    def pending(self):
        return self.gate.pending()

    def capabilities(self):
        return {"provider": self.name, "display": "unknown",
                "tools": {}, "features": {}}

    def catalog(self):
        """GET /act payload: apps + tool rows + capabilities. system_ctl
        appends its own rows through register(catalog=...)."""
        return {"ok": True, "display": self.capabilities().get("display"),
                "provider": self.name,
                "apps": [a.to_dict() for a in self.list_apps()],
                "tools": list(self._tool_rows),
                "capabilities": self.capabilities()}

    # -- built-in plans ----------------------------------------------------

    def _plan_wait(self, a, ctx):
        ms = _as_int(a.get("ms", a.get("delay_ms", 250)), 250)
        ms = min(max(ms, 0), 30000)    # a step can never sleep forever

        def fn(_ctx):
            time.sleep(ms / 1000.0)
            return f"waited {ms}ms"
        return ActionPlan(fn, tier=0, describe=f"wait {ms}ms")

    def _plan_context(self, a, ctx):
        per_app = a.get("per_app") or {}
        default = a.get("default")
        if not per_app and default is None:
            return ActionResult.refused("context_dispatch: empty table")
        for k, v in list(per_app.items()):
            if not isinstance(v, dict):
                return ActionResult.refused(
                    f"context_dispatch: rule for '{k}' is not an action")
        if default is not None and not isinstance(default, dict):
            return ActionResult.refused("context_dispatch: default is not "
                                        "an action")

        def fn(_ctx):
            f = self.focused_app()
            actual = (f.wm_class if f else "") or ""
            chosen, rule = None, None
            for key, act in per_app.items():
                if wm_match(key, actual, f.instance if f else ""):
                    chosen, rule = key, act
                    break
            if rule is None:
                if default is None:
                    return ActionResult.refused(
                        "context: focused '%s' matches no rule and no "
                        "default" % (actual or "?"),
                        extra={"focus": actual})
                chosen, rule = "default", default
            child_ctx = dict(_ctx)
            child_ctx.pop("_confirmed", None)   # a gated child confirms itself
            child_ctx["depth"] = int(child_ctx.get("depth", 0)) + 1
            r = self.run(rule, child_ctx)
            r.extra.setdefault("context", {"rule": chosen, "focus": actual})
            return r
        return ActionPlan(fn, tier=_as_int(a.get("tier"), 0),
                          describe=describe_action(a))


def _as_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default
