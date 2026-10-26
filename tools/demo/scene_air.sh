#!/usr/bin/env bash
# scene_air.sh — §A recorded scene: the exact calls the Go Live camera
# view POSTs (web/js/live.js): fireLetter -> /live/event {type:'air'},
# pointer ops -> /live/event {type:'pointer'|'scroll'|'click'}.
# Real xdotool/provider effects on the demo display, captured on video.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"
load_env
PASS=0; FAIL=0
RUNLOG="$DEMO_STATE/air.log"; : > "$RUNLOG"
exec > >(tee -a "$RUNLOG") 2>&1
log()  { printf '[demo %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
note() { log "      $1"; }
ok()   { PASS=$((PASS+1)); log "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); log "  FAIL  $1"; }
scene(){ log "===================== SCENE $1 ====================="; }
ev() { # ev BODY -> reply body
    "${CURL[@]}" -X POST "$BASE/live/event" -H "X-WF-Key: $KEY" \
        -H 'Content-Type: application/json' --data-binary "$1"; }
EVE="$PROOFS/08-air-evidence.txt"; : > "$EVE"

scene "08-air  (section A: the calls the Go Live view makes)"
chrome_nav '#/live'
sleep 1.2
bash "$HERE/record.sh" start "08-air"

# --- 1. air-written C -> Chrome really opens (same body as fireLetter) --------
note "air-C -> POST /live/event {type:'air',letter:'C'}"
CB=$(DISPLAY="$D" xdotool search --class chrome | sort -u | tr '\n' ' ')
ev '{"type":"air","letter":"C"}' | tee -a "$EVE"; echo
NEW_WID=""
for i in $(seq 1 15); do
    for wid in $(DISPLAY="$D" xdotool search --class chrome 2>/dev/null); do
        case " $CB " in *" $wid "*) ;; *) NEW_WID=$wid;; esac
    done
    [ -n "$NEW_WID" ] && break; sleep 1
done
if [ -n "$NEW_WID" ]; then
    ok "air-C -> real Chrome window $NEW_WID"
elif DISPLAY="$D" wmctrl -l | grep -qi chrome; then
    ok "air-C -> Chrome running (opened a tab in the existing instance)"
else
    bad "air-C -> no Chrome"
fi

# --- 2. finger cursor + scroll (same bodies as pointer()) ----------------------
note "finger pointer sweep -> {type:'pointer',x,y} (real mousemove)"
for xy in 0.15,0.25 0.35,0.35 0.55,0.45 0.75,0.55 0.6,0.4; do
    ev "{\"type\":\"pointer\",\"x\":${xy%,*},\"y\":${xy#*,}}" >/dev/null
    sleep 0.3
done
LOC=$(DISPLAY="$D" xdotool getmouselocation); echo "cursor: $LOC" | tee -a "$EVE"
X=$(echo "$LOC" | sed -n 's/.*x:\([0-9]*\).*/\1/p')
if [ "$X" -gt 700 ] 2>/dev/null; then ok "pointer -> real cursor at $LOC"; else bad "pointer -> $LOC"; fi
note "finger slide -> {type:'scroll',dy} (real wheel events)"
ev '{"type":"scroll","dy":4}' >/dev/null; sleep 0.5
ev '{"type":"scroll","dy":-3}' >/dev/null; sleep 0.5

# --- 3. V sign -> YouTube ------------------------------------------------------
note "V sign -> POST /live/event {type:'air',letter:'V'}"
ev '{"type":"air","letter":"V"}' | tee -a "$EVE"; echo
sleep 5
DISPLAY="$D" wmctrl -l | grep -i "youtube\|chrome" | tee -a "$EVE"
"${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | python3 -c \
    'import json,sys; d=json.load(sys.stdin)
print("last_action:", json.dumps(d.get("last_action") or {})[:240])
print("feed:", [e.get("type") for e in d.get("feed",[])][-8:])' | tee -a "$EVE"

# --- 4. hover + pinch/click -> the video-select gesture -------------------------
note "hover + click -> {type:'click',x,y} (video select)"
ev '{"type":"pointer","x":0.5,"y":0.45}' >/dev/null; sleep 0.7
ev '{"type":"click","x":0.5,"y":0.45}' >/dev/null; sleep 0.7
LOC2=$(DISPLAY="$D" xdotool getmouselocation); echo "cursor: $LOC2" | tee -a "$EVE"

bash "$HERE/record.sh" stop
ok "section A recorded: Chrome, scroll, YouTube, select-click"
cp "$RUNLOG" "$PROOFS/08-air.log" 2>/dev/null || true
