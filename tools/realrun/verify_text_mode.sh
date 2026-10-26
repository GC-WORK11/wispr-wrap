#!/usr/bin/env bash
# verify_text_mode — Go Live TEXT mode end to end on the real stack:
#   teach a sign (label "verify-text") -> POST /live/mode text ->
#   palm (listening) -> perform the sign -> the word is TYPED into the
#   focused real editor (SinkEdit), the sign's action does NOT fire.
# This is the sign-to-text half of the founder's Text|Control toggle.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/text_mode"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

G="RR-TEXT"; WORD="verify-text-$(date +%s)"
log "text mode: teach $G -> mode text -> sign types '$WORD' into SinkEdit"

# --- pair -------------------------------------------------------------------
info=$(curl -sf "$BASE/pair-info") || { verdict UNVERIFIABLE "no /pair-info"; }
PC="$(echo "$info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')"
PAIR_CODE="$PC"
sleep 1.1
call POST /pair "{\"code\":\"$PC\"}" >/dev/null
[ "$CALL_CODE" = 200 ] || verdict FAILED "pair failed ($CALL_CODE)"

# --- teach the sign (no action binding — text mode types the WORD) ----------
code=$(call POST /teach/begin "{\"name\":\"$G\",\"takes\":1,\"label\":\"$WORD\"}")
[ "$code" = 200 ] || verdict FAILED "teach/begin $code"
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 \
    "teach:$G" >/dev/null
sleep 0.25
call POST /teach/end '{}' >/dev/null
# bind nothing: PUT /bank with only a label annotates the sign — text
# mode types THAT word. (argv:"" is a real 400 — omit it entirely.)
code=$(call PUT /bank "{\"gloss\":\"$G\",\"label\":\"$WORD\"}")
[ "$code" = 200 ] || { call_body; verdict FAILED "PUT /bank $code"; }
echo "taught $G label='$WORD' (no bound action)" | tee "$PROOF/teach.txt"

# --- switch to TEXT mode ----------------------------------------------------
call POST /live/mode '{"mode":"text"}' >/dev/null
[ "$CALL_CODE" = 200 ] || { call_body; verdict FAILED "mode text $CALL_CODE"; }
call_body > "$PROOF/mode.json"
call_body | grep -q '"text_mode": *true' \
    || verdict FAILED "daemon did not enter text mode: $CALL_BODY"

focus_term || verdict FAILED "could not focus the editor window"
SINK_SHA_BEFORE=$(sha256sum "$SINK" 2>/dev/null | cut -d' ' -f1)
MARK_BEFORE=$(ls "$MARKERS" 2>/dev/null | wc -l)

# --- arm + perform ----------------------------------------------------------
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 palm \
    >/dev/null 2>&1
sleep 0.3
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 \
    "sign:$G" | tee "$PROOF/sign-resp.log" >/dev/null

for i in $(seq 1 40); do
    grep -q "$WORD" "$SINK" 2>/dev/null && break; sleep 0.2
done

shot "$PROOF/after.png"
MARK_AFTER=$(ls "$MARKERS" 2>/dev/null | wc -l)
{
    echo "sink sha before: $SINK_SHA_BEFORE"
    echo "sink sha after:  $(sha256sum "$SINK" 2>/dev/null | cut -d' ' -f1)"
    echo "markers before/after: $MARK_BEFORE/$MARK_AFTER"
} | tee "$PROOF/effects.txt"

if grep -q "$WORD" "$SINK" 2>/dev/null; then
    if [ "$MARK_AFTER" -gt "$MARK_BEFORE" ]; then
        verdict FAILED "text typed BUT a marker also fired — action leaked in text mode"
    fi
    verdict VERIFIED "sign -> its label typed into the real focused editor (no action fired)"
else
    verdict FAILED "typed word never landed in the editor"
fi
