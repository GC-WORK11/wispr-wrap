#!/usr/bin/env python3
"""macro — chained action steps for the merged product (contract v2,
ops/APP_API_CONTRACT.md: kind:"macro" bound actions on bank rows).

The @ picker (Devin PRO's lane) stores a step list on a bank entry; at fire
time the daemon calls run_macro(steps, provider). Steps execute SERIALLY
through the ActionProvider contract — every spawn is argv-only inside the
provider layer; macro.py itself never spawns.

Step shapes accepted (normalize_step handles both):

    {"step":"wait_ms","ms":400}            or shorthand {"wait_ms":400}
    {"step":"open_app","exec":"firefox"}   or {"open_app":"firefox"}
    {"step":"type","text":"hello"}         or {"type":"hello"}
    {"step":"keys","keys":"ctrl+Tab"}      or {"keys":"ctrl+Tab"}
    {"step":"argv","argv":["notify-send","hi"]}   or {"argv":[...]}  (tier 2)
    {"step":"context_dispatch","default":{...},"by_wm_class":{...}}
    {"tool":"<any catalog id>","args":{...}}      — generic passthrough:
        every /act catalog tool (sys.mute, media.next, app.open,
        win.tile-left, ...) is a valid macro step.

Extra step fields:
    "optional": true   — a failed step is recorded but does NOT stop the
                         chain, and does NOT fail the macro result.
    "delay_ms": N      — extra wait AFTER this step (contract example:
                         {"tool":"app.open",...,"delay_ms":800} gives the
                         app time to open before the next step fires).
    "label": str       — carried into the step result for the UI.

Semantics (honest, documented — the picker UI must show these):
  * Serial: one step at a time, in order. No parallelism.
  * Stop-on-fail: first failed non-optional step aborts the macro;
    remaining steps are reported "skipped".
  * Total timeout: deadline covers the WHOLE chain including wait/delay
    sleeps; on expiry remaining steps are "skipped" with error "timeout".
  * Focus guard: "type" and "keys" steps call provider.verify_focus()
    BEFORE dispatching (demo review: refuse rather than mistype
    into the wrong window). A provider with no focus guard => step fails
    "focus-unverified" — we never type blind.
  * Results: per-step {i, step, label, ok, detail, ms} list — JSON-able,
    sized for /live/state's last_act or a picker-run log.
"""
import time

MAX_STEPS = 64
MAX_WAIT_MS = 60_000          # a single wait can't exceed a minute
DEFAULT_TIMEOUT_S = 30.0

_STEP_KEYS = ("wait_ms", "open_app", "type", "keys", "argv",
              "context_dispatch")

# step kind -> (tool, tier) for the action we hand the provider
_TYPE_ACTION = {"kind": "builtin", "tool": "type.text", "tier": 1}
_KEYS_ACTION = {"kind": "builtin", "tool": "key.press", "tier": 1}
_ARGV_ACTION = {"kind": "command", "tool": "command", "tier": 2}
_APP_ACTION = {"kind": "app", "tool": "app.open", "tier": 1}
_CTX_ACTION = {"kind": "context", "tier": 1}


# ------------------------------------------------------------------
# validation / normalization
# ------------------------------------------------------------------

def _is_str_list(v):
    return isinstance(v, list) and v and all(isinstance(x, str) and x
                                             for x in v)


def normalize_step(raw):
    """One raw step dict -> canonical {"step": kind, ...} or raises
    ValueError with a human reason (validate_steps collects these)."""
    if not isinstance(raw, dict):
        raise ValueError("step must be an object, got %s" % type(raw).__name__)

    if "step" in raw or "wait_ms" in raw:
        kind = raw.get("step", "wait_ms")
    elif "tool" in raw:
        kind = "tool"
    else:
        found = [k for k in _STEP_KEYS if k in raw]
        if len(found) == 1:
            kind = found[0]
        elif not found:
            raise ValueError("no step type: need one of %s or 'tool'"
                             % ",".join(_STEP_KEYS))
        else:
            raise ValueError("ambiguous step: multiple step keys %s" % found)

    st = {"step": kind}
    for opt in ("optional", "delay_ms", "label", "args", "kind", "tier"):
        if opt in raw:
            st[opt] = raw[opt]

    if kind == "wait_ms":
        ms = raw.get("ms", raw.get("wait_ms"))
        if not isinstance(ms, (int, float)) or ms < 0:
            raise ValueError("wait_ms: ms must be a non-negative number")
        if ms > MAX_WAIT_MS:
            raise ValueError("wait_ms: %d exceeds %d cap" % (ms, MAX_WAIT_MS))
        st["ms"] = int(ms)
        return st

    if kind == "type":
        text = raw.get("text", raw.get("type"))
        if not isinstance(text, str):
            raise ValueError("type: text must be a string")
        st["text"] = text
        return st

    if kind == "keys":
        keys = raw.get("keys")
        if not isinstance(keys, str) or not keys.strip():
            raise ValueError("keys: must be a non-empty key spec like 'ctrl+Tab'")
        st["keys"] = keys.strip()
        return st

    if kind == "argv":
        argv = raw.get("argv")
        if not _is_str_list(argv):
            raise ValueError("argv: must be a non-empty list of strings "
                             "(argv-only spawn — no shell)")
        st["argv"] = argv
        return st

    if kind == "open_app":
        v = raw.get("open_app")
        args = raw.get("args") or {}
        execv = raw.get("exec") or args.get("exec") or \
            (v.get("exec") if isinstance(v, dict) else v)
        app = raw.get("app") or args.get("app") or \
            (v.get("app") if isinstance(v, dict) else None)
        if not isinstance(execv or "", str) or not execv:
            raise ValueError("open_app: need 'exec' (argv0 or .desktop name)")
        st["exec"] = execv
        if isinstance(app, str) and app:
            st["app"] = app
        return st

    if kind == "context_dispatch":
        v = raw.get("context_dispatch") or {}
        if not isinstance(v, dict):
            raise ValueError("context_dispatch: must be an object")
        default = raw.get("default", v.get("default"))
        by = raw.get("by_wm_class", v.get("by_wm_class"))
        if not isinstance(default, dict):
            raise ValueError("context_dispatch: need a 'default' action object")
        if by is not None and not isinstance(by, dict):
            raise ValueError("context_dispatch: 'by_wm_class' must be an object")
        st["default"] = default
        st["by_wm_class"] = by or {}
        return st

    if kind == "tool":
        tool = raw.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError("tool: must be a non-empty catalog id")
        if not isinstance(st.get("args", {}), dict):
            raise ValueError("tool step 'args' must be an object")
        st["tool"] = tool.strip()
        return st

    raise ValueError("unknown step type: %r" % kind)


def validate_steps(steps):
    """-> (normalized_steps, errors). errors empty means runnable."""
    if not isinstance(steps, list):
        return [], ["macro steps must be a JSON list"]
    if not steps:
        return [], ["macro needs at least one step"]
    if len(steps) > MAX_STEPS:
        return [], ["macro has %d steps, cap is %d" % (len(steps), MAX_STEPS)]
    norm, errs = [], []
    for i, raw in enumerate(steps):
        try:
            norm.append(normalize_step(raw))
        except ValueError as e:
            errs.append("step %d: %s" % (i, e))
    return norm, errs


# ------------------------------------------------------------------
# step -> action mapping (through the provider, never direct spawn)
# ------------------------------------------------------------------

def _step_action(st):
    kind = st["step"]
    if kind == "type":
        a = dict(_TYPE_ACTION)
        a["args"] = {"text": st["text"]}
        a["label"] = st.get("label", "type %d chars" % len(st["text"]))
    elif kind == "keys":
        a = dict(_KEYS_ACTION)
        a["args"] = {"keys": st["keys"]}
        a["label"] = st.get("label", "keys %s" % st["keys"])
    elif kind == "argv":
        a = dict(_ARGV_ACTION)
        a["argv"] = st["argv"]
        a["label"] = st.get("label", " ".join(st["argv"]))
    elif kind == "open_app":
        a = dict(_APP_ACTION)
        a["args"] = {"exec": st["exec"]}
        if st.get("app"):
            a["args"]["app"] = st["app"]
        a["label"] = st.get("label", "open %s" % st["exec"])
    elif kind == "context_dispatch":
        a = dict(_CTX_ACTION)
        a["default"] = st["default"]
        a["by_wm_class"] = st["by_wm_class"]
        a["label"] = st.get("label", "context dispatch")
    elif kind == "tool":
        a = {"tool": st["tool"], "args": st.get("args") or {}}
        if st.get("kind"):
            a["kind"] = st["kind"]
        if st.get("tier") is not None:
            a["tier"] = st["tier"]
        a["label"] = st.get("label", st["tool"])
    else:
        a = None
    return a


def _verify_focus(provider, ctx):
    """Call the provider's focus guard. Returns (ok, note)."""
    vf = getattr(provider, "verify_focus", None)
    if vf is None:
        return False, "focus-unverified: provider has no verify_focus guard"
    try:
        res = vf(ctx) if ctx is not None else vf()
    except TypeError:
        res = vf()
    except Exception as e:
        return False, "focus-unverified: verify_focus raised %s" % e
    if isinstance(res, dict):
        return bool(res.get("ok")), res.get("note", "") or res.get("detail", "")
    return bool(res), ""


def _res_ok(res):
    if isinstance(res, dict):
        return bool(res.get("ok"))
    return bool(getattr(res, "ok", res if isinstance(res, bool) else False))


def _res_detail(res):
    if isinstance(res, dict):
        return str(res.get("detail", ""))[:400]
    return str(getattr(res, "detail", ""))[:400]


# ------------------------------------------------------------------
# the engine
# ------------------------------------------------------------------

def run_macro(steps, provider, ctx=None, timeout_s=DEFAULT_TIMEOUT_S,
              _sleep=time.sleep, _now=time.monotonic):
    """Run a normalized-or-raw step list serially through `provider`.

    Returns {"ok", "ran", "steps":[per-step results], "elapsed_ms",
             "error"}. Never raises on bad input — validation errors come
    back as {"ok":False,"error":...} so a hostile/malformed bank row can't
    crash the daemon."""
    norm, errs = validate_steps(steps)
    if errs:
        return {"ok": False, "ran": 0, "steps": [], "elapsed_ms": 0,
                "error": "; ".join(errs)}
    timeout_s = float(timeout_s if timeout_s and timeout_s > 0
                      else DEFAULT_TIMEOUT_S)
    deadline = _now() + timeout_s
    results, ran, failed = [], 0, False
    t0 = _now()

    def remaining_ms():
        return int((deadline - _now()) * 1000)

    def timed_out():
        return _now() >= deadline

    for i, st in enumerate(norm):
        label = st.get("label") or st["step"]
        if failed or timed_out():
            results.append({"i": i, "step": st["step"], "label": label,
                            "ok": False, "detail": "timeout" if not failed
                            else "skipped", "ms": 0})
            continue
        t1 = _now()
        ok, detail = True, ""
        if st["step"] == "wait_ms":
            # sleep in slices so a long wait still honors the deadline
            left = st["ms"]
            while left > 0 and not timed_out():
                chunk = min(left, 50, max(1, remaining_ms()))
                _sleep(chunk / 1000.0)
                left -= chunk
            if left > 0:
                ok, detail = False, "timeout during wait_ms"
        else:
            if st["step"] in ("type", "keys"):
                fok, fnote = _verify_focus(provider, ctx)
                if not fok:
                    ok, detail = False, fnote or "focus-unverified"
            if ok:
                action = _step_action(st)
                try:
                    res = provider.run(action, ctx)
                except Exception as e:
                    res = None
                    ok, detail = False, "provider.run raised %s" % e
                if res is not None:
                    ok, detail = _res_ok(res), _res_detail(res)
        ms = int((_now() - t1) * 1000)
        optional = bool(st.get("optional"))
        results.append({"i": i, "step": st["step"], "label": label,
                        "ok": ok or optional, "detail": detail,
                        "ms": ms,
                        **({"optional": True} if optional else {})})
        ran += 1
        if not ok and not optional:
            failed = True
        # delay_ms = extra wait AFTER this step (gives open_app time etc.)
        dl = st.get("delay_ms")
        if isinstance(dl, (int, float)) and dl > 0 and not timed_out():
            _sleep(min(dl, max(0, remaining_ms())) / 1000.0)

    ok_all = not any(not r["ok"] for r in results)
    elapsed = int((_now() - t0) * 1000)
    err = None
    if failed:
        err = "stopped at step %d" % next(
            r["i"] for r in results if not r["ok"] and not r.get("optional"))
    elif not ok_all:
        err = "timeout"
    return {"ok": ok_all, "ran": ran, "steps": results,
            "elapsed_ms": elapsed, "error": err}


def run_macro_action(action, provider, ctx=None):
    """Adapter for a bound {"kind":"macro","steps":[...]} bank action —
    the dispatcher (act_linux) routes kind:macro here."""
    res = run_macro((action or {}).get("steps"), provider, ctx=ctx)
    # surface as an ActionResult-shaped answer through the provider's own
    # result type when available, else the plain dict
    try:
        from act_iface import ActionResult
        return ActionResult(ok=res["ok"],
                            detail=res["error"] or
                            "%d/%d steps" % (res["ran"], len(res["steps"])),
                            tier=int((action or {}).get("tier", 2)),
                            confirm_id=None)
    except Exception:
        return res


if __name__ == "__main__":
    import json
    import sys
    doc = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else []
    norm, errs = validate_steps(doc)
    print(json.dumps({"steps": norm, "errors": errs}, indent=2))
