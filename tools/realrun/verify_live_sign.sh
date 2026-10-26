#!/usr/bin/env bash
# verify_live_sign — the flagship proof: teach a sign through the REAL
# /teach API, bind it to an argv through PUT /bank (the real binding
# path — it writes commands.tsv), then perform it through /live/kp and
# observe the marker file the daemon spawns. Also proves the listen gate:
# the same sign before the palm-hold commits nothing.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/live_sign"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

G=RR-SWIPE
MARKER="$MARKERS/rr-swipe"
rm -f "$MARKER"
snapshot > "$PROOF/before.txt"

# --- teach through the real endpoints -------------------------------------
call POST /teach/begin "{\"name\":\"$G\",\"takes\":1,\"label\":\"realrun swipe\"}" >/dev/null
code=$CALL_CODE
echo "teach/begin -> $code $(call_body)"
[ "$code" = 200 ] || verdict PENDING "/teach/begin -> $code (not wired)"
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 teach:$G
sleep 0.3
call POST /teach/end '{}' >/dev/null
code=$CALL_CODE
echo "teach/end -> $code $(call_body)"
call GET /bank > /dev/null; call_body | python3 -c '
import json,sys
rows=json.load(sys.stdin).get("signs",[])
for r in rows:
    if r.get("gloss")=="RR-SWIPE":
        print("bank row:", json.dumps(r))
' | tee "$PROOF/taught.txt"

# --- bind through the real API (writes commands.tsv, no shell) -------------
call PUT /bank "{\"gloss\":\"$G\",\"label\":\"realrun swipe\",\"argv\":\"/usr/bin/touch $MARKER\",\"tier\":0}" >/dev/null
code=$CALL_CODE
echo "PUT /bank -> $code $(call_body)" | tee "$PROOF/bind.txt"
[ "$code" = 200 ] || verdict FAILED "bind failed"

# --- gate check: same sign WITHOUT listening must not commit ---------------
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 namaste 2>/dev/null
sleep 0.3
ls "$MARKER" 2>/dev/null && verdict FAILED "marker existed before any sign"
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 sign:$G
sleep 0.5
if [ -f "$MARKER" ]; then
    verdict FAILED "sign committed while NOT listening (palm gate broken)"
else
    echo "gate OK: sign while not listening -> no marker" | tee "$PROOF/gate.txt"
fi

# --- palm hold -> listening -> perform -> marker ----------------------------
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 palm
sleep 0.4
call GET /live/state > /dev/null; call_body | tee "$PROOF/state-after-palm.json"
python3 - <<PY
import json
s = json.load(open("$PROOF/state-after-palm.json"))
assert s.get("listening"), "daemon did not enter listening after palm hold"
print("listening=True after palm hold")
PY
[ $? -eq 0 ] || verdict FAILED "palm hold did not start listening"

python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 sign:$G
for i in $(seq 1 30); do [ -f "$MARKER" ] && break; sleep 0.2; done
call GET /live/state > /dev/null; call_body > "$PROOF/state-after-sign.json"

# A second open-palm hold while ALREADY listening must not overwrite the
# last sign reading with "listening" (the reading strip flipped before).
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 palm >/dev/null 2>&1
sleep 0.4
call GET /live/state > /dev/null; call_body > "$PROOF/state-after-extra-palm.json"
KEEP=0
python3 - "$PROOF/state-after-extra-palm.json" "$G" <<'PY' && KEEP=1
import json, sys
s = json.load(open(sys.argv[1]))
lm = s.get("last_match") or {}
assert s.get("listening") is True, s.get("listening")
assert lm.get("gloss") == sys.argv[2], lm
print("redundant palm kept last_match=%s" % lm.get("gloss"))
PY
echo "redundant palm kept last_match: $KEEP" | tee -a "$PROOF/RESULT.detail"
snapshot > "$PROOF/after.txt"
shot "$PROOF/after.png"
diff -u "$PROOF/before.txt" "$PROOF/after.txt" > "$PROOF/diff.txt" || true

# The app parses /live/state with JSON.parse: Python's Infinity/NaN are
# INVALID JSON. With one taught sign `margin` is infinite, so this exact
# reply is the regression case (state poll froze in the browser).
STRICT=1
python3 - "$PROOF/state-after-sign.json" <<'PY' || STRICT=0
import json, sys
def bad(c):
    raise ValueError("non-finite JSON constant: " + c)
json.loads(open(sys.argv[1]).read(), parse_constant=bad)
print("strict JSON: ok")
PY
echo "strict JSON parse: $STRICT" | tee -a "$PROOF/RESULT.detail"

if [ -f "$MARKER" ] && [ "$STRICT" = 1 ] && [ "$KEEP" = 1 ]; then
    last=$(python3 -c "import json; s=json.load(open('$PROOF/state-after-sign.json')); print(s.get('last_match'))" 2>/dev/null)
    echo "marker spawned: $MARKER; last_match=$last" >> "$PROOF/RESULT.detail"
    verdict VERIFIED "synthetic sign -> endpointer -> DTW match -> argv spawned (marker file + last_match accepted); /live/state with a one-sign bank is strict JSON; a redundant palm keeps the last sign reading"
elif [ -f "$MARKER" ] && [ "$STRICT" != 1 ]; then
    verdict FAILED "/live/state is not strict JSON (Infinity/NaN) — the browser state poll would freeze"
elif [ -f "$MARKER" ]; then
    verdict FAILED "a redundant open-palm hold overwrote last_match (reading flipped to listening)"
else
    cat "$PROOF/state-after-sign.json"
    verdict FAILED "sign matched nothing / action never spawned"
fi
