#!/usr/bin/env bash
# scene_composer.sh — recorded scene: the composer + laptop send path through
# the REAL UI. CDP drives the composer's own controls (box -> go.click()) so
# compose.js -> POST /send -> xdotool types into the focused sink editor —
# the same path a finger on the phone takes. Drawer + Teach sheet shown.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"
load_env
PASS=0; FAIL=0
RUNLOG="$DEMO_STATE/composer.log"; : > "$RUNLOG"
exec > >(tee -a "$RUNLOG") 2>&1
log()  { printf '[demo %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
note() { log "      $1"; }
ok()   { PASS=$((PASS+1)); log "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); log "  FAIL  $1"; }
scene(){ log "===================== SCENE $1 ====================="; }
jse()  { python3 "$HERE/nav.py" --eval "$1"; }
SINK_WID() { DISPLAY="$D" xdotool search --class SinkEdit 2>/dev/null | tail -1; }
MARK="wrap-demo-$(date +%s)"

scene "00-composer  (home -> send text -> drawer -> teach)"
chrome_nav '#/'
sleep 1.5
bash "$HERE/record.sh" start "00-composer"

# --- 1. composer home is up (light shell, flower, pill) ------------------------
TITLE=$(jse 'document.title')
CLS=$(jse 'document.getElementById("composer").className')
note "composer: title='$TITLE' class='$CLS'"
[ -n "$CLS" ] && ok "composer home rendered" || bad "composer missing"

# --- 2. real send through the UI: box -> go.click() -> /send -> sink ----------
W=$(SINK_WID)
[ -n "$W" ] && DISPLAY="$D" xdotool windowactivate "$W" 2>/dev/null && sleep 0.6
BEFORE=$(wc -c < "$SINK" 2>/dev/null || echo 0)
jse '(function(){var b=document.getElementById("box");b.value="'"$MARK"'";b.dispatchEvent(new Event("input",{bubbles:true}));document.getElementById("go").click();return "clicked"})()' | sed 's/^/ui: /'
note "sent '$MARK' via the composer go button"
GREW=0
for _ in $(seq 1 40); do
    NOW=$(wc -c < "$SINK" 2>/dev/null || echo 0)
    grep -q "$MARK" "$SINK" 2>/dev/null && { GREW=1; break; }
    sleep 0.25
done
sleep 1.2   # let the sink visibly hold the text on camera
CW=$(chrome_wid); [ -n "$CW" ] && DISPLAY="$D" xdotool windowactivate "$CW" 2>/dev/null
if [ "$GREW" = 1 ]; then
    ok "composer send -> /send -> text typed into the real sink editor"
else
    bad "composer send: sink unchanged ($BEFORE -> $NOW)"
fi

# --- 3. drawer: hamburger -> laptop row + nav ---------------------------------
jse 'document.getElementById("menu").click(); "menu"' >/dev/null
sleep 1.0
OPEN=$(jse 'document.getElementById("drawer").getAttribute("aria-hidden")')
note "drawer aria-hidden=$OPEN"
[ "$OPEN" = "false" ] && ok "drawer opened" || bad "drawer did not open"
sleep 1.4   # drawer visible on camera

# --- 4. teach sheet: quick actions grid ---------------------------------------
chrome_nav '#/teach'
sleep 2.0
N_TILES=$(jse 'document.querySelectorAll("#sheet-teach button").length')
note "teach sheet buttons: $N_TILES"
[ "${N_TILES:-0}" -gt 3 ] 2>/dev/null && ok "teach sheet rendered ($N_TILES controls)" || bad "teach sheet thin"

# --- 5. back to live for the sign scenes ---------------------------------------
chrome_nav '#/live'
sleep 1.0
bash "$HERE/record.sh" stop
ok "composer scene recorded"
cp "$RUNLOG" "$PROOFS/00-composer.log" 2>/dev/null || true
log "scene done: $PASS pass $FAIL fail"
[ "$FAIL" = 0 ]
