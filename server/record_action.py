#!/usr/bin/env python3
"""record_action — teach a sign by DEMONSTRATING the action (spec addendum).

The user presses Record, does the thing on the laptop (types a command,
hits shortcuts, clicks, waits for a window), presses Stop, and gets a
replayable step list — a macro — that binds to a sign like any other
action. This is the "show it once" lane: faster than hand-building a
macro in a UI, and honest about what it saw.

CAPTURE
=======
X11 only. `xinput test-xi2 --root` selects every XI2 event class on the
root window; cooked key/button events delivered to any window propagate
up to root, so the stream is global WITHOUT grabbing devices or
injecting anything. We spawn it argv-only for the record window and
parse its text blocks:

    EVENT type 2 (KeyPress)
        device: 3
        detail: 38
        flags:
        root: 1072.30/640.36
        event: 1072.30/640.36
        buttons:
        modifiers: locked 0x0 latched 0x0 base 0x0 effective: 0x0
        group: locked 0x0 latched 0x0 base 0x0
        valuators:
        windows: root 0x25a event 0x25a child 0x0

Only cooked events are used: type 2/3 KeyPress/KeyRelease, type 4/5
ButtonPress/ButtonRelease. Raw* events (13-16) carry the same physical
press a second time and are ignored, as is Motion (6) — pointer paths
are not replayable actions (positions are captured on button events
instead). `flags: repeat` on a KeyPress is XKB autorepeat: for a
printable key it appends another character; for a chord it folds into
the step's `repeat` count instead of duplicating steps.

test-xi2 prints NO timestamps — each event is stamped when its "EVENT"
line is read off the pipe (sub-ms pipe latency at this event rate;
macro timing precision is ~10ms at worst and waits are rounded to
50ms anyway).

MACRO STEP CONTRACT (the file output — what the action provider runs)
====================================================================
~/.config/wf/recordings/<name>.json (mode 600, atomic write):

    {"format": "wf-macro/1", "name": ..., "engine": "record_action/xinput-test-xi2",
     "created_ms": ..., "duration_ms": ..., "truncated": bool,
     "display": "x11", "context": {"wm_class": ..., "title": ...},
     "steps": [ <step>, ... ], "events": [ <normalized event>, ... ]}

A step is a SINGLE-KEY dict — the key is the verb the ActionProvider
dispatches on (the macro engine contract):

    {"type":  "<text>"}          type literal text at the cursor
    {"keys":  "<chord>"}         one key press; xdotool syntax —
                                 "Return", "ctrl+t", "ctrl+shift+Escape";
                                 optional "repeat": n when the same press
                                 repeated (autorepeat fold, default 1)
    {"wait_ms": <int>}           pause, rounded to 50ms; only gaps
                                 >= wait_min_ms become waits
    {"open_app": "<exec>"}       launch an installed app — NEVER emitted
                                 by the recorder (key/button events can't
                                 reveal launches); the field exists for
                                 hand-edited steps and app-context binds
    {"argv":   ["<argv0>", ...]} spawn argv directly, no shell — used for
                                 positioned pointer work: a click records
                                 ["xdotool","mousemove",X,Y,"click",N],
                                 double/scroll via click --repeat N

`events` is the normalized raw stream (kind/down/code/sym/t_ms/pos) kept
for audit and re-normalization — the recorded keys may be sensitive
(it can contain typed passwords), hence mode 600 like the sign bank.

to_bank_action(macro) wraps steps as {"kind":"macro","tier":2,"steps":..}
for PUT /bank; to_tool_steps(steps) adapts to the app-contract
{tool,args,delay_ms} step shape if W10's engine reads that instead.

KEYCODES -> KEYSYMS
===================
`xmodmap -pke` (parsed once at record start) gives the LIVE layout's
keycode -> (base, shifted, level3, level3+shift) keysyms, so recordings
on non-US layouts type the right characters. Fallback when xmodmap is
absent: an embedded US-QWERTY evdev table (X keycode = evdev + 8) —
marked keymap:"fallback-us" in the file, an honest degradation, and raw
keycodes are kept in `events` regardless.

HONEST DEGRADES (probe_display -> reason; start() refuses, never guesses)
=========================================================================
  wayland        XDG_SESSION_TYPE=wayland or WAYLAND_DISPLAY set —
                 test-xi2 on Wayland only sees XWayland-bound events:
                 capture is silently INCOMPLETE, so we refuse outright.
  no-display     no DISPLAY — nothing to record.
  xinput-missing the xinput binary is not installed.
X11 but no xdotool/xmodmap: recording still runs; context hint is empty
and the fallback keymap is used.

LIMITATIONS (documented, not hidden)
====================================
* Clicks replay by absolute position — same-window-layout assumption;
  context.wm_class is the hint for the replay/confirm layer to check.
* Pointer motion, drags and scroll deltas beyond click/wheel are not
  recorded (motion volume vs replay value — deliberate).
* Caps_Lock/Num_Lock state and AltGr chords on exotic layouts are
  approximated (altgr handled via xmodmap level-3 column; caps ignored).
* Application launches are invisible to input events — open_app steps
  are never synthesized; bind them manually if needed.
* ~30s / ~150-step caps (env-tunable): a recording is a sign's action,
  not a keystroke logger. Truncation is reported, never silent.

Env knobs: WF_REC_MAX_MS, WF_REC_MAX_STEPS, WF_REC_WAIT_MIN_MS,
WF_REC_DIR.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.expanduser(
    os.environ.get("WF_REC_DIR", "~/.config/wf/recordings"))

MAX_MS = int(os.environ.get("WF_REC_MAX_MS", "30000"))
MAX_STEPS = int(os.environ.get("WF_REC_MAX_STEPS", "150"))
WAIT_MIN_MS = int(os.environ.get("WF_REC_WAIT_MIN_MS", "500"))
WAIT_ROUND_MS = 50
CLICK_DBL_MS = 400          # same-button clicks closer than this merge
CLICK_POS_EPS = 8           # px of jitter still counted as same spot
MAX_EVENTS_KEPT = 4000      # normalized events persisted for audit
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

XINPUT_ARGV = ["xinput", "test-xi2", "--root"]

# XI2 cooked event types we use (raw 13-16 + motion 6 + all else skipped)
EV = {2: "key", 3: "key", 4: "button", 5: "button"}
EV_DOWN = {2: True, 3: False, 4: True, 5: False}

# keysym -> canonical chord modifier name (xdotool syntax)
MODIFIERS = {
    "Shift_L": "shift", "Shift_R": "shift",
    "Control_L": "ctrl", "Control_R": "ctrl",
    "Alt_L": "alt", "Alt_R": "alt",
    "Meta_L": "alt", "Meta_R": "alt",
    "Super_L": "super", "Super_R": "super",
    "Hyper_L": "super", "Hyper_R": "super",
}
LEVEL3 = {"ISO_Level3_Shift"}     # AltGr: shapes chars, not a chord mod
MOD_ORDER = ("ctrl", "alt", "shift", "super")

# keysym name -> literal char for the typing channel
KEYSYM_CHAR = {
    "space": " ", "exclam": "!", "quotedbl": '"', "numbersign": "#",
    "dollar": "$", "percent": "%", "ampersand": "&", "apostrophe": "'",
    "parenleft": "(", "parenright": ")", "asterisk": "*", "plus": "+",
    "comma": ",", "minus": "-", "period": ".", "slash": "/",
    "colon": ":", "semicolon": ";", "less": "<", "equal": "=",
    "greater": ">", "question": "?", "at": "@",
    "bracketleft": "[", "backslash": "\\", "bracketright": "]",
    "asciicircum": "^", "underscore": "_", "grave": "`",
    "braceleft": "{", "bar": "|", "braceright": "}", "asciitilde": "~",
}

# Fallback US-QWERTY map, X keycode = evdev + 8 -> (base, shifted).
# Used only when `xmodmap -pke` is unavailable; marked in the output.
_US_ROWS = (
    (9, "Escape"), (10, "1", "exclam"), (11, "2", "at"), (12, "3", "numbersign"),
    (13, "4", "dollar"), (14, "5", "percent"), (15, "6", "asciicircum"),
    (16, "7", "ampersand"), (17, "8", "asterisk"), (18, "9", "parenleft"),
    (19, "0", "parenright"), (20, "minus", "underscore"), (21, "equal", "plus"),
    (22, "BackSpace"), (23, "Tab"),
    (24, "q", "Q"), (25, "w", "W"), (26, "e", "E"), (27, "r", "R"),
    (28, "t", "T"), (29, "y", "Y"), (30, "u", "U"), (31, "i", "I"),
    (32, "o", "O"), (33, "p", "P"), (34, "bracketleft", "braceleft"),
    (35, "bracketright", "braceright"), (36, "Return"), (37, "Control_L"),
    (38, "a", "A"), (39, "s", "S"), (40, "d", "D"), (41, "f", "F"),
    (42, "g", "G"), (43, "h", "H"), (44, "j", "J"), (45, "k", "K"),
    (46, "l", "L"), (47, "semicolon", "colon"), (48, "apostrophe", "quotedbl"),
    (49, "grave", "asciitilde"), (50, "Shift_L"), (51, "backslash", "bar"),
    (52, "z", "Z"), (53, "x", "X"), (54, "c", "C"), (55, "v", "V"),
    (56, "b", "B"), (57, "n", "N"), (58, "m", "M"),
    (59, "comma", "less"), (60, "period", "greater"), (61, "slash", "question"),
    (62, "Shift_R"), (63, "KP_Multiply"), (64, "Alt_L"), (65, "space"),
    (66, "Caps_Lock"),
    (67, "F1"), (68, "F2"), (69, "F3"), (70, "F4"), (71, "F5"),
    (72, "F6"), (73, "F7"), (74, "F8"), (75, "F9"), (76, "F10"),
    (77, "Num_Lock"), (78, "Scroll_Lock"),
    (79, "KP_7"), (80, "KP_8"), (81, "KP_9"), (82, "KP_Subtract"),
    (83, "KP_4"), (84, "KP_5"), (85, "KP_6"), (86, "KP_Add"),
    (87, "KP_1"), (88, "KP_2"), (89, "KP_3"), (90, "KP_0"), (91, "KP_Decimal"),
    (94, "less", "greater"), (95, "F11"), (96, "F12"),
    (104, "KP_Enter"), (105, "Control_R"), (106, "KP_Divide"), (107, "Print"),
    (108, "ISO_Level3_Shift"),
    (110, "Home"), (111, "Up"), (112, "Prior"), (113, "Left"), (114, "Right"),
    (115, "End"), (116, "Down"), (117, "Next"), (118, "Insert"),
    (119, "Delete"), (121, "XF86AudioMute"), (122, "XF86AudioLowerVolume"),
    (123, "XF86AudioRaiseVolume"), (127, "Pause"), (133, "Super_L"),
    (134, "Super_R"), (135, "Menu"), (156, "KP_Enter"),
    (171, "XF86AudioNext"), (172, "XF86AudioPlay"), (173, "XF86AudioPrev"),
    (174, "XF86AudioStop"),
)
EVDEV_KEYMAP = {}
for _row in _US_ROWS:
    EVDEV_KEYMAP[_row[0]] = _row[1:]


# ---------------------------------------------------------------------
# environment probe — honest refuses, never silent partial capture
# ---------------------------------------------------------------------

def probe_display(environ=None):
    """What this box can record. {"ok","display","reason",tools...}.
    display: "x11" | "wayland" | "none". ok=False => reason is set."""
    env = os.environ if environ is None else environ
    wayland = bool(env.get("WAYLAND_DISPLAY")) or \
        env.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    display = env.get("DISPLAY", "")
    tools = {t: bool(shutil.which(t)) for t in
             ("xinput", "xmodmap", "xdotool", "xprop")}
    out = {"display": "wayland" if wayland else ("x11" if display else "none"),
           "reason": None, **tools}
    if wayland:
        # test-xi2 on Wayland only receives events bound for XWayland
        # windows — a recording there is silently incomplete. Refuse.
        out["reason"] = ("wayland: test-xi2 sees only XWayland-bound "
                         "events; capture would be silently incomplete")
        out["ok"] = False
    elif not display:
        out["reason"] = "no-display: DISPLAY is not set"
        out["ok"] = False
    elif not tools["xinput"]:
        out["reason"] = "xinput-missing: install xinput (x11-xinput-apps)"
        out["ok"] = False
    else:
        out["ok"] = True
    return out


def focused_context(environ=None):
    """Focused-app hint at record start (xdotool, then xprop — same
    probes wfd uses). Best effort: missing tools -> empty fields."""
    env = os.environ if environ is None else environ
    if not env.get("DISPLAY"):
        return {"wm_class": "", "title": ""}
    wid = ""
    try:
        if shutil.which("xdotool"):
            wid = subprocess.run(
                ["xdotool", "getactivewindow"], capture_output=True,
                text=True, timeout=5).stdout.strip()
        if not wid.isdigit():
            if shutil.which("xprop"):
                out = subprocess.run(
                    ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                    capture_output=True, text=True, timeout=5).stdout
                m = re.search(r"0x[0-9a-fA-F]+", out)
                wid = m.group(0) if m else ""
            else:
                return {"wm_class": "", "title": ""}
        cls, title = "", ""
        if wid:
            if shutil.which("xdotool"):
                title = subprocess.run(
                    ["xdotool", "getwindowname", wid], capture_output=True,
                    text=True, timeout=5).stdout.strip()
                c = subprocess.run(
                    ["xdotool", "getwindowclassname", wid],
                    capture_output=True, text=True, timeout=5).stdout.strip()
                cls = c if "failed" not in c.lower() else ""
            if not cls and shutil.which("xprop"):
                wm = subprocess.run(
                    ["xprop", "-id", wid, "WM_CLASS"], capture_output=True,
                    text=True, timeout=5).stdout
                names = re.findall(r'"([^"]+)"', wm)
                cls = names[-1] if names else ""
        return {"wm_class": cls.lower(), "title": title}
    except Exception:
        return {"wm_class": "", "title": ""}


# ---------------------------------------------------------------------
# keymap: xmodmap -pke -> {keycode: (base, shifted, lvl3, lvl3shift)}
# ---------------------------------------------------------------------

def parse_xmodmap(text):
    """`xmodmap -pke` lines: 'keycode  38 = a A a A adiaeresis Adiaeresis'
    -> {38: ("a","A","adiaeresis","Adiaeresis")}. Tolerant of =/NoSymbol."""
    km = {}
    for line in text.splitlines():
        m = re.match(r"\s*keycode\s+(\d+)\s*=\s*(.*)", line)
        if not m:
            continue
        syms = [s for s in m.group(2).split() if s != "NoSymbol"]
        if syms:
            km[int(m.group(1))] = tuple(syms[:4])
    return km


def load_keymap(environ=None):
    """(keymap, source): live `xmodmap -pke` map, else embedded US table."""
    env = os.environ if environ is None else environ
    if env.get("DISPLAY") and shutil.which("xmodmap"):
        try:
            out = subprocess.run(["xmodmap", "-pke"], capture_output=True,
                                 text=True, timeout=8).stdout
            km = parse_xmodmap(out)
            if km:
                return km, "xmodmap"
        except Exception:
            pass
    return dict(EVDEV_KEYMAP), "fallback-us"


def _keysym_char(sym):
    if not sym:
        return None
    if len(sym) == 1 and sym.isprintable():
        return sym
    return KEYSYM_CHAR.get(sym)


# ---------------------------------------------------------------------
# test-xi2 block parser — incremental, one EVENT block at a time
# ---------------------------------------------------------------------

_EVENT_RE = re.compile(r"^EVENT type (\d+) \((\w+)\)")
_FIELD_RE = re.compile(r"^\s+(\w[\w ]*?):\s*(.*)$")


class EventParser:
    """Feed raw stdout lines; get one dict per completed event block.
    Non-event lines (banners, xinput chatter) are skipped."""

    def __init__(self):
        self._cur = None

    def feed(self, line):
        m = _EVENT_RE.match(line)
        if m:
            done, self._cur = self._cur, {
                "evtype": int(m.group(1)), "name": m.group(2),
                "detail": None, "device": None, "flags": "", "root": None}
            return done
        if self._cur is None:
            return None
        f = _FIELD_RE.match(line)
        if not f:
            return None
        key, val = f.group(1).strip(), f.group(2).strip()
        if key == "detail":
            try:
                self._cur["detail"] = int(val)
            except ValueError:
                pass
        elif key == "device":
            try:
                self._cur["device"] = int(val)
            except ValueError:
                pass
        elif key == "flags":
            self._cur["flags"] = val
        elif key == "root":
            p = val.split("/")
            try:
                self._cur["root"] = (float(p[0]), float(p[1]))
            except (IndexError, ValueError):
                pass
        return None

    def flush(self):
        done, self._cur = self._cur, None
        return done


def iter_events(lines):
    """Parse a whole transcript -> cooked key/button event dicts only."""
    p = EventParser()
    for line in lines:
        ev = p.feed(line)
        if ev is not None:
            yield ev
    ev = p.flush()
    if ev is not None:
        yield ev


# ---------------------------------------------------------------------
# normalization: parsed events -> macro steps
# ---------------------------------------------------------------------

class _CapHit(Exception):
    pass


def normalize(events, keymap, *, wait_min_ms=WAIT_MIN_MS, max_ms=MAX_MS,
              max_steps=MAX_STEPS):
    """Parsed event dicts (each needs t_ms) -> (steps, meta).

    Coalescing rules, all documented in the module header:
      printable key (+shift/altgr via keymap) -> append to pending type
      modifier+key or non-printable key        -> {"keys": chord}
      held-key autorepeat (flags:repeat)       -> char append / repeat++
      a lone modifier press+release            -> {"keys": <keysym>}
      button press+release                     -> argv xdotool click step
      gap >= wait_min_ms before any new step   -> {"wait_ms": rounded}
    """
    steps = []
    typed = []                       # pending literal chars
    mods = set()                     # held canonical modifiers
    mod_used = {}                    # canon mod -> a real key used it
    lone_mod = {}                    # canon mod -> (press t, keysym)
    level3 = False
    btn = {}                         # button -> (t_ms, x, y)
    prev_t = None
    t0 = None
    truncated = False
    n_in = 0

    def finish_type():
        if typed:
            steps.append({"type": "".join(typed)})
            typed.clear()

    def emit(step):
        nonlocal truncated
        if "keys" in step and steps and \
                set(steps[-1]) <= {"keys", "repeat"} and \
                steps[-1]["keys"] == step["keys"]:
            steps[-1]["repeat"] = steps[-1].get("repeat", 1) + 1
            return
        if "wait_ms" in step and steps and \
                set(steps[-1]) == {"wait_ms"}:
            steps[-1]["wait_ms"] += step["wait_ms"]
            return
        steps.append(step)
        if len(steps) >= max_steps:
            truncated = True
            raise _CapHit

    def maybe_wait(t):
        nonlocal prev_t
        if prev_t is not None and t - prev_t >= wait_min_ms:
            finish_type()
            ms = int(round((t - prev_t) / WAIT_ROUND_MS)) * WAIT_ROUND_MS
            emit({"wait_ms": ms})
        prev_t = t

    def emit_click(b, x, y, t):
        """click step; same spot+button within CLICK_DBL_MS folds into
        --repeat N (double-click, wheel spin)."""
        if steps and steps[-1].get("argv", [])[:2] == \
                ["xdotool", "mousemove"]:
            av = steps[-1]["argv"]
            try:
                lx, ly = float(av[2]), float(av[3])
            except (IndexError, ValueError):
                lx = ly = None
            if lx is not None and av[-1] == str(b) and \
                    abs(lx - x) <= CLICK_POS_EPS and abs(ly - y) <= CLICK_POS_EPS:
                rep = 1
                if "--repeat" in av:
                    rep = int(av[av.index("--repeat") + 1])
                if rep < 4:
                    na = ["xdotool", "mousemove", str(int(lx)), str(int(ly)),
                          "click", "--repeat", str(rep + 1), str(b)]
                    steps[-1] = {"argv": na}
                    return
        av = ["xdotool", "mousemove", str(int(x)), str(int(y)),
              "click", str(b)]
        emit({"argv": av})

    try:
        for ev in events:
            if ev is None:
                continue
            # Accept BOTH event shapes:
            #  parser shape  (evtype/detail/flags/root) — transcripts fed
            #                straight to normalize();
            #  audit shape   (kind/down/code/pos/repeat) — _norm_event
            #                output, what live captures actually store.
            # Feeding audit dicts through the evtype branch used to drop
            # every event -> recordings always produced empty steps.
            if "evtype" in ev:
                et = ev.get("evtype")
                if et not in EV:
                    continue                  # raw/motion/etc — not actions
                kind, down = EV[et], EV_DOWN[et]
                detail = ev.get("detail")
                repeat_flag = "repeat" in (ev.get("flags") or "")
                root = ev.get("root")
            else:
                kind, down = ev.get("kind"), bool(ev.get("down"))
                if kind not in ("key", "button"):
                    continue
                detail = ev.get("code")
                repeat_flag = bool(ev.get("repeat"))
                pos = ev.get("pos")
                root = (float(pos[0]), float(pos[1])) \
                    if isinstance(pos, (list, tuple)) and len(pos) == 2 \
                    else None
            if detail is None:
                continue
            t = ev.get("t_ms", 0)
            if t0 is None:
                t0 = t
            if t - t0 > max_ms:
                truncated = True
                break
            n_in += 1
            if kind == "key":
                syms = keymap.get(detail)
                base = syms[0] if syms else None
                canon = MODIFIERS.get(base)
                if down:
                    repeat = repeat_flag
                    if canon:
                        if not repeat:
                            mods.add(canon)
                            mod_used.setdefault(canon, False)
                            lone_mod[canon] = (t, base)
                        prev_t = t
                        continue
                    if base in LEVEL3:
                        level3 = True
                        prev_t = t
                        continue
                    maybe_wait(t)
                    # char channel: plain / shift / altgr columns
                    ch = None
                    if mods <= {"shift"}:
                        col = 2 if level3 and len(syms or ()) > 2 else \
                            (1 if "shift" in mods and len(syms or ()) > 1
                             else 0)
                        ch = _keysym_char((syms or (None,))[col]
                                          if syms and len(syms) > col else base)
                    if ch is not None:
                        typed.append(ch)
                    else:
                        finish_type()
                        spec = "+".join([m for m in MOD_ORDER if m in mods]
                                        + [base or f"keycode_{detail}"])
                        emit({"keys": spec})
                else:  # release
                    if canon:
                        used = mod_used.get(canon, True)
                        lone = lone_mod.pop(canon, None)
                        mods.discard(canon)
                        # modifier pressed + released with nothing between:
                        # it IS the action (e.g. tap Super for the launcher)
                        if lone is not None and not used:
                            maybe_wait(t)
                            finish_type()
                            emit({"keys": lone[1]})
                        mod_used.pop(canon, None)
                        prev_t = t
                        continue
                    if base in LEVEL3:
                        level3 = False
                        prev_t = t
                        continue
                    prev_t = t
            else:  # button
                root = root or (0.0, 0.0)
                if down:
                    btn[detail] = (t, root[0], root[1])
                    prev_t = t
                    continue
                pt = btn.pop(detail, None)
                maybe_wait(t)
                finish_type()
                x, y = (pt[1], pt[2]) if pt else (root[0], root[1])
                emit_click(detail, x, y, t)
            # any non-modifier press while a mod is held marks it used
            if down:
                for c in lone_mod:
                    mod_used[c] = True
        finish_type()
    except _CapHit:
        finish_type()

    # trim leading/trailing waits — replay starts and ends with intent
    while steps and "wait_ms" in steps[0]:
        steps.pop(0)
    while steps and "wait_ms" in steps[-1]:
        steps.pop()
    meta = {"n_events_in": n_in, "truncated": truncated}
    return steps, meta


# ---------------------------------------------------------------------
# macro assembly + storage
# ---------------------------------------------------------------------

def _now_ms():
    return int(time.time() * 1000)


def build_macro(name, norm_events, keymap, *, context=None, display="x11",
                keymap_src="unknown", source="recorded",
                wait_min_ms=WAIT_MIN_MS, max_ms=MAX_MS, max_steps=MAX_STEPS):
    """Normalized raw events -> the wf-macro/1 file dict."""
    steps, meta = normalize(norm_events, keymap, wait_min_ms=wait_min_ms,
                            max_ms=max_ms, max_steps=max_steps)
    ts = [e["t_ms"] for e in norm_events if e.get("t_ms") is not None]
    return {
        "format": "wf-macro/1",
        "engine": "record_action/xinput-test-xi2",
        "name": name,
        "created_ms": _now_ms(),
        "duration_ms": (max(ts) - min(ts)) if ts else 0,
        "truncated": meta["truncated"],
        "display": display,
        "keymap": keymap_src,
        "context": context or {"wm_class": "", "title": ""},
        "source": source,
        "steps": steps,
        "events": norm_events[:MAX_EVENTS_KEPT],
    }


def convert(text, name="converted", *, keymap=None, wait_min_ms=WAIT_MIN_MS):
    """Saved `xinput test-xi2 --root` transcript -> macro dict, no capture.
    Timestamps are synthesized (lines arrive at 0/80ms cadence) — waits in
    a converted file reflect event ORDER, not real time."""
    p = EventParser()
    norm = []
    t = 0
    for line in text.splitlines():
        ev = p.feed(line)
        if ev is None:
            continue
        t += 80
        ev["t_ms"] = t
        if ev.get("evtype") in EV:
            norm.append(_norm_event(ev))
    ev = p.flush()
    if ev is not None and ev.get("evtype") in EV:
        ev["t_ms"] = t + 80
        norm.append(_norm_event(ev))
    km = keymap or dict(EVDEV_KEYMAP)
    return build_macro(name, norm, km, keymap_src="convert",
                       source="transcript", wait_min_ms=wait_min_ms)


def _norm_event(ev):
    """Parsed block -> stored normalized event (compact, audit-safe)."""
    out = {"kind": EV.get(ev["evtype"], "other"),
           "down": EV_DOWN.get(ev["evtype"], True),
           "code": ev.get("detail"), "t_ms": ev.get("t_ms", 0)}
    if "repeat" in (ev.get("flags") or ""):
        out["repeat"] = True
    if ev.get("root"):
        out["pos"] = [round(ev["root"][0]), round(ev["root"][1])]
    return out


def sanitize_name(name):
    name = str(name or "").strip()
    if not NAME_RE.match(name):
        raise ValueError("name must be [A-Za-z0-9_-]{1,64}")
    return name


def save_macro(macro, dir=None):
    """Atomic write, mode 600 — recorded input can contain secrets."""
    d = dir or DEFAULT_DIR
    os.makedirs(d, exist_ok=True)
    os.chmod(d, 0o700)
    path = os.path.join(d, sanitize_name(macro["name"]) + ".json")
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".macro.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(macro, f, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def load_macro(name, dir=None):
    path = os.path.join(dir or DEFAULT_DIR, sanitize_name(name) + ".json")
    with open(path) as f:
        return json.load(f)


def list_recordings(dir=None):
    d = dir or DEFAULT_DIR
    out = []
    try:
        files = sorted(os.listdir(d))
    except OSError:
        return out
    for fn in files:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn)) as f:
                m = json.load(f)
            out.append({"name": m.get("name", fn[:-5]),
                        "n_steps": len(m.get("steps", [])),
                        "duration_ms": m.get("duration_ms", 0),
                        "created_ms": m.get("created_ms", 0),
                        "truncated": m.get("truncated", False),
                        "context": m.get("context", {})})
        except (OSError, ValueError, KeyError):
            continue
    return out


def delete_recording(name, dir=None):
    path = os.path.join(dir or DEFAULT_DIR, sanitize_name(name) + ".json")
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------
# bindings for the action layer
# ---------------------------------------------------------------------

_TOOL_OF = {"type": "type.text", "keys": "key.press",
            "open_app": "app.open", "argv": "command"}


def to_bank_action(macro, tier=2):
    """The object PUT /bank stores on a sign (app-contract kind:macro)."""
    return {"kind": "macro",
            "label": str(macro.get("name", "macro")),
            "tier": int(tier),
            "steps": [dict(s) for s in macro.get("steps", [])],
            "duration_ms": macro.get("duration_ms", 0),
            "context": macro.get("context", {})}


def to_tool_steps(steps):
    """Adapter: flat {verb: arg} steps -> the app-contract step shape
    [{"tool":..,"args":{..},"delay_ms":..}] in case the macro engine
    reads that. wait_ms folds into the NEXT step's delay_ms (delay before
    it runs); a trailing wait becomes an explicit terminal wait step."""
    out, pending = [], 0
    for s in steps:
        if "wait_ms" in s:
            pending += int(s["wait_ms"])
            continue
        verb, arg = next(iter(s.items())) if len(s) == 1 else (None, None)
        if verb is None and "keys" in s:
            verb, arg = "keys", s["keys"]
        tool = _TOOL_OF.get(verb)
        if tool is None:
            continue                    # unknown verb — skip honestly
        args = {"text": arg} if tool == "type.text" else \
               {"keys": arg} if tool == "key.press" else \
               {"exec": arg} if tool == "app.open" else \
               {"argv": list(arg)}
        if isinstance(s.get("repeat"), int) and s["repeat"] > 1:
            args["repeat"] = s["repeat"]
        st = {"tool": tool, "args": args}
        if pending:
            st["delay_ms"] = pending
            pending = 0
        out.append(st)
    if pending:
        out.append({"tool": "wait_ms", "args": {"ms": pending}})
    return out


# ---------------------------------------------------------------------
# Recorder — the live capture session
# ---------------------------------------------------------------------

class Recorder:
    """start() spawns `xinput test-xi2 --root`; stop() builds + saves.

    Injection points for tests: `spawn` (returns a Popen-like object with
    .stdout lines and .terminate/.wait) and `environ`. Without them the
    real argv is spawned and os.environ probed."""

    def __init__(self, name="recording", dir=None, *, max_ms=MAX_MS,
                 max_steps=MAX_STEPS, wait_min_ms=WAIT_MIN_MS,
                 environ=None, spawn=None):
        self.name = name
        self.dir = dir
        self.max_ms = max_ms
        self.max_steps = max_steps
        self.wait_min_ms = wait_min_ms
        self._env = environ if environ is not None else os.environ
        self._spawn = spawn
        self._probe = probe_display(self._env)
        self._proc = None
        self._parser = EventParser()
        self._norm = []
        self._keymap = dict(EVDEV_KEYMAP)
        self._keymap_src = "fallback-us"
        self._context = {"wm_class": "", "title": ""}
        self._t0 = None
        self._lock = threading.Lock()
        self._truncated = False
        self._reader = None
        self._watchdog = None

    # -- session -------------------------------------------------

    def start(self):
        """Refuse honestly (probe reason) or begin capturing."""
        if self._proc is not None:
            return {"ok": False, "reason": "already-recording"}
        if not self._probe["ok"]:
            return {"ok": False, "reason": self._probe["reason"],
                    "display": self._probe["display"]}
        try:
            sanitize_name(self.name)
        except ValueError as e:
            return {"ok": False, "reason": f"name: {e}"}
        self._context = focused_context(self._env)
        self._keymap, self._keymap_src = load_keymap(self._env)
        spawn = self._spawn or (
            lambda argv: subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1))
        try:
            self._proc = spawn(XINPUT_ARGV)
        except OSError as e:
            self._proc = None
            return {"ok": False, "reason": f"spawn-failed: {e}"}
        self._t0 = time.monotonic()
        self._norm = []
        self._truncated = False
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self._watchdog = threading.Timer(self.max_ms / 1000.0, self._expire)
        self._watchdog.daemon = True
        self._watchdog.start()
        return {"ok": True, "name": self.name, "max_ms": self.max_ms,
                "context": self._context, "keymap": self._keymap_src}

    def _elapsed_ms(self):
        return int((time.monotonic() - self._t0) * 1000) if self._t0 else 0

    def _expire(self):
        """Duration cap: stop the stream, mark truncation."""
        with self._lock:
            self._truncated = True
        self._kill()

    def _kill(self):
        p = self._proc
        if p is None:
            return
        try:
            p.terminate()
        except Exception:
            try:
                os.kill(p.pid, signal.SIGTERM)
            except Exception:
                pass

    def _read(self):
        """stdout lines -> parsed events -> normalized stream."""
        proc = self._proc
        try:
            for line in proc.stdout:
                ev = self._parser.feed(line)
                if ev is None or ev.get("evtype") not in EV:
                    continue
                ev["t_ms"] = self._elapsed_ms()
                with self._lock:
                    self._norm.append(_norm_event(ev))
        except Exception:
            pass

    def status(self):
        with self._lock:
            return {"recording": self._proc is not None,
                    "elapsed_ms": self._elapsed_ms(),
                    "n_events": len(self._norm),
                    "truncated": self._truncated}

    def stop(self, save=True):
        """End capture, normalize, persist. Always returns a summary —
        an empty recording is {"ok":True,"n_steps":0}, not an error."""
        if self._watchdog:
            self._watchdog.cancel()
        self._kill()
        if self._reader:
            self._reader.join(timeout=5)
        tail = self._parser.flush()
        if tail is not None and tail.get("evtype") in EV:
            tail["t_ms"] = self._elapsed_ms()
            with self._lock:
                self._norm.append(_norm_event(tail))
        self._proc = None
        with self._lock:
            norm = list(self._norm)
            truncated = self._truncated
        macro = build_macro(self.name, norm, self._keymap,
                            context=self._context,
                            display=self._probe["display"],
                            keymap_src=self._keymap_src,
                            wait_min_ms=self.wait_min_ms,
                            max_ms=self.max_ms, max_steps=self.max_steps)
        macro["truncated"] = macro["truncated"] or truncated
        out = {"ok": True, "name": self.name,
               "n_steps": len(macro["steps"]),
               "duration_ms": macro["duration_ms"],
               "truncated": macro["truncated"],
               "context": self._context, "keymap": self._keymap_src,
               "steps": macro["steps"]}
        if save:
            out["path"] = save_macro(macro, self.dir)
        return out


# ---------------------------------------------------------------------
# module-level convenience (one recorder at a time, like the daemon)
# ---------------------------------------------------------------------

_CURRENT = None
_CURRENT_LOCK = threading.Lock()


def start(name="recording", dir=None, **kw):
    """Begin a capture. Returns {"ok":True,...} or {"ok":False,"reason"}.
    Only one recording at a time — a second start refuses."""
    global _CURRENT
    with _CURRENT_LOCK:
        if _CURRENT is not None and _CURRENT.status()["recording"]:
            return {"ok": False, "reason": "already-recording"}
        _CURRENT = Recorder(name, dir, **kw)
        return _CURRENT.start()


def stop(save=True):
    global _CURRENT
    with _CURRENT_LOCK:
        r = _CURRENT
        _CURRENT = None
    if r is None:
        return {"ok": False, "reason": "not-recording"}
    return r.stop(save=save)


def status():
    with _CURRENT_LOCK:
        return (_CURRENT.status() if _CURRENT is not None
                else {"recording": False, "elapsed_ms": 0,
                      "n_events": 0, "truncated": False})
