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
EDIT_WID() { DISPLAY="$D" xdotool search --name "LibreOffice Writer" 2>/dev/null | tail -1; }
LAST_SEND() { "${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | python3 -c "import json,sys;s=json.load(sys.stdin).get('last_send') or {};print(s.get('kind'),s.get('into'))"; }
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

# --- 2. real send through the UI: box -> go.click() -> /send -> Writer -------
W=$(EDIT_WID)
[ -n "$W" ] && DISPLAY="$D" xdotool windowactivate "$W" 2>/dev/null && sleep 0.8
# type char-by-char through the real input events so the camera sees the
# waveform pill morph into the send arrow, then tap send
jse '(function(){var b=document.getElementById("box"),s="'"$MARK"'",i=0;var iv=setInterval(function(){if(i<s.length){if(i===0)b.value="";b.value+=s[i++];b.dispatchEvent(new Event("input",{bubbles:true}));}else{clearInterval(iv);}},80);return "typing"})()' | sed 's/^/ui: /'
sleep 2.2   # chars landing + morph on camera
jse 'document.getElementById("go").click();"clicked"' | sed 's/^/ui: /'
note "sent '$MARK' via the composer go button"
SENT=0
for _ in $(seq 1 40); do
    grep -q "$MARK" $STATE/wfd.log 2>/dev/null && { SENT=1; break; }
    sleep 0.25
done
sleep 1.2   # let Writer visibly hold the text on camera
CW=$(chrome_wid); [ -n "$CW" ] && DISPLAY="$D" xdotool windowactivate "$CW" 2>/dev/null
if [ "$SENT" = 1 ]; then
    ok "composer send -> /send -> daemon typed '$MARK' into the focused editor"
else
    bad "composer send: daemon never logged the type"
fi

# --- 2b. image send: stage a real PNG via the file input -> go ---------------
W=$(EDIT_WID)
[ -n "$W" ] && DISPLAY="$D" xdotool windowactivate "$W" 2>/dev/null && sleep 0.5
# a tiny real PNG (32x32 pink square), built in-page and staged through the
# composer's own file input — the same handler a tap on the photo picker hits.
STAGED=$(jse '(function(){
  var c=document.createElement("canvas");c.width=c.height=32;
  var x=c.getContext("2d");x.fillStyle="#FF309C";x.fillRect(0,0,32,32);
  var u=c.toDataURL("image/png").split(",")[1];
  var b=Uint8Array.from(atob(u),function(ch){return ch.charCodeAt(0)});
  var f=new File([b],"photo.png",{type:"image/png"});
  var dt=new DataTransfer();dt.items.add(f);
  var inp=document.getElementById("file")||document.getElementById("fileany");
  inp.files=dt.files;inp.dispatchEvent(new Event("change",{bubbles:true}));
  var a=document.getElementById("attach");
  return a&&!a.hidden?"staged":"not-staged";})()')
note "image staged via #file input: $STAGED"
sleep 0.9
jse 'document.getElementById("go").click();"sent"' >/dev/null
IMGOK=0
for _ in $(seq 1 24); do
  DISPLAY="$D" xclip -selection clipboard -t TARGETS -o 2>/dev/null | grep -q "image/png" && { IMGOK=1; break; }
  sleep 0.25
done
[ "$IMGOK" = 1 ] && ok "image send -> clipboard holds image/png on the laptop" \
                || bad "image send: no image/png clipboard target"
sleep 1.0
CW=$(chrome_wid); [ -n "$CW" ] && DISPLAY="$D" xdotool windowactivate "$CW" 2>/dev/null

# --- 2c. file send: stage notes.txt via fileany -> go -> laptop disk ---------
W=$(EDIT_WID)
[ -n "$W" ] && DISPLAY="$D" xdotool windowactivate "$W" 2>/dev/null && sleep 0.5
UPLOADS=$WFHOME/wf-uploads
FBEFORE=$(ls "$UPLOADS" 2>/dev/null | wc -l)
jse '(function(){
  var f=new File(["hello from the phone"],"wrap-note.txt",{type:"text/plain"});
  var dt=new DataTransfer();dt.items.add(f);
  var inp=document.getElementById("fileany")||document.getElementById("file");
  inp.files=dt.files;inp.dispatchEvent(new Event("change",{bubbles:true}));
  return "staged";})()' | sed "s/^/ui: /"
sleep 0.9
jse 'document.getElementById("go").click();"sent"' >/dev/null
FILEOK=0
for _ in $(seq 1 24); do
  ls "$UPLOADS" 2>/dev/null | grep -q "wrap-note.txt" && { FILEOK=1; break; }
  sleep 0.25
done
FPATH=$(ls -t "$UPLOADS"/*wrap-note.txt 2>/dev/null | head -1)
[ "$FILEOK" = 1 ] && ok "file send -> saved to laptop: $(basename "$FPATH" 2>/dev/null)" \
                  || bad "file send: nothing in $UPLOADS"
sleep 1.0
CW=$(chrome_wid); [ -n "$CW" ] && DISPLAY="$D" xdotool windowactivate "$CW" 2>/dev/null

# --- 3. back to live (drawer + teach sheets get their own clip: scene_sheets) -
chrome_nav '#/live'
sleep 1.0
bash "$HERE/record.sh" stop
ok "composer scene recorded"
cp "$RUNLOG" "$PROOFS/00-composer.log" 2>/dev/null || true
log "scene done: $PASS pass $FAIL fail"
[ "$FAIL" = 0 ]
