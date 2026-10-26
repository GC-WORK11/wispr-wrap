#!/usr/bin/env bash
# scene_altmax.sh — spec demo chapter: a taught sign does
# a REAL laptop window action — maximize the focused window, and alt-tab
# between windows — through the same teach -> perform -> OS-effect path.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"
load_env
PASS=0; FAIL=0
RUNLOG="$DEMO_STATE/altmax.log"; : > "$RUNLOG"
exec > >(tee -a "$RUNLOG") 2>&1
log()  { printf '[demo %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
note() { log "      $1"; }
ok()   { PASS=$((PASS+1)); log "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); log "  FAIL  $1"; }
scene(){ log "===================== SCENE $1 ====================="; }
play() { local f="$1"
         "${CURL[@]}" -X POST "${RT[@]}" "$REPLAY/clear" >/dev/null 2>&1
         sleep 0.15
         "${CURL[@]}" -X POST "${RT[@]}" "$REPLAY/clip" \
             -H 'Content-Type: application/json' \
             --data-binary "@$CLIPS/$f" >/dev/null
         wait_drain 90; }
call() { "${CURL[@]}" -X "$1" "$BASE$2" -H "X-WF-Key: $KEY" \
         -H 'Content-Type: application/json' --data-binary "$3" >/dev/null; }
EVE="$PROOFS/09-altmax-evidence.txt"; : > "$EVE"

scene "09-altmax  (a sign that does a real window action)"
chrome_nav '#/live'
sleep 1.5
bash "$HERE/record.sh" start "09-altmax"

# --- drop the glosses whose motions we're re-binding (identical training
#     data is genuinely ambiguous — the matcher would pick the old sign).
#     Also drop stale MAX-WIN/ALT-TAB so re-runs don't pile up exemplars. ---
for g in OPEN-NOTES TYPE-HELLO MAX-WIN ALT-TAB; do
    "${CURL[@]}" -X DELETE -H "X-WF-Key: $KEY" "$BASE/bank?gloss=$g" >/dev/null
done
note "removed OPEN-NOTES + TYPE-HELLO (+stale MAX-WIN/ALT-TAB)"

# --- teach MAX-WIN (reuses the OPEN-NOTES teach clip — same kp path) ---------
call POST /teach/begin '{"name":"MAX-WIN","takes":3,"label":"maximize window"}'
play teach-OPEN-NOTES.json
call POST /teach/end '{}'
# bind: maximize the ACTIVE window (real wmctrl EWMH state change)
call PUT /bank '{"gloss":"MAX-WIN","argv":["wmctrl","-r",":ACTIVE:","-b","add,maximized_vert,maximized_horz"],"tier":0,"label":"maximize window"}'
note "taught MAX-WIN -> wmctrl maximize :ACTIVE:"

# --- teach ALT-TAB -> xdotool key alt+Tab ------------------------------------
call POST /teach/begin '{"name":"ALT-TAB","takes":3,"label":"switch window"}'
play teach-TYPE-HELLO.json
call POST /teach/end '{}'
call PUT /bank '{"gloss":"ALT-TAB","argv":["xdotool","key","alt+Tab"],"tier":0,"label":"switch window"}'
note "taught ALT-TAB -> xdotool key alt+Tab"
"${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/bank" | python3 -c \
    'import json,sys; [print(" ", s["gloss"], "->", s.get("action")) for s in json.load(sys.stdin).get("signs",[])]' | tee -a "$EVE"

# --- arm listening -----------------------------------------------------------
play palm-hold.json
sleep 0.6

# --- perform MAX-WIN: the focused window really maximizes ---------------------
# Camera-visible version: maximize the small SinkEdit editor so the growth
# reads on video (the chromeless app window is already near-fullscreen).
TARGET=$(DISPLAY="$D" xdotool search --class SinkEdit 2>/dev/null | tail -1)
[ -n "$TARGET" ] && DISPLAY="$D" xdotool windowactivate "$TARGET" 2>/dev/null && sleep 0.5
[ -z "$TARGET" ] && TARGET=$(DISPLAY="$D" xdotool getactivewindow 2>/dev/null)
G0=$(DISPLAY="$D" xdotool getwindowgeometry --shell "$TARGET" 2>/dev/null)
note "target window $TARGET before: $G0" | tee -a "$EVE"
play perform-OPEN-NOTES.json
sleep 1.2
G1=$(DISPLAY="$D" xdotool getwindowgeometry --shell "$TARGET" 2>/dev/null)
ST=$(DISPLAY="$D" xprop -id "$TARGET" _NET_WM_STATE 2>/dev/null)
note "after: $G1 ; state: $ST" | tee -a "$EVE"
if echo "$ST" | grep -q "MAXIMIZED"; then
    ok "MAX-WIN -> window really maximized (EWMH state)"
else
    bad "MAX-WIN -> not maximized: $ST"
fi

# --- perform ALT-TAB: the active window really changes ------------------------
# Camera-visible: app window active -> alt+Tab -> previous window (the sink)
# comes forward; the swap reads on video.
CW=$(chrome_wid)
[ -n "$CW" ] && DISPLAY="$D" xdotool windowactivate "$CW" 2>/dev/null && sleep 0.6
A0=$(DISPLAY="$D" xdotool getactivewindow 2>/dev/null)
play perform-TYPE-HELLO.json
sleep 1.2
A1=$(DISPLAY="$D" xdotool getactivewindow 2>/dev/null)
{ echo "active before: $A0"; echo "active after:  $A1"; } | tee -a "$EVE"
if [ "$A0" != "$A1" ]; then
    ok "ALT-TAB -> active window switched ($A0 -> $A1)"
else
    bad "ALT-TAB -> active window did not change"
fi

"${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | python3 -c \
    'import json,sys; s=json.load(sys.stdin)
print("last_match:", json.dumps(s.get("last_match") or {}))' | tee -a "$EVE"
# --- leave the bank clean: our glosses collide with the demo's motions ------
for g in MAX-WIN ALT-TAB; do
    "${CURL[@]}" -X DELETE -H "X-WF-Key: $KEY" "$BASE/bank?gloss=$g" >/dev/null
done
bash "$HERE/record.sh" stop
[ "$FAIL" -eq 0 ] && ok "altmax recorded" || bad "$FAIL checks failed"
cp "$RUNLOG" "$PROOFS/09-altmax.log" 2>/dev/null || true
