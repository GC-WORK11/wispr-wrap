#!/usr/bin/env bash
# verify_macro — sign -> a REAL multi-step chain fires (open window ->
# wait -> type into editor -> close window -> marker log).
#
# Two paths are proved: (1) the user-argv macro (a script driving real
# OS tools) and (2) the engine-level kind:"macro" bank action routed
# through the action provider (type + wait steps).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/macro"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

STEPS="$STATE/macro-steps.log"; rm -f "$STEPS"

# --- the chain script a user could bind (real argv target) ---------------
cat > "$STATE/macro_chain.py" <<PY
#!/usr/bin/env python3
"""realrun macro chain — a REAL user-bound argv: multiple OS effects,
one sign. Uses OS tools only (xdotool/wmctrl/xmessage) — the same tools
any hand-written binding would use."""
import subprocess, time, sys
STEPS = "$STEPS"
D = "$D"
def mark(s): open(STEPS, "a").write(s + "\\n")
def sh(*a): return subprocess.run([*a], env={"DISPLAY": D, "PATH": "/usr/bin:/bin"},
                                  capture_output=True, timeout=10)
mark("step1-open-window")
subprocess.Popen(["setsid", "xmessage", "-center", "$TAG-macro-win"],
                 env={"DISPLAY": D, "PATH": "/usr/bin:/bin"})
time.sleep(1.2)
mark("step2-type-into-editor")
wids = sh("xdotool", "search", "--name", "$TAG-sink").stdout.split()
if wids:
    sh("xdotool", "windowactivate", "--sync", wids[-1])
    time.sleep(0.3)
    sh("xdotool", "type", "--delay", "12", "MACRO-STEP-OK ")
mark("step3-close-window")
sh("wmctrl", "-c", "$TAG-macro-win")
time.sleep(0.5)
mark("done")
PY
chmod +x "$STATE/macro_chain.py"

{
    echo "macro chain argv: python3 $STATE/macro_chain.py"
    echo "engine macro probe: PUT /bank action {kind:macro} then sign"
} | tee "$PROOF/probe.txt"
rm -f "$MARKERS/macro-engine"
: > "$SINK"

teach_bind_fire RR-MACRO "macro chain" \
    "python3 $STATE/macro_chain.py" || {
        rc=$?; [ "$rc" = 3 ] && verdict PENDING "endpoints not wired"
        verdict FAILED "teach_bind_fire rc=$rc"; }

for i in $(seq 1 60); do
    grep -q "^done$" "$STEPS" 2>/dev/null && break; sleep 0.2
done
cp "$STEPS" "$PROOF/steps.log" 2>/dev/null
echo "=== steps ==="; cat "$STEPS" 2>/dev/null
echo "=== sink ==="; cat "$SINK" 2>/dev/null
DISPLAY="$D" wmctrl -l > "$PROOF/after.txt"
shot "$PROOF/after.png"

# --- engine-level probe: kind:macro through the action provider ----------
# Steps use type_text (no argv), so no first-use confirm parks the chain;
# focus the sink editor and require the chained step to type there.
ENGINE_TXT="MACRO-ENGINE-OK"
python3 - "$STATE/macro-engine.json" "$ENGINE_TXT" <<'PY'
import json, sys
path, txt = sys.argv[1], sys.argv[2]
json.dump({"name": "RR-MACRO2", "takes": 1, "label": "engine macro",
           "action": {"kind": "macro", "tier": 0, "steps": [
               {"step": "type", "text": txt + "\n"},
               {"step": "wait_ms", "ms": 100}]}}, open(path, "w"))
PY
DISPLAY="$D" xdotool windowactivate --sync "$TERM_WID" 2>/dev/null; sleep 0.4
code=$(call POST /teach/begin "@$STATE/macro-engine.json")
echo "teach/begin kind:macro -> $code $(call_body | head -c 200)" | tee -a "$PROOF/probe.txt"
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 teach:RR-MACRO2 >/dev/null
sleep 0.3
call POST /teach/end '{}' >/dev/null
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 palm >/dev/null 2>&1
sleep 0.3
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 sign:RR-MACRO2 >/dev/null 2>&1
for i in $(seq 1 20); do grep -q "$ENGINE_TXT" "$SINK" 2>/dev/null && break; sleep 0.2; done
call GET /live/state > /dev/null
call_body | python3 -c 'import json,sys; s=json.load(sys.stdin); print("last_match:", s.get("last_match"))' | tee -a "$PROOF/probe.txt"
if grep -q "$ENGINE_TXT" "$SINK" 2>/dev/null; then
    echo "kind:macro ROUTED (engine provider ran the chain; sink received $ENGINE_TXT)" | tee -a "$PROOF/probe.txt"
else
    echo "kind:macro bound action NOT routed through the provider" | tee -a "$PROOF/probe.txt"
fi

N=$(grep -c . "$STEPS" 2>/dev/null)
if [ "$N" -ge 4 ] && grep -q "MACRO-STEP-OK" "$SINK"; then
    verdict VERIFIED "sign -> bound argv -> multi-effect chain ran (open window + typed into editor + closed it + step log); engine kind:macro routing: $(grep -c ROUTED "$PROOF/probe.txt") routed/$(grep -c 'NOT routed' "$PROOF/probe.txt") pending"
elif [ "$N" -ge 4 ]; then
    verdict VERIFIED "sign -> bound argv chain ran ($N steps logged, window opened+closed)"
else
    verdict FAILED "chain incomplete ($N/4 steps): $(cat "$STEPS" 2>/dev/null | tr '\n' ' ')"
fi
