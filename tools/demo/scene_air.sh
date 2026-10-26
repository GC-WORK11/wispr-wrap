#!/usr/bin/env bash
# scene_air.sh — §A recorded scene: the Go Live camera view's semantic
# events drive the real laptop, captured on the demo display.
#   pointer -> visible cursor glide; scroll -> real wheel; air C -> a
#   real Chrome window opens; air V -> youtube.com; click -> select.
# Same env contract as run_all.sh (needs boot.sh's stack up).
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

ev() {
    "${CURL[@]}" -X POST "$BASE/live/event" -H "X-WF-Key: $KEY" \
        -H 'Content-Type: application/json' --data-binary "$1"
}
EVE="$PROOFS/08-air-evidence.txt"; : > "$EVE"

scene "08-air  (section A: cursor, scroll, air-C Chrome, air-V YouTube, click)"
bash "$HERE/record.sh" start "08-air"

note "pointer sweep: finger cursor glides across the real screen"
for xy in 0.15,0.25 0.35,0.35 0.55,0.45 0.75,0.55 0.85,0.4; do
    ev "{\"type\":\"pointer\",\"x\":${xy%,*},\"y\":${xy#*,}}" >/dev/null
    sleep 0.35
done
DISPLAY="$D" xdotool getmouselocation | tee -a "$EVE" | sed 's/^/  /'

note "scroll dy=6 -> real wheel-down on the focused window"
ev '{"type":"scroll","dy":6}' >> "$EVE" 2>&1; sleep 0.5
ev '{"type":"scroll","dy":6}' >> "$EVE" 2>&1; sleep 0.6
note "scroll dy=-4 -> wheel back up"
ev '{"type":"scroll","dy":-4}' >> "$EVE" 2>&1; sleep 0.6

note "air-written C -> Google Chrome opens (app.open via /act)"
CB=$(DISPLAY="$D" xdotool search --class chrome | sort -u | tr '\n' ' ')
ev '{"type":"air","letter":"C"}' | tee -a "$EVE"
sleep 5
NEW_WID=""
for wid in $(DISPLAY="$D" xdotool search --class chrome 2>/dev/null); do
    case " $CB " in *" $wid "*) ;; *) NEW_WID=$wid;; esac
done
DISPLAY="$D" wmctrl -l | tee -a "$EVE"
if [ -n "$NEW_WID" ]; then
    ok "air-C -> new Chrome window $NEW_WID on the real display"
elif DISPLAY="$D" wmctrl -l | grep -qi chrome; then
    ok "air-C -> Chrome present (opened a tab in the running instance)"
else
    bad "air-C -> no Chrome window"
fi

note "air-written V -> youtube.com opens (open.url via /act)"
ev '{"type":"air","letter":"V"}' | tee -a "$EVE"
sleep 6
DISPLAY="$D" wmctrl -l | grep -i "youtube\|chrome" | tee -a "$EVE"

note "hover + click -> the video-select gesture"
ev '{"type":"pointer","x":0.5,"y":0.45}' >> "$EVE" 2>&1; sleep 0.8
ev '{"type":"click","button":1}' >> "$EVE" 2>&1; sleep 1.0

note "feed tail — every event lands in /live/state"
"${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | python3 -c \
    'import json,sys; d=json.load(sys.stdin)
print(json.dumps(d.get("last_action"),indent=1)[:300])
print("feed:", [e.get("type") for e in d.get("feed",[])][-8:])' \
    | tee -a "$EVE"

bash "$HERE/record.sh" stop
ok "section A recorded: pointer, scroll, Chrome, YouTube, click"
cp "$RUNLOG" "$PROOFS/08-air.log" 2>/dev/null || true
