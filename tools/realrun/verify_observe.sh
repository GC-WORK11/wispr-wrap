#!/usr/bin/env bash
# verify_observe — rehearsal mode: the real sign pipeline (endpointer ->
# DTW -> signflow_ops) runs and REPORTS everything it would do, but no
# action is dispatched. Then the SAME sign, switched back to live through
# the same route, really fires — so "no effect" is observe mode, not a
# dead path.
#
# Route under test: POST /live/mode {"mode":"observe"|"live"} (the daemon
# has no /ops surface; the old probe in this verifier looked for routes
# that never existed).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/observe"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

G=RR-OBS
MARKER="$MARKERS/rr-observe"
rm -f "$MARKER"
snapshot > "$PROOF/before.txt"

# --- teach + bind through the real endpoints ------------------------------
call POST /teach/begin "{\"name\":\"$G\",\"takes\":1,\"label\":\"observe probe\"}" >/dev/null
code=$CALL_CODE
echo "teach/begin -> $code $(call_body)" | tee "$PROOF/teach.txt"
[ "$code" = 200 ] || verdict PENDING "/teach/begin -> $code (not wired)"
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 "teach:$G" >/dev/null
sleep 0.3
call POST /teach/end '{}' >/dev/null
echo "teach/end -> $CALL_CODE $(call_body)" >> "$PROOF/teach.txt"
call PUT /bank "{\"gloss\":\"$G\",\"label\":\"observe probe\",\"argv\":\"/usr/bin/touch $MARKER\",\"tier\":0}" >/dev/null
code=$CALL_CODE
echo "PUT /bank -> $code $(call_body)" | tee "$PROOF/bind.txt"
[ "$code" = 200 ] || verdict FAILED "bind failed ($code)"

# --- enter observe mode through the public route --------------------------
call POST /live/mode '{"mode":"observe"}' >/dev/null
code=$CALL_CODE
echo "POST /live/mode observe -> $code $(call_body)" | tee "$PROOF/mode.txt"
[ "$code" = 200 ] || verdict FAILED "observe route -> $code"
call GET /live/state >/dev/null
cp "$CALL_BODY" "$PROOF/state-observe.json"
python3 - "$PROOF/state-observe.json" <<'PY' | tee -a "$PROOF/mode.txt"
import json, sys
s = json.load(open(sys.argv[1]))
assert s.get("observe_mode") is True, s.get("observe_mode")
print("state: observe_mode=True")
PY
[ $? -eq 0 ] || verdict FAILED "daemon state did not report observe_mode"

# --- fire the bound sign while observing: report, never dispatch ----------
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 palm >/dev/null 2>&1
sleep 0.3
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 "sign:$G" >/dev/null 2>&1
sleep 0.8
if [ -f "$MARKER" ]; then
    snapshot > "$PROOF/after.txt"
    verdict FAILED "observe mode dispatched the bound action (marker exists)"
fi
call GET /live/state >/dev/null
cp "$CALL_BODY" "$PROOF/state-after-observe.json"
python3 - "$PROOF/state-after-observe.json" "$G" <<'PY' | tee "$PROOF/observe.txt"
import json, sys
s = json.load(open(sys.argv[1])); g = sys.argv[2]
la = s.get("last_action") or {}
assert la.get("observe") is True, la
assert la.get("dispatched") is False, la
assert g in (la.get("glosses") or []), la
feed = s.get("feed") or []
assert any(e.get("type") == "would-fire" and g in (e.get("glosses") or [])
           for e in feed), feed[-3:]
print("would-fire reported: gloss=%s label=%r dispatched=%s action-not-executed, marker=absent" %
      (g, la.get("label"), la.get("dispatched")))
PY
[ $? -eq 0 ] || verdict FAILED "observe mode did not report the match/would-fire"
echo "no marker written in observe mode (checked $MARKER)" >> "$PROOF/observe.txt"

# --- same sign, same listening session, now live: real OS effect ----------
call POST /live/mode '{"mode":"live"}' >/dev/null
code=$CALL_CODE
echo "POST /live/mode live -> $code $(call_body)" | tee -a "$PROOF/mode.txt"
[ "$code" = 200 ] || verdict FAILED "live route -> $code"
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 "sign:$G" >/dev/null 2>&1
for i in $(seq 1 30); do [ -f "$MARKER" ] && break; sleep 0.2; done
call GET /live/state >/dev/null
cp "$CALL_BODY" "$PROOF/state-after-live.json"
snapshot > "$PROOF/after.txt"
shot "$PROOF/after.png"
diff -u "$PROOF/before.txt" "$PROOF/after.txt" > "$PROOF/diff.txt" || true

if [ -f "$MARKER" ]; then
    echo "live: marker fired ($MARKER)" | tee "$PROOF/live.txt"
    verdict VERIFIED "observe: match + would-fire reported, no dispatch; live: same sign fired the real argv (marker), toggled via POST /live/mode"
else
    cat "$PROOF/state-after-live.json"
    verdict FAILED "observe path worked but the live control fire did not (marker absent)"
fi
