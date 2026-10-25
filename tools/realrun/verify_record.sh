#!/usr/bin/env bash
# verify_record — teach-by-demonstration: real input events -> macro json.
#
# The daemon has no /record route today (probed below). The real engine
# is server/record_action.py's Recorder — we run IT (real code path:
# probe -> spawn `xinput test-xi2 --root` -> parse -> normalize -> save)
# while injecting REAL keystrokes + a click via xdotool into the focused
# editor. xinput-the-binary is absent on this box, so the spawn resolves
# to tools/realrun/bin/xinput — an XI2-protocol shim (see its header):
# same event classes, same wire format, REAL X server events.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/record"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

{
    echo "daemon record-route probes:"
    for p in /record /record/start /record/stop /record/status /recordings; do
        echo "  GET  $p -> $(call GET "$p")"
        echo "  POST $p -> $(call POST "$p" '{"name":"rr"}')"
    done
} | tee "$PROOF/probe.txt"

focus_term || verdict FAILED "could not focus editor for injection"
: > "$SINK"

export PATH="$STATE/bin:$PATH"   # `xinput` = the XI2 shim (binary absent)
python3 "$HERE/do_record.py" realrun-rec "$WFHOME/.config/wf/recordings" \
    "type:hi" "key:ctrl+s" "click:400,400,1" > "$PROOF/record.json"
rc=$?
cat "$PROOF/record.json"

snapshot > "$PROOF/after.txt"
shot "$PROOF/after.png"
MACRO=$(python3 -c "import json;print(json.load(open('$PROOF/record.json')).get('macro_path') or '')" 2>/dev/null)
[ -n "$MACRO" ] && cp "$MACRO" "$PROOF/macro.json" 2>/dev/null

python3 - "$PROOF/record.json" > "$PROOF/steps-check.txt" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
steps = d.get("steps") or []
kinds = []
for s in steps:
    kinds += [k for k in ("type", "keys", "argv", "wait_ms", "open_app") if k in s]
print("format=%s engine=%s n_events=%s n_steps=%s" %
      (d.get("format"), d.get("engine"), d.get("n_events"), d.get("n_steps")))
print("step kinds:", kinds)
print("steps:", json.dumps(steps))
need = {"type", "keys", "argv"}
sys.exit(0 if need <= set(kinds) else 1)
PY
STEPS_OK=$?

if [ $STEPS_OK -eq 0 ]; then
    verdict VERIFIED "real XI2 capture -> macro.json (format+type/keys/click steps verified, mode $(python3 -c "import json;print(json.load(open('$PROOF/record.json')).get('mode'))" 2>/dev/null)); capture via XI2 shim, daemon route PENDING"
elif [ "$(python3 -c "import json;print(json.load(open('$PROOF/record.json'))['probe'].get('ok'))" 2>/dev/null)" = "False" ]; then
    verdict NEVER-ON-BOX "recorder refused: $(python3 -c "import json;print(json.load(open('$PROOF/record.json'))['probe'].get('reason'))" 2>/dev/null)"
else
    verdict FAILED "record ran but macro missing expected steps (rc=$rc)"
fi
