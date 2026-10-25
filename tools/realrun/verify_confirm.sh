#!/usr/bin/env bash
# verify_confirm — the tier>=2 laptop-side confirm gate: sign parks the
# action, NOTHING spawns, POST /act/confirm releases it (and cancel
# buries it). The parked action is observable on GET /act pending and
# /live/state.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/confirm"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

M1="$MARKERS/park-fired"; M2="$MARKERS/park-cancelled"
rm -f "$M1" "$M2"
snapshot > "$PROOF/before.txt"

# bind + fire a tier-2 action
teach_bind_fire RR-PARK "parked action" "/usr/bin/touch $M1" 2 || {
    rc=$?; [ "$rc" = 3 ] && verdict PENDING "endpoints not wired"
    verdict FAILED "teach_bind_fire rc=$rc"; }
sleep 0.8
echo "--- after tier-2 sign (must NOT have fired) ---" | tee -a "$PROOF/RESULT.detail"
call GET /live/state > /dev/null
call_body | python3 -c 'import json,sys; s=json.load(sys.stdin); print(json.dumps({"listening":s.get("listening"),"last_match":s.get("last_match"),"pending":s.get("pending")},indent=1))' | tee "$PROOF/state-parked.json"
[ -f "$M1" ] && verdict FAILED "tier-2 action fired WITHOUT confirm — gate broken"

CID=$(python3 -c "import json; s=json.load(open('$PROOF/state-parked.json')); p=s.get('pending') or []; print(p[0]['id'] if p else '')")
[ -n "$CID" ] || { call GET /act > /dev/null; call_body | tee "$PROOF/act-pending.json"; CID=$(python3 -c "import json; p=json.load(open('$PROOF/act-pending.json')).get('pending') or []; print(p[0]['id'] if p else '')"); }
[ -n "$CID" ] || verdict FAILED "no pending confirm surfaced on /live/state or /act"
echo "pending confirm id: $CID" | tee -a "$PROOF/RESULT.detail"

call POST /act/confirm "{\"id\":\"$CID\"}" >/dev/null
code=$CALL_CODE
echo "POST /act/confirm -> $code $(call_body)"
wait_file "$M1" 30 || verdict FAILED "confirmed action never spawned"

# cancel path: second parked action, denied
teach_bind_fire RR-PARK2 "cancelled action" "/usr/bin/touch $M2" 2 || {
    rc=$?; [ "$rc" = 3 ] && verdict PENDING "endpoints not wired"
    verdict FAILED "teach_bind_fire rc=$rc"; }
sleep 0.8
call GET /live/state > /dev/null
CID2=$(call_body | python3 -c 'import json,sys; p=json.load(sys.stdin).get("pending") or []; print(p[-1]["id"] if p else "")')
echo "second pending id: $CID2"
call POST /act/confirm "{\"id\":\"$CID2\",\"cancel\":true}" >/dev/null
code=$CALL_CODE
echo "cancel -> $code $(call_body)"
sleep 0.6
[ -f "$M2" ] && verdict FAILED "cancelled tier-2 action still fired"

snapshot > "$PROOF/after.txt"
diff -u "$PROOF/before.txt" "$PROOF/after.txt" > "$PROOF/diff.txt" || true

if [ -f "$M1" ] && [ ! -f "$M2" ]; then
    verdict VERIFIED "tier-2 gate real: sign parked (no spawn), /act/confirm fired it, cancel buried the second — all through real routes"
else
    verdict FAILED "gate semantics broken (fired=$([ -f "$M1" ] && echo yes || echo no) cancelled-spawned=$([ -f "$M2" ] && echo yes || echo no))"
fi
