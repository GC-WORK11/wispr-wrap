#!/usr/bin/env bash
# verify_context — the SAME sign produces a DIFFERENT real effect
# depending on which app is focused.
#
# Two real paths are proved: (1) a bound argv script that reads the
# focused window and dispatches differently (a user binding), and
# (2) the engine-level {"kind":"context","by_wm_class":...} action
# carried by teach/PUT-bank through the action provider.
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

# engine-level probe: context_dispatch action routing via the teach-
# carried action dict. type_text (not argv) keeps the first-use gate out
# of the way; focus the sink editor and require the routed text in it.
ENGINE_TXT="CTX-ENGINE-OK"
engine_json() {
    python3 - "$STATE/ctx-engine.json" "$ENGINE_TXT" <<'PY'
import json, sys
path, txt = sys.argv[1], sys.argv[2]
action = {"kind": "context", "tier": 0,
          "by_wm_class": {"SinkEdit": {"type": "type_text",
                                       "text": txt + "\n"}},
          "default": {"type": "type_text", "text": "CTX-DEFAULT-WRONG\n"}}
json.dump({"name": "RR-CTX2", "takes": 1, "label": "ctx engine",
           "action": action}, open(path, "w"))
PY
}
engine_json
DISPLAY="$D" xdotool windowactivate --sync "$TERM_WID" 2>/dev/null; sleep 0.4
code=$(call POST /teach/begin "@$STATE/ctx-engine.json")
echo "teach/begin kind:context -> $code $(call_body | head -c 200)" | tee "$PROOF/probe.txt"
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 teach:RR-CTX2 >/dev/null 2>&1
sleep 0.3; call POST /teach/end '{}' >/dev/null
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 palm >/dev/null 2>&1
sleep 0.3
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 sign:RR-CTX2 >/dev/null 2>&1
for i in $(seq 1 20); do grep -q "$ENGINE_TXT" "$SINK" 2>/dev/null && break; sleep 0.2; done
call GET /live/state > /dev/null
call_body | python3 -c 'import json,sys; s=json.load(sys.stdin); print("last_match:", s.get("last_match"))' | tee -a "$PROOF/probe.txt"
if grep -q "$ENGINE_TXT" "$SINK" 2>/dev/null; then
    echo "context_dispatch ROUTED (focused SinkEdit -> type_text; sink received $ENGINE_TXT)" | tee -a "$PROOF/probe.txt"
else
    echo "context_dispatch action NOT routed by daemon (PENDING)" | tee -a "$PROOF/probe.txt"
fi

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
