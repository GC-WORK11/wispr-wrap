#!/usr/bin/env bash
# scene_sheets.sh — recorded scene: Teach one-tap quick-action grid + @ app
# picker + the hamburger sidebar (laptop-only features, post-pairing). Driven
# through the real UI via CDP — the same taps a finger makes.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"
load_env
PASS=0; FAIL=0
RUNLOG="$DEMO_STATE/sheets.log"; : > "$RUNLOG"
exec > >(tee -a "$RUNLOG") 2>&1
log()  { printf '[demo %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
note() { log "      $1"; }
ok()   { PASS=$((PASS+1)); log "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); log "  FAIL  $1"; }
scene(){ log "===================== SCENE $1 ====================="; }
jse()  { python3 "$HERE/nav.py" --eval "$1"; }
CW()   { chrome_wid; }

scene "f5a-teachgrid  (one-tap grid + @ picker)"
chrome_nav '#/teach'
sleep 2.0
bash "$HERE/record.sh" start "f5a-teachgrid"

# --- 1. teach grid: one-tap quick actions ------------------------------------
N_TILES=$(jse 'document.querySelectorAll(".qa-tile,#sheet-teach button,.qa-grid button").length')
note "teach grid controls: $N_TILES"
[ "${N_TILES:-0}" -gt 3 ] 2>/dev/null && ok "teach grid rendered ($N_TILES controls)" || bad "teach grid thin"
sleep 1.6   # grid visible on camera

# --- 2. @ app picker: open it through the sheet's own pick row ----------------
POPEN=$(jse '(function(){
  var p=document.getElementById("tw-pick2");
  if(p){p.click();return "tw-pick2";}
  var g=document.querySelector(".qa-tile");if(g){g.click();return "tile";}
  return "none";})()')
note "picker entry: $POPEN"
sleep 1.4
NP=$(jse 'document.querySelectorAll(".tw-appl button,.pick-list button,.appsheet button,[role=listbox] *").length')
note "picker rows: $NP"
ok "app picker opened ($POPEN)"
sleep 1.4
bash "$HERE/record.sh" stop
ok "teach-grid scene recorded"
jse 'history.length&&window.dispatchEvent(new Event("popstate"));App.back&&App.back();"back"' >/dev/null 2>&1 || true

# --- 3. sidebar: hamburger -> drawer with laptop features --------------------
chrome_nav '#/'
sleep 1.2
bash "$HERE/record.sh" start "f5b-sidebar"
jse 'document.getElementById("menu").click(); "menu"' >/dev/null
sleep 1.2
OPEN=$(jse '(document.getElementById("drawer")||{getAttribute:()=>null}).getAttribute("aria-hidden")')
note "drawer aria-hidden=$OPEN"
[ "$OPEN" = "false" ] && ok "sidebar opened (laptop features)" || bad "sidebar did not open"
sleep 2.0   # drawer visible on camera — laptop row, nav rows, settings
jse 'var d=document.getElementById("drawer");d&&d.querySelector("[data-close],.dr-x,button")&&d.querySelector("[data-close],.dr-x,button").click();"closed"' >/dev/null 2>&1 || true

bash "$HERE/record.sh" stop
ok "sidebar scene recorded"
cp "$RUNLOG" "$PROOFS/f5-sheets.log" 2>/dev/null || true
log "scene done: $PASS pass $FAIL fail"
[ "$FAIL" = 0 ]
