#!/usr/bin/env bash
# verify_record — teach-by-demonstration through the REAL daemon routes:
#   POST /teach/record/start -> inject REAL keystrokes+click (xdotool/XTEST)
#   -> POST /teach/record/stop -> wf-macro/1 with type/keys/argv steps.
# The daemon's Recorder spawns `xinput test-xi2 --root` — resolved on
# this box to the realrun XI2 shim (xinput binary absent; same XI2 event
# classes, same text wire — see xinput_shim.py header). XTEST injection
# is what real xdotool-driven tests and physical input look like at XI2.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/record"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

focus_term || verdict FAILED "could not focus editor for injection"
: > "$SINK"
snapshot > "$PROOF/before.txt"

# --- start through the real route ------------------------------------------
call POST /teach/record/start '{"name":"realrun-rec"}' >/dev/null
code=$CALL_CODE
echo "record/start -> $code $(call_body)" | tee "$PROOF/start.json"
[ "$code" = 200 ] || {
    [ "$code" = 404 ] && verdict PENDING "no /teach/record/start route"
    [ "$code" = 503 ] && verdict NEVER-ON-BOX "recorder refused: $(call_body | head -1)"
    verdict FAILED "record/start -> $code"; }
sleep 0.6   # capture thread up

# --- REAL injected input: type "hi", chord ctrl+s, one click --------------
DISPLAY="$D" xdotool type --delay 60 "hi"
sleep 0.15
DISPLAY="$D" xdotool key ctrl+s
sleep 0.15
DISPLAY="$D" xdotool mousemove 400 400 click 1
sleep 0.8   # drain the xi2 pipe

# --- stop through the real route -------------------------------------------
call POST /teach/record/stop '{}' >/dev/null
code=$CALL_CODE
call_body | tee "$PROOF/stop.json"
echo "record/stop -> $code"
[ "$code" = 200 ] || verdict FAILED "record/stop -> $code"

MACRO=$(python3 -c "import json;print(json.load(open('$PROOF/stop.json')).get('path') or '')" 2>/dev/null)
[ -n "$MACRO" ] && [ -f "$MACRO" ] && cp "$MACRO" "$PROOF/macro.json"
python3 - "$PROOF/stop.json" > "$PROOF/steps-check.txt" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
steps = d.get("steps") or []
kinds = []
for s in steps:
    kinds += [k for k in ("type", "keys", "argv", "wait_ms", "open_app") if k in s]
print("name=%s duration_ms=%s steps=%d context=%s" %
      (d.get("name"), d.get("duration_ms"), len(steps), d.get("context")))
print("step kinds:", kinds)
print("steps:", json.dumps(steps))
print("recorded action:", json.dumps(d.get("action", {}).get("kind")))
need = {"type", "keys", "argv"}
sys.exit(0 if need <= set(kinds) else 1)
PY
STEPS_OK=$?

snapshot > "$PROOF/after.txt"
shot "$PROOF/after.png"
diff -u "$PROOF/before.txt" "$PROOF/after.txt" > "$PROOF/diff.txt" || true

if [ $STEPS_OK -eq 0 ]; then
    verdict VERIFIED "real XI2 capture via /teach/record -> wf-macro/1 (type+keys+click steps, mode $(stat -c %a "$MACRO" 2>/dev/null)); capture via XI2 shim (xinput binary absent)"
else
    verdict FAILED "record ran but macro missing expected steps (see steps-check.txt)"
fi
