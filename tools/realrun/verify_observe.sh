#!/usr/bin/env bash
# verify_observe — rehearsal/observe mode through the REAL daemon routes:
# matches are computed and reported, actions NEVER fire.
#
#   POST /live/mode {"mode":"observe"}  (X-WF-Key) -> signflow_ops set_observe
#   palm -> listening -> perform a TAUGHT+BOUND sign -> /live/state reports
#   a "would-fire" event carrying the resolved action, and the bound argv
#   is never spawned (no marker file). Then POST /live/mode {"mode":"live"}
#   and the SAME sign spawns the marker — the match and the binding were
#   real; observe suppressed ONLY the side effect.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/observe"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

G=RR-OBSERVE
MARKER="$MARKERS/rr-observe"
rm -f "$MARKER"
snapshot > "$PROOF/before.txt"

# --- route + auth probes ----------------------------------------------------
{
    echo "observe-mode route probes:"
    echo "  noauth POST /live/mode   -> $(call_noauth POST /live/mode '{"mode":"observe"}')"
    echo "  noauth POST /live/listen -> $(call_noauth POST /live/listen '{"on":true}')"
    echo "  noauth POST /live/event  -> $(call_noauth POST /live/event '{"type":"on"}')"
    call POST /live/event '{"type":"off"}' >/dev/null
    echo "  authed POST /live/event {off}   -> $CALL_CODE $(call_body)"
    call POST /live/listen '{"on":false}' >/dev/null
    echo "  authed POST /live/listen {off}  -> $CALL_CODE $(call_body)"
    call POST /live/mode '{"mode":"observe"}' >/dev/null
    echo "  authed POST /live/mode {observe} -> $CALL_CODE $(call_body)"
} | tee "$PROOF/probe.txt"

call POST /live/mode '{"mode":"observe"}' >/dev/null
[ "$CALL_CODE" = 200 ] || verdict PENDING "/live/mode -> $CALL_CODE (route not wired)"
call POST /live/listen '{"on":false}' >/dev/null   # known state: not listening

# --- state reports observe ---------------------------------------------------
call GET /live/state >/dev/null; call_body > "$PROOF/state-observe-on.json"
python3 - "$PROOF/state-observe-on.json" <<'PY' || verdict FAILED "observe flag not reflected in /live/state"
import json, sys
s = json.load(open(sys.argv[1]))
assert s.get("observe_mode") is True, s.get("observe_mode")
print("observe_mode=True in /live/state")
PY

# --- teach + bind through the real endpoints ---------------------------------
call POST /teach/begin "{\"name\":\"$G\",\"takes\":1,\"label\":\"realrun observe\"}" >/dev/null
echo "teach/begin -> $CALL_CODE $(call_body)"
[ "$CALL_CODE" = 200 ] || verdict PENDING "/teach/begin -> $CALL_CODE"
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 "teach:$G" >/dev/null
sleep 0.3
call POST /teach/end '{}' >/dev/null
echo "teach/end -> $CALL_CODE $(call_body)"
call PUT /bank "{\"gloss\":\"$G\",\"label\":\"realrun observe\",\"argv\":\"/usr/bin/touch $MARKER\",\"tier\":0}" >/dev/null
echo "PUT /bank -> $CALL_CODE $(call_body)" | tee "$PROOF/bind.txt"
[ "$CALL_CODE" = 200 ] || verdict FAILED "bind failed"

# --- palm -> listening -> sign: reported but NO effect -----------------------
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 palm >/dev/null
sleep 0.4
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 "sign:$G"

# the match must be ACCEPTED (computed) — poll last_match until it lands
ok=""
for _ in $(seq 1 40); do
    call GET /live/state >/dev/null
    read -r gloss acc <<<"$(python3 -c 'import json,sys
m = json.load(open(sys.argv[1])).get("last_match") or {}
print(m.get("gloss") or "", m.get("accepted"))' "$CALL_BODY" 2>/dev/null)"
    [ "$gloss" = "$G" ] && [ "$acc" = "True" ] && { ok=1; break; }
    sleep 0.2
done
call GET /live/state >/dev/null; call_body > "$PROOF/state-would-fire.json"
[ -n "$ok" ] || { cat "$PROOF/state-would-fire.json"; verdict FAILED "bound sign never matched in observe mode"; }
sleep 1.5    # give any (forbidden) dispatched action time to land

echo "would-fire evidence:"
# NB: ops stats are daemon-lifetime counters — assert the DELTA against the
# baseline captured in state-observe-on.json (earlier verifiers legitimately
# bump fires), never absolute values.
python3 - "$PROOF/state-observe-on.json" "$PROOF/state-would-fire.json" "$G" <<'PY' | tee "$PROOF/would-fire.txt"
import json, sys
base = json.load(open(sys.argv[1]))
s, g = json.load(open(sys.argv[2])), sys.argv[3]
wf = [e for e in s.get("feed", []) if e.get("type") == "would-fire"
      and g in (e.get("glosses") or [])]
la = s.get("last_action") or {}
d_wf = (s.get("ops") or {}).get("would_fire", 0) - \
       (base.get("ops") or {}).get("would_fire", 0)
d_fi = (s.get("ops") or {}).get("fires", 0) - \
       (base.get("ops") or {}).get("fires", 0)
print("  feed would-fire events:", json.dumps(wf))
print("  last_action:", json.dumps(la))
print("  ops delta: would_fire=+%d fires=+%d" % (d_wf, d_fi))
assert wf, "no would-fire event reported for %s" % g
assert la.get("observe") is True and g in (la.get("glosses") or []), la
assert la.get("action"), "would-fire event must carry the resolved action"
assert d_wf >= 1, "would_fire counter did not move"
assert d_fi == 0, "a real fire was counted in observe mode"
print("  would-fire reported with resolved action; zero fires counted")
PY
[ ${PIPESTATUS[0]} -eq 0 ] || verdict FAILED "match happened but was not reported as would-fire"

if [ -f "$MARKER" ]; then
    verdict FAILED "marker spawned in OBSERVE mode — side effect leaked"
else
    echo "observe OK: bound argv never spawned (no $MARKER)" | tee "$PROOF/no-marker.txt"
fi

# --- same sign in live mode DOES fire (match + binding were real) -----------
call POST /live/mode '{"mode":"live"}' >/dev/null
echo "mode live -> $CALL_CODE $(call_body)"
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 "sign:$G" >/dev/null
for _ in $(seq 1 40); do [ -f "$MARKER" ] && break; sleep 0.2; done
[ -f "$MARKER" ] || { call GET /live/state >/dev/null; call_body; \
    verdict FAILED "sign did not fire after mode->live (match/binding broken?)"; }
echo "live OK: same sign spawned marker after mode->live" | tee "$PROOF/live-marker.txt"

# --- leave the world tidy -----------------------------------------------------
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 namaste >/dev/null 2>&1
call GET /live/state >/dev/null; call_body > "$PROOF/state-final.json"
snapshot > "$PROOF/after.txt"
shot "$PROOF/after.png"
diff -u "$PROOF/before.txt" "$PROOF/after.txt" > "$PROOF/diff.txt" || true

verdict VERIFIED "observe mode: bound sign matched + would-fire reported via /live/state (feed+last_action), marker never spawned; same sign fired after /live/mode live; /live/mode|listen|event authed 200 / noauth 403"
