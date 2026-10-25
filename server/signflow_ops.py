#!/usr/bin/env python3
"""signflow_ops — the session layer that makes a small taught vocabulary
powerful instead of a macro toy (founder addendum 17:06).

Sits BETWEEN perception (endpointer -> signbank.match) and the act
layer (act_iface / wfd routing).  signbank answers "which sign was
that, if any?"; this module answers "what should HAPPEN, given the
signs around it and the context we're in?".

Four pieces, all session state, stdlib only, thread-safe:

1. COMPOSITIONAL SEQUENCES.  Accepted matches arrive as
   (gloss, dist, t_ms) via on_match()/feed().  Consecutive accepted
   signs whose inter-sign gap is <= window_ms form a sequence resolved
   against a composition table — "OPEN BROWSER" inside ~1.2 s fires one
   bound action, so 10 taught signs compose into dozens of commands.
   Resolution is longest-match with early commit:

     * a sign that is a proper PREFIX of a composition ("OPEN" of
       "OPEN BROWSER") is HELD up to window_ms; a follow-up inside the
       window extends the sequence; if nothing follows it resolves to
       what it is (its own single-gloss composition if it has one, else
       its bank-bound action).
     * a sequence that is an EXACT entry and not a prefix of anything
       longer COMMITS IMMEDIATELY — no added latency.
     * a sign in no composition at all fires its bound action at once —
       only signs that can compose pay the window.
     * a dead end ("OPEN FOO" nowhere) re-seeds: held signs resolve
       left-to-right greedy (longest exact prefix, else single) and the
       newcomer starts fresh.

   Every hold emits a "partial" event ({"seq", "next", "due_ms"}) so
   the phone can show "OPEN + …" live while the user is still signing —
   composition-in-progress is part of the /live/state surface.

   Composition tables are PER-PROFILE namespaces: a sequence defined
   under "default" does not hold (or fire) signs under "cooking".
   Programmatic set_composition() writes the active profile's table;
   a flat file "sequences" table belongs to the profile the file
   declares ("profile" key, absent -> "default").  save() flattens the
   union of all tables under the current profile so a saved session
   keeps composing where it was saved (namespaces merge — documented).

   The table is ~/.config/wf/compositions.json, hot-reloaded by
   (mtime_ns, size):

       {"window_ms": 1200,
        "profile": "default",
        "profile_signs": {"COOKING": "cooking"},
        "sequences": {
            "OPEN BROWSER": {"kind": "app", "tool": "app.open", ...},
            "NEXT SLIDE":   {"kind": "builtin", "tool": "key.press", ...}}}

   Keys are whitespace-separated glosses, matched case-insensitively.
   A bad JSON edit emits a {"type":"compositions","ok":false} event and
   keeps the last good table — it never wipes the session.

2. PROFILES.  Named contexts ("cooking", "presenting"): the SAME sign
   resolves to a different action per profile.  Per-profile bindings
   live INSIDE the bound action object —

       {"kind":"builtin","tool":"key.press","args":{"keys":"Right"},
        "profiles":{"cooking":{"kind":"builtin","tool":"key.press",
                               "args":{"keys":"Down"}}}}

   — because the bank file format (signbank.py, landed in another lane)
   has no profile field but persists "action" opaquely.  So profiles DO
   persist in the bank file, inside each row's action JSON; no format
   change needed.  An action that is ONLY {"profiles": {...}} exists
   only in the listed profiles — unbound elsewhere (reported, never
   fired).  Switch programmatically via set_profile() or by teaching a
   reserved profile-sign (profile_signs maps gloss -> profile; when it
   matches, any held sequence resolves under the OLD profile first,
   then the switch happens — the sign itself never fires an action).

3. OBSERVE / REHEARSAL MODE.  set_observe(True): matching, composition
   and profile logic all run and identical events are emitted, but
   action_router is NEVER invoked — "would-fire" events carry the exact
   resolved action so a user can practice with zero consequences.
   Passing expected=<gloss|None> with each match turns it into an eval
   mode: eval_correct / wrong_accept / wrong_reject / correct_reject
   counters for the feedback strip.

4. SESSION STATS.  Counters for the feedback strip: accepted, rejected
   (+reject_reasons), fires split single/sequence, would_fire, unbound,
   profile_switches, fire_errors, eval counters; plus a bounded
   "recent" ring (64 events) — the why-did-that-happen feed.  Rejected
   matches are reported (with a "near" flag when the miss was close)
   but never enter sequences and never disturb the window.

THREADING / SIDE EFFECTS.  One RLock guards all state.  Side effects
never run inside the lock: locked work produces markers — ("event", ev)
to emit, ("fire", ev, action, ctx) to route-then-emit — and _dispatch
invokes the injected callables afterwards, so callbacks may re-enter
ops freely.  action_router(action_dict, ctx_dict) and event_sink(dict)
are the only effect channels; this module never spawns processes and
has no process-spawning imports at all.  It is the pure-logic layer
wfd wires.

HONEST LIMITS:
  * window_ms is a constant (~1.2 s default, file-overridable): a sign
    that is a composition PREFIX always waits the full window when the
    user meant it alone.  Adaptive windows are unmeasured — tune on
    real captures, not intuition.
  * Greedy left-to-right resolution commits the first unambiguous
    sequence.  Overlapping keys ("A B" and "B C") resolve by arrival
    order — design tables without overlaps.
  * Profile resolution is one level deep (a variant's own "profiles"
    key is ignored) and happens at FIRE time, not sign time.
  * Expiry is driven by tick(now_ms): the daemon must call it (per
    /live/state poll or a timer) or held prefixes never resolve.
"""

import json
import math
import os
import tempfile
import threading
import time
from collections import deque

DEFAULT_COMP_PATH = os.path.expanduser("~/.config/wf/compositions.json")
DEFAULT_WINDOW_MS = 1200     # inter-sign gap for composing; file-overridable
RECENT_N = 64                # feedback-strip ring depth
_MISSING = object()          # "argument not supplied" sentinel


def _now_ms():
    return int(time.time() * 1000)


def norm_gloss(g):
    """Canonical gloss for comparisons: collapse whitespace, upper-case."""
    return " ".join(str(g).split()).upper()


def norm_seq(seq):
    """"OPEN  browser" | ["open","BROWSER"] -> ("OPEN","BROWSER")."""
    parts = seq.split() if isinstance(seq, str) else [str(p) for p in seq]
    return tuple(p.upper() for p in parts if p.strip())


def resolve_action(action, profile):
    """Profile-aware action resolution — one level, at fire time.

    Any action object may carry "profiles": {name: variant}.  In that
    profile the variant replaces the whole action; otherwise the base
    action (minus the profiles key) fires.  An action that is ONLY
    {"profiles": ...} resolves to None outside those profiles —
    unbound, never a guess.
    """
    if not isinstance(action, dict):
        return action
    profs = action.get("profiles")
    if isinstance(profs, dict) and profile and profile in profs:
        v = profs[profile]
        return dict(v) if isinstance(v, dict) else v
    base = {k: v for k, v in action.items() if k != "profiles"}
    return base or None


class SignFlowOps:
    """Session composer + context layer over SignBank.

        ops = SignFlowOps(bank, action_router=router, event_sink=sink)
        ops.on_match(bank.match(frames), t_ms=seg_end_ms)
        # ... or feed("OPEN", 0.42, t_ms)
        ops.tick(now_ms)              # expire held prefixes (per poll)
        ops.state()                   # snapshot for GET /live/state
    """

    def __init__(self, bank=None, action_router=None, event_sink=None,
                 window_ms=DEFAULT_WINDOW_MS,
                 compositions_path=DEFAULT_COMP_PATH, autoload=True,
                 clock=None):
        self.bank = bank
        self.compositions_path = compositions_path
        self.window_ms = max(0, int(window_ms))
        self._router = action_router
        self._sink = event_sink
        self._clock = clock or _now_ms
        self._lock = threading.RLock()
        self.profile = "default"
        self.observe = False
        self._pending = []                    # held prefix items
        # composition tables are PER-PROFILE namespaces: a sequence
        # defined under "default" does not hold signs under "cooking".
        # save() flattens every table into the file's declared profile,
        # so a round-trip keeps composing where it was saved.
        self._comp_by_profile = {"default": {}}
        self._profile_signs = {}              # programmatic gloss -> profile
        self._file_profile_signs = {}         # file-loaded gloss -> profile
        self._comp_mtime = _MISSING           # forces first stat to "load"
        self._recent = deque(maxlen=RECENT_N)
        self._stats = {
            "accepted": 0, "rejected": 0, "reject_reasons": {},
            "fires": 0, "single_fires": 0, "sequence_fires": 0,
            "fire_errors": 0, "would_fire": 0, "unbound": 0,
            "profile_switches": 0, "events": 0,
            "eval_n": 0, "eval_correct": 0, "wrong_accept": 0,
            "wrong_reject": 0, "correct_reject": 0,
        }
        if autoload:
            self.tick()          # first load of compositions file

    # ------------------------------------------------------------------
    # intake
    # ------------------------------------------------------------------

    def on_match(self, match, dist=None, t_ms=None, *, accepted=None,
                 action=None, label=None, reason=None, expected=_MISSING,
                 score=None, second_gloss=None, margin=None):
        """Feed one scored segment.  `match` may be a signbank
        MatchResult (fields auto-read; kwargs override) or a gloss
        string — feed("OPEN", 0.42, 15233).  Rejected matches are
        reported + counted but never enter a sequence.  Returns the
        list of events emitted (also sent to event_sink)."""
        if hasattr(match, "gloss") and hasattr(match, "dist"):
            r = match
            gloss = r.gloss
            if dist is None:
                dist = r.dist
            if accepted is None:
                accepted = bool(r.accepted)
            if action is None:
                action = getattr(r, "action", None)
            if label is None:
                label = getattr(r, "label", "") or ""
            if reason is None:
                reason = getattr(r, "reason", "") or ""
            if score is None:
                score = getattr(r, "score", None)
            if second_gloss is None:
                second_gloss = getattr(r, "second_gloss", None)
            if margin is None:
                margin = getattr(r, "margin", None)
        else:
            gloss = match
            if accepted is None:
                accepted = True
        t_ms = self._clock() if t_ms is None else int(t_ms)
        it = {"gloss": (str(gloss).strip() if gloss else None),
              "gkey": norm_gloss(gloss) if gloss else "",
              "dist": dist, "t": t_ms, "action": action,
              "label": label or "", "score": score,
              "second": second_gloss, "margin": margin,
              "reason": reason or "", "expected": expected}
        with self._lock:
            out = self._maybe_reload_locked()
            if it["gloss"] and accepted:
                out += self._accept_locked(it)
            else:
                out += self._reject_locked(it)
        return self._dispatch(out)

    # spec name: matches "feed a session composer"
    def feed(self, *args, **kw):
        return self.on_match(*args, **kw)

    def tick(self, now_ms=None):
        """Expire held composition prefixes by wall clock.  wfd calls
        this per /live/state poll (or a ~100 ms timer) — held signs can
        only resolve when the clock advances past their due time."""
        now = self._clock() if now_ms is None else int(now_ms)
        with self._lock:
            out = self._maybe_reload_locked()
            if self._pending and \
                    now - self._pending[-1]["t"] > self.window_ms:
                out += self._resolve_pending_locked()
        return self._dispatch(out)

    def flush(self):
        """Force-resolve any held sequence NOW (session end / listen
        toggle off).  Held prefixes become their best resolution."""
        with self._lock:
            out = self._resolve_pending_locked()
        return self._dispatch(out)

    # ------------------------------------------------------------------
    # accepted / rejected paths (locked; return marker lists)
    # ------------------------------------------------------------------

    def _seq(self):
        return tuple(i["gkey"] for i in self._pending)

    def _tbl(self):
        """The active profile's composition table (created on demand)."""
        return self._comp_by_profile.setdefault(self.profile, {})

    @property
    def _prefixes(self):
        """Proper prefixes of the active profile's compositions —
        computed live so programmatic + file edits take effect at once."""
        return {k[:i] for k in self._tbl() for i in range(1, len(k))}

    def _accept_locked(self, it):
        self._stats["accepted"] += 1
        ev = {"type": "match", "gloss": it["gloss"], "label": it["label"],
              "dist": it["dist"], "t_ms": it["t"], "profile": self.profile}
        for k_src, k_dst in (("score", "score"), ("second", "second_gloss"),
                             ("margin", "margin")):
            if it[k_src] is not None:
                ev[k_dst] = it[k_src]
        out = [("event", ev)]
        out += self._eval_locked(it, accepted=True)

        # reserved profile-sign: never composes, never fires an action;
        # held signs resolve under the OLD profile before the switch.
        target = self._psign(it["gkey"])
        if target is not None:
            out += self._resolve_pending_locked()
            if target != self.profile:
                self._stats["profile_switches"] += 1
                self.profile = target
            out.append(("event", {"type": "profile",
                                  "profile": self.profile, "via": "sign",
                                  "gloss": it["gloss"], "t_ms": it["t"]}))
            return out

        # gap-based rolling window: only consecutive accepted signs join
        if self._pending and \
                it["t"] - self._pending[-1]["t"] > self.window_ms:
            out += self._resolve_pending_locked()

        if self.window_ms <= 0:
            return out + self._seed_locked(it)   # composition disabled

        self._pending.append(it)
        seq = self._seq()
        if seq in self._prefixes:
            out.append(("event", self._partial_ev()))
            return out
        if seq in self._tbl():
            # exact entry, not a prefix of anything longer: commit NOW
            return out + self._resolve_pending_locked()
        # dead end: the held prefix can't extend into any composition —
        # resolve it, then the newcomer seeds its own sequence.
        it2 = self._pending.pop()
        out += self._resolve_pending_locked()
        return out + self._seed_locked(it2)

    def _reject_locked(self, it):
        self._stats["rejected"] += 1
        rsn = it["reason"] or "rejected"
        rr = self._stats["reject_reasons"]
        rr[rsn] = rr.get(rsn, 0) + 1
        near = False
        d = it["dist"]
        if rsn == "threshold" and self.bank is not None and \
                isinstance(d, (int, float)) and math.isfinite(d):
            # "the miss was close": within 2x of the accept bar — far
            # enough to be an almost-sign, not random noise.  (0.99 on a
            # 0.6 bar is a near miss the UI should surface honestly.)
            near = d <= self.bank.reject_threshold * 2
        ev = {"type": "reject", "gloss": it["gloss"], "dist": d,
              "reason": rsn, "near": near, "t_ms": it["t"],
              "profile": self.profile}
        if it["expected"] is not _MISSING:
            ev["expected"] = it["expected"]
        out = [("event", ev)]
        out += self._eval_locked(it, accepted=False)
        return out

    def _eval_locked(self, it, accepted):
        """Observe-mode eval accounting — expected=<gloss> labels a
        positive sample, expected=None labels a deliberate negative."""
        exp = it["expected"]
        if exp is _MISSING:
            return []
        self._stats["eval_n"] += 1
        if accepted:
            ok = exp is not None and norm_gloss(exp) == it["gkey"]
            self._stats["eval_correct" if ok else "wrong_accept"] += 1
        else:
            ok = exp is None
            self._stats["eval_correct"] += 1 if ok else 0
            self._stats["correct_reject" if ok else "wrong_reject"] += 1
        if ok:
            return []
        return [("event", {"type": "eval", "ok": False, "expected": exp,
                           "got": it["gkey"] if accepted else None,
                           "t_ms": it["t"]})]

    # ------------------------------------------------------------------
    # composition resolution (locked)
    # ------------------------------------------------------------------

    def _seed_locked(self, it):
        """A lone accepted sign: hold if it opens a composition, else
        fire (composition entry first, bank-bound action as fallback)."""
        key = (it["gkey"],)
        if self.window_ms > 0 and key in self._prefixes:
            self._pending.append(it)
            return [("event", self._partial_ev())]
        if key in self._tbl():
            return self._fire_locked("sequence", [it], self._tbl()[key])
        act = self._bound(it["gkey"])
        if act is None:
            act = it["action"]
        return self._fire_locked("single", [it], act)

    def _resolve_pending_locked(self):
        """Greedy left-to-right: longest exact composition prefix wins,
        else the leftmost sign fires its bound action alone."""
        out = []
        while self._pending:
            seq = self._seq()
            k = 0
            for n in range(len(seq), 0, -1):
                if seq[:n] in self._tbl():
                    k = n
                    break
            if k:
                items, self._pending = self._pending[:k], self._pending[k:]
                out += self._fire_locked("sequence", items,
                                         self._tbl()[seq[:k]])
            else:
                it = self._pending.pop(0)
                act = self._bound(it["gkey"])
                if act is None:
                    act = it["action"]
                out += self._fire_locked("single", [it], act)
        return out

    def _partial_ev(self):
        seq = self._seq()
        nxt = sorted({k[len(seq)] for k in self._tbl()
                      if len(k) > len(seq) and k[:len(seq)] == seq})
        return {"type": "partial",
                "seq": [i["gloss"] for i in self._pending],
                "next": nxt, "exact": seq in self._tbl(),
                "due_ms": self._pending[-1]["t"] + self.window_ms,
                "t_ms": self._pending[-1]["t"], "profile": self.profile}

    def _fire_locked(self, kind, items, action):
        glosses = [i["gloss"] for i in items]
        resolved = resolve_action(action, self.profile)
        t = items[-1]["t"]
        if resolved is None:
            self._stats["unbound"] += 1
            return [("event", {"type": "unbound", "glosses": glosses,
                               "profile": self.profile, "t_ms": t})]
        label = items[-1]["label"] if kind == "single" else \
            (resolved.get("label") if isinstance(resolved, dict) else None)
        ev = {"type": "would-fire" if self.observe else "fire",
              "kind": kind, "glosses": glosses, "action": resolved,
              "profile": self.profile, "t_ms": t}
        if label:
            ev["label"] = label
        if self.observe:
            self._stats["would_fire"] += 1
            return [("event", ev)]
        self._stats["fires"] += 1
        self._stats["single_fires" if kind == "single"
                     else "sequence_fires"] += 1
        ctx = {"kind": kind, "glosses": list(glosses),
               "profile": self.profile, "t_ms": t,
               "source": "signflow_ops"}
        return [("fire", ev, resolved, ctx)]

    def _bound(self, gkey):
        """A gloss's bound action straight from the bank (looked up at
        fire time — rebinds between sign and fire take effect)."""
        if self.bank is None:
            return None
        try:
            for row in self.bank.list():
                if norm_gloss(row.get("gloss")) == gkey:
                    return row.get("action")
        except Exception:
            pass
        return None

    def _psign(self, gkey):
        if gkey in self._profile_signs:
            return self._profile_signs[gkey]
        return self._file_profile_signs.get(gkey)

    # ------------------------------------------------------------------
    # dispatch — outside the lock; the only place effects happen
    # ------------------------------------------------------------------

    def _dispatch(self, out):
        emitted = []
        for item in out:
            if item[0] == "fire":
                _, ev, action, ctx = item
                if self._router is None:
                    ev["ok"] = None
                    ev["routed"] = False
                else:
                    try:
                        res = self._router(action, ctx)
                        ev["ok"] = (bool(res.get("ok", True))
                                    if isinstance(res, dict) else True)
                        if isinstance(res, dict) and "error" in res:
                            ev["error"] = res["error"]
                    except Exception as e:
                        ev["ok"] = False
                        ev["error"] = repr(e)
                        with self._lock:
                            self._stats["fire_errors"] += 1
                emitted.append(ev)
                self._note(ev)
            else:
                emitted.append(item[1])
                self._note(item[1])
        return emitted

    def _note(self, ev):
        with self._lock:
            self._stats["events"] += 1
            self._recent.append(ev)
        if self._sink is not None:
            try:
                self._sink(ev)
            except Exception:
                pass          # a UI sink must never take down the pipeline

    # ------------------------------------------------------------------
    # compositions file: load / hot-reload / save
    # ------------------------------------------------------------------

    def _maybe_reload_locked(self):
        if not self.compositions_path:
            return []
        # (mtime_ns, size) — a bare mtime check misses edits made within
        # one filesystem clock tick (os.utime in tests, fast rewrites in
        # the UI).  Same-size same-tick rewrites remain a documented gap
        # — reload_compositions() force-reloads regardless.
        try:
            st = os.stat(self.compositions_path)
            mt = (st.st_mtime_ns, st.st_size)
        except OSError:
            mt = None
        if mt == self._comp_mtime:
            return []
        self._comp_mtime = mt
        if mt is None:
            self._comp_by_profile = {}
            self._file_profile_signs = {}
            return []
        try:
            with open(self.compositions_path) as f:
                data = json.load(f)
            comp = {}
            for k, v in (data.get("sequences") or {}).items():
                key = norm_seq(k)
                if key and isinstance(v, dict):
                    comp[key] = v.get("action", v)
            if data.get("window_ms") is not None:
                self.window_ms = max(0, int(data["window_ms"]))
            self._file_profile_signs = {
                norm_gloss(k): str(v)
                for k, v in (data.get("profile_signs") or {}).items()}
            # a flat "sequences" table belongs to the profile the file
            # declares (absent -> "default"); tables set programmatically
            # under other profiles are untouched.
            fprof = data.get("profile")
            if not isinstance(fprof, str) or not fprof.strip():
                fprof = "default"
            self._comp_by_profile[fprof.strip()] = comp
            out = [("event", {"type": "compositions", "ok": True,
                              "n": len(comp), "t_ms": self._clock()})]
            prof = data.get("profile")
            if isinstance(prof, str) and prof and prof != self.profile:
                out += self._resolve_pending_locked()
                self._stats["profile_switches"] += 1
                self.profile = prof
                out.append(("event", {"type": "profile", "profile": prof,
                                      "via": "file",
                                      "t_ms": self._clock()}))
            return out
        except Exception as e:
            # honest failure: keep the last good table, say why
            return [("event", {"type": "compositions", "ok": False,
                               "error": repr(e), "t_ms": self._clock()})]

    def reload_compositions(self):
        """Force a reload (mtime check normally gates it)."""
        with self._lock:
            self._comp_mtime = _MISSING
            out = self._maybe_reload_locked()
        return self._dispatch(out)

    def save_compositions(self, path=None):
        """Persist the live table + profile state, atomic, mode 600
        (same dir holds the pairing key — treat config as sensitive)."""
        path = path or self.compositions_path or DEFAULT_COMP_PATH
        signs = dict(self._file_profile_signs)
        signs.update(self._profile_signs)
        # flat file can name one profile only — the union of every
        # table is written under the CURRENT profile so a saved session
        # keeps composing after reload (namespaces merge on save).
        merged = {}
        for t in self._comp_by_profile.values():
            merged.update(t)
        data = {"version": 1, "window_ms": self.window_ms,
                "profile": self.profile, "profile_signs": signs,
                "sequences": {" ".join(k): v for k, v in merged.items()}}
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d or ".", prefix=".comps.",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=1)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
            os.chmod(path, 0o600)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        with self._lock:
            st = os.stat(path)
            self._comp_mtime = (st.st_mtime_ns, st.st_size)
        return path

    def set_composition(self, seq, action):
        """Bind a sequence in the ACTIVE profile's table
        (action=None removes).  The same glosses may compose
        differently per profile — contexts are real namespaces."""
        key = norm_seq(seq)
        if not key:
            return False
        with self._lock:
            tbl = self._tbl()
            if action is None:
                tbl.pop(key, None)
            else:
                tbl[key] = action
            ev = {"type": "compositions", "ok": True, "via": "api",
                  "n": len(tbl), "profile": self.profile,
                  "t_ms": self._clock()}
        self._dispatch([("event", ev)])
        return True

    def remove_composition(self, seq):
        return self.set_composition(seq, None)

    def compositions(self):
        """The ACTIVE profile's composition table, display-ready."""
        with self._lock:
            return {" ".join(k): v for k, v in self._tbl().items()}

    # ------------------------------------------------------------------
    # profiles
    # ------------------------------------------------------------------

    def set_profile(self, name, via="api"):
        """Switch the active context.  Held signs resolve under the OLD
        profile first — a context switch is a clean sequence boundary."""
        name = str(name).strip() or "default"
        with self._lock:
            out = self._resolve_pending_locked()
            if name != self.profile:
                self._stats["profile_switches"] += 1
                self.profile = name
            out.append(("event", {"type": "profile", "profile": name,
                                  "via": via, "t_ms": self._clock()}))
        return self._dispatch(out)

    def set_profile_sign(self, gloss, profile):
        """Reserve `gloss` as the switch-sign for `profile`
        (profile=None unreserves).  The sign is taught in the bank like
        any other; matching it switches context instead of firing."""
        g = norm_gloss(gloss)
        if not g:
            return False
        with self._lock:
            if profile is None:
                self._profile_signs.pop(g, None)
            else:
                self._profile_signs[g] = str(profile)
        return True

    def profile_signs(self):
        with self._lock:
            out = dict(self._file_profile_signs)
            out.update(self._profile_signs)
            return out

    def profiles(self):
        """Known profile names: sign-mapped + embedded in bound actions
        + embedded in composition actions."""
        s = {"default"}
        with self._lock:
            s |= set(self._profile_signs.values())
            s |= set(self._file_profile_signs.values())
            s |= set(self._comp_by_profile.keys())
            for t in self._comp_by_profile.values():
                for v in t.values():
                    if isinstance(v, dict) and \
                            isinstance(v.get("profiles"), dict):
                        s |= set(v["profiles"].keys())
        if self.bank is not None:
            try:
                for r in self.bank.list():
                    a = r.get("action")
                    if isinstance(a, dict) and \
                            isinstance(a.get("profiles"), dict):
                        s |= set(a["profiles"].keys())
            except Exception:
                pass
        return sorted(s)

    def bind(self, gloss, action=None, profile=None, label=None):
        """Profile-aware rebind written INTO the bank row — bank.save()
        persists it inside the row's action JSON (the format's opaque
        field; no signbank change needed).

        profile=None/"default": replace the base action, keep variants.
        profile="cooking": set that variant only; action=None drops it.
        """
        if self.bank is None:
            return False
        gkey = norm_gloss(gloss)
        row = None
        for r in self.bank.list():
            if norm_gloss(r.get("gloss")) == gkey:
                row = r
                break
        if row is None:
            return False
        cur = row.get("action")
        cur = dict(cur) if isinstance(cur, dict) else {}
        if not profile or profile == "default":
            new = dict(action) if isinstance(action, dict) else {}
            if cur.get("profiles"):
                new["profiles"] = cur["profiles"]
        else:
            new = {k: v for k, v in cur.items() if k != "profiles"}
            profs = dict(cur.get("profiles") or {})
            if action is None:
                profs.pop(profile, None)
            else:
                profs[profile] = action
            if profs:
                new["profiles"] = profs
        return self.bank.set_action(gloss, action=new, label=label)

    # ------------------------------------------------------------------
    # observe / stats / state
    # ------------------------------------------------------------------

    def set_observe(self, on):
        """Rehearsal mode: report everything, fire nothing."""
        with self._lock:
            self.observe = bool(on)
            ev = {"type": "observe", "on": self.observe,
                  "t_ms": self._clock()}
        self._dispatch([("event", ev)])
        return self.observe

    def stats(self):
        with self._lock:
            s = dict(self._stats)
            s["reject_reasons"] = dict(self._stats["reject_reasons"])
            return s

    def state(self, now_ms=None):
        """Snapshot for GET /live/state — pending composition included
        so the phone can render "OPEN + …" and the feedback strip."""
        now = self._clock() if now_ms is None else int(now_ms)
        with self._lock:
            due = None
            if self._pending:
                due = self._pending[-1]["t"] + self.window_ms - now
            return {"ok": True, "profile": self.profile,
                    "observe": self.observe, "window_ms": self.window_ms,
                    "pending": [i["gloss"] for i in self._pending],
                    "pending_due_ms": due,
                    "n_compositions": len(self._tbl()),
                    "comp_profiles": sorted(self._comp_by_profile),
                    "profile_signs": self.profile_signs(),
                    "stats": self.stats(),
                    "recent": list(self._recent)}

    def reset_session(self):
        """New session: clear pending + stats + feed.  Keeps profile,
        compositions and bindings (they're configuration, not state)."""
        with self._lock:
            self._pending = []
            self._recent.clear()
            for k in self._stats:
                self._stats[k] = {} if k == "reject_reasons" else 0
            ev = {"type": "session", "reset": True, "t_ms": self._clock()}
        self._dispatch([("event", ev)])
