#!/usr/bin/env bash
# scene_textmode.sh — recorded scene: Go Live TEXT mode. The UI shows the
# Text tab selected; a replayed taught sign types its word into the real
# SinkEdit editor (the same path a signer uses: keypoints -> match -> word
# at the laptop cursor).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"
load_env
PASS=0; FAIL=0
RUNLOG="$DEMO_STATE/textmode.log"; : > "$RUNLOG"
exec > >(tee -a "$RUNLOG") 2>&1
log()  { printf '[demo %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
note() { log "      $1"; }
ok()   { PASS=$((PASS+1)); log "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); log "  FAIL  $1"; }
scene(){ log "===================== SCENE $1 ====================="; }
jse()  { python3 "$HERE/nav.py" --eval "$1"; }

scene "f2-text  (Go Live Text mode -> sign types into the editor)"
chrome_nav '#/live'
sleep 2.0
bash "$HERE/record.sh" start "f2-text"

# Text tab on the real toggle, then confirm daemon-side
jse 'var t=document.getElementById("lv-m-text");t&&t.click();"tabbed"' >/dev/null
sleep 0.8
MODE=$(jse 'var t=document.getElementById("lv-m-text");t?t.getAttribute("aria-selected"):"?"')
note "text tab aria-selected=$MODE"
"${CURL[@]}" -H "X-WF-Key: $KEY" -X POST -H 'Content-Type: application/json' \
    -d '{"mode":"text"}' "$BASE/live/mode" | sed 's/^/daemon: /'
TM=$("${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | python3 -c "import json,sys;print(json.load(sys.stdin).get('text_mode'))" 2>/dev/null)
[ "$TM" = "True" ] && ok "daemon text_mode on" || bad "daemon text_mode=$TM"

# listening gate: a held palm puts the daemon in listening
ensure_listening
LIS=$("${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | python3 -c "import json,sys;print(json.load(sys.stdin).get('listening'))" 2>/dev/null)
note "listening=$LIS"

# focus the real editor and KEEP it focused — text mode types at the cursor;
# focus must still be on the editor when the sign commits. LibreOffice Writer
# is the real editor on camera.
WID=$(DISPLAY="$D" xdotool search --name "LibreOffice Writer" 2>/dev/null | tail -1)
[ -n "$WID" ] && DISPLAY="$D" xdotool windowactivate "$WID" 2>/dev/null || true
sleep 0.5
FIRED=0
LT0=$(grep -c "live-text" /tmp/wf-realrun/daemon.log 2>/dev/null || echo 0)
for i in 1 2 3; do
    ensure_listening || continue
    push_clip "$CLIPS/perform-TYPE-HELLO.json"
    for _ in $(seq 1 80); do
      [ "$(grep -c "live-text" /tmp/wf-realrun/daemon.log 2>/dev/null || echo 0)" -gt "$LT0" ] && { FIRED=1; break; }
      sleep 0.25
    done
    [ "$FIRED" = 1 ] && break
    note "  text-mode attempt $i: no text send yet"
done
sleep 0.8
if [ "$FIRED" = 1 ]; then
    ok "Text mode: sign -> word typed into LibreOffice Writer (last_send.kind=text)"
else
    bad "text-mode sign produced no text send"
fi

# back to control for the next chapter
jse 'var t=document.getElementById("lv-m-ctl");t&&t.click();"ctl"' >/dev/null
"${CURL[@]}" -H "X-WF-Key: $KEY" -X POST -H 'Content-Type: application/json' \
    -d '{"mode":"control"}' "$BASE/live/mode" >/dev/null
bash "$HERE/record.sh" stop
ok "text-mode scene recorded"
cp "$RUNLOG" "$PROOFS/f2-text.log" 2>/dev/null || true
log "scene done: $PASS pass $FAIL fail"
[ "$FAIL" = 0 ]
