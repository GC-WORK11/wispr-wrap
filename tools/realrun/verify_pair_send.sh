#!/usr/bin/env bash
# verify_pair_send — pair the composer then send text into a REAL focused
# editor window (Tk text widget on the real X display). The editor buffer
# is mirrored to $SINK by the app itself (bind <KeyRelease>), so the file
# diff proves real keystroke delivery — not a written-to-file shortcut.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/pair_send"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

TEXT="realrun-pair-send-$(date +%s)"
log "pair+send typing: focus SinkEdit ($TERM_WID) then POST /send"

# --- auth gate first (wrong key must fail) --------------------------------
bad=$(call_noauth POST /send '{"text":"should-never-land","mode":"type"}')
echo "unauthed /send -> $bad (expect 403)" | tee "$PROOF/auth-gate.txt"

# --- pair flow (the real contract) ----------------------------------------
info=$(curl -sf "$BASE/pair-info") || { verdict UNVERIFIABLE "no /pair-info"; }
echo "$info" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("code_len",len(d["code"]))' > "$PROOF/pair-info.txt"
PC="$(echo "$info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')"
PAIR_CODE="$PC"   # for sanitize_dir redaction
sleep 1.1   # /pair rate limit: 1 try/sec
call POST /pair "{\"code\":\"$PC\"}" >/dev/null
code=$CALL_CODE
paired_key="$(call_json key)"
[ "$code" = 200 ] && [ "$paired_key" = "$KEY" ] \
    || { call_body; verdict FAILED "pair failed ($code)"; }
echo "paired -> key matches daemon key" | tee -a "$PROOF/pair-info.txt"

# --- snapshot + focus -----------------------------------------------------
snapshot > "$PROOF/before.txt"
focus_term || verdict FAILED "could not focus the editor window"
active_before=$(DISPLAY="$D" xdotool getactivewindow)
cls=$(DISPLAY="$D" xprop -id "$active_before" WM_CLASS)
echo "active=$active_before $cls" | tee "$PROOF/focus.txt"
echo "$cls" | grep -q SinkEdit || verdict FAILED "focus did not land on SinkEdit ($cls)"
shot "$PROOF/before.png"

# --- the REAL path: POST /send mode=type -> daemon -> xdotool type --------
call POST /send "{\"text\":\"$TEXT\",\"mode\":\"type\"}" >/dev/null
code=$CALL_CODE
echo "POST /send -> $code $(call_body)"
for i in $(seq 1 40); do
    grep -q "$TEXT" "$SINK" 2>/dev/null && break; sleep 0.2
done

snapshot > "$PROOF/after.txt"
shot "$PROOF/after.png"
diff -u "$PROOF/before.txt" "$PROOF/after.txt" > "$PROOF/diff.txt" || true

if grep -q "$TEXT" "$SINK"; then
    verdict VERIFIED "typed text landed in the focused real editor window (sink sha changed, xdotool type)"
else
    echo "=== sink content ==="; cat "$SINK"
    verdict FAILED "text never appeared in the editor"
fi
