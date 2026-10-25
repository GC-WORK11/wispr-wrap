#!/usr/bin/env bash
# verify_context — the SAME sign produces a DIFFERENT real effect
# depending on which app is focused.
#
# Honest scope: the provider-level context_dispatch action (per_app
# tables routed inside act_iface) has no daemon route today — probed
# below. The user-reachable path that DOES exist is a bound argv: a
# script that reads the focused window and dispatches differently — a
# real binding a user could write. We prove both facts: the effect is
# real and observable, and the engine-level route is pending.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/context"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

# --- context dispatcher script (a real user-bound argv) -------------------
cat > "$STATE/ctx_dispatch.py" <<PY
#!/usr/bin/env python3
"""context-dispatch binding: same sign -> different marker per focused
app (reads the REAL focused window via xdotool/xprop)."""
import subprocess, sys, os
D = "$D"; M = "$MARKERS"
def sh(*a):
    return subprocess.run([*a], env={"DISPLAY": D, "PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True, timeout=8)
wid = sh("xdotool", "getactivewindow").stdout.strip()
cls = ""
if wid.isdigit():
    out = sh("xprop", "-id", wid, "WM_CLASS").stdout
    import re
    m = re.findall(r'"([^"]+)"', out)
    cls = (m[-1] if m else "?")
open(os.path.join(M, "ctx-" + (cls or "none")), "w").write(
    "fired while focused: %s (%s)\n" % (cls or "?", wid))
PY
chmod +x "$STATE/ctx_dispatch.py"
rm -f "$MARKERS/ctx-SinkEdit" "$MARKERS/ctx-Xfce4-terminal"

teach_bind_fire RR-CTX "context dispatch" \
    "python3 $STATE/ctx_dispatch.py" || {
        rc=$?; [ "$rc" = 3 ] && verdict PENDING "endpoints not wired"
        verdict FAILED "teach_bind_fire rc=$rc"; }

# fire 1: sink editor focused
DISPLAY="$D" xdotool windowactivate --sync "$TERM_WID" 2>/dev/null; sleep 0.4
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 sign:RR-CTX >/dev/null
sleep 0.8
ls "$MARKERS"/ctx-* > "$PROOF/focus1.txt" 2>/dev/null || echo "no marker yet" > "$PROOF/focus1.txt"

# fire 2: terminal focused — SAME sign, different real effect expected
DISPLAY="$D" xdotool windowactivate --sync "$TERM2_WID" 2>/dev/null; sleep 0.4
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 sign:RR-CTX >/dev/null
sleep 0.8
ls "$MARKERS"/ctx-* > "$PROOF/focus2.txt" 2>/dev/null || echo "no marker yet" > "$PROOF/focus2.txt"

# engine-level probe: context_dispatch action routing via teach-carried
# action dict (stored opaquely) — the honest "is the provider wired" test
rm -f "$MARKERS/ctx-engine"
code=$(call POST /teach/begin '{"name":"RR-CTX2","takes":1,"label":"ctx engine","action":{"kind":"context","by_wm_class":{"sinkedit":{"argv":["/usr/bin/touch","'$MARKERS'/ctx-engine"]},"default":{"argv":["/usr/bin/true"]}},"tier":0}')
echo "teach/begin kind:context -> $code" | tee "$PROOF/probe.txt"
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 teach:RR-CTX2 >/dev/null 2>&1
sleep 0.3; call POST /teach/end '{}' >/dev/null
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 sign:RR-CTX2 >/dev/null 2>&1
sleep 0.6
call GET /live/state > /dev/null
call_body | python3 -c 'import json,sys; s=json.load(sys.stdin); print("last_match:", s.get("last_match"))' | tee -a "$PROOF/probe.txt"
[ -f "$MARKERS/ctx-engine" ] \
    && echo "context_dispatch ROUTED" | tee -a "$PROOF/probe.txt" \
    || echo "context_dispatch action NOT routed by daemon (PENDING)" | tee -a "$PROOF/probe.txt"

snapshot > "$PROOF/after.txt"
DIFFED=$(comm -13 <(sort "$PROOF/focus1.txt") <(sort "$PROOF/focus2.txt"))
echo "=== marker set grew ==="; cat "$PROOF/focus1.txt" "$PROOF/focus2.txt"

if [ -f "$MARKERS/ctx-SinkEdit" ] && [ -f "$MARKERS/ctx-Xfce4-terminal" ]; then
    verdict VERIFIED "same sign -> different effect per focused app (ctx-SinkEdit + ctx-Xfce4-terminal markers); engine context_dispatch: $(tail -1 "$PROOF/probe.txt")"
elif [ -f "$MARKERS/ctx-SinkEdit" ] || [ -f "$MARKERS/ctx-Xfce4-terminal" ]; then
    verdict FAILED "only one context produced a marker ($(cat "$PROOF/focus1.txt" "$PROOF/focus2.txt" | tr '\n' ' '))"
else
    verdict FAILED "no context marker written"
fi
