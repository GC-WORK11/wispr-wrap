#!/bin/bash
# scene.sh — build the whole recorded scene on Xvfb :98 @ 1920x1080.
#
#   ./scene.sh          start everything (idempotent: kills a prior scene)
#   ./scene.sh --clean  tear the scene down only
#
# Layout (1920x1080):
#   +---------------------------+---------------------------------------+
#   |  phone composer (Chrome   |  wf-editor    xfce4-terminal (sink)   |
#   |  --app, 430x860)          |  wf-tabs      Chrome window, 2 tabs   |
#   |                           |  wf-log       daemon log (tail -f)    |
#   |                           |  wf-proof     watch /tmp/demo/out     |
#   +---------------------------+---------------------------------------+
#
# Everything under /tmp/demo — the demo HOME is scratch, the bank and
# commands.tsv it uses are seeded here, nothing touches ~/.
set -u
cd "$(dirname "$0")"
D=":98"
DEMO=/tmp/demo
WFD_PORT=8742
REPLAY_PORT=8877
CDP_PORT=9223
LOGS=$DEMO/logs
OUT=$DEMO/out
DEMOHOME=$DEMO/home

pids_file=$DEMO/pids

clean() {
  if [ -f "$pids_file" ]; then
    while read -r p; do kill "$p" 2>/dev/null; done < "$pids_file"
    rm -f "$pids_file"
  fi
  pkill -f "Xvfb :98" 2>/dev/null
  pkill -f "wf-port=never" 2>/dev/null
  sleep 0.4
}
[ "${1:-}" = "--clean" ] && { clean; echo "cleaned"; exit 0; }
clean
rm -rf "$DEMOHOME"
mkdir -p "$DEMOHOME/.config/wf" "$LOGS" "$OUT" "$DEMO/clips" "$DEMOHOME/Pictures"
: > "$pids_file"
echo $$ >> "$pids_file"

run() { "$@" >>"$LOGS/$(basename "$1").log" 2>&1 & echo $! >> "$pids_file"; }

# --- display + wm ----------------------------------------------------
Xvfb "$D" -screen 0 1920x1080x24 >>"$LOGS/xvfb.log" 2>&1 &
echo $! >> "$pids_file"
for i in $(seq 1 50); do xdpyinfo -display "$D" >/dev/null 2>&1 && break; sleep 0.1; done
DISPLAY=$D xfwm4 --replace >>"$LOGS/xfwm4.log" 2>&1 &
echo $! >> "$pids_file"
sleep 0.8
xsetroot -display "$D" -solid "#0e1420" 2>/dev/null || true

# --- demo-side helper scripts ----------------------------------------
cat > "$DEMO/fired.sh" <<'SH'
#!/bin/sh
printf '\n   ╔══════════════════════════════════════════════╗\n'
printf '   ║   SIGN FIRED — OPEN-TERM opened this window  ║\n'
printf '   ║   routed via commands.tsv (argv, no shell)   ║\n'
printf '   ╚══════════════════════════════════════════════╝\n\n'
exec /bin/sh -i
SH
chmod +x "$DEMO/fired.sh"

cat > "$DEMO/title.sh" <<'SH'
#!/bin/sh
printf '\n'
printf '    ╦ ╦╦╔═╗╔═╗╦═╗  ╦ ╦╦═╗╔═╗╔═╗  ═══ live e2e demo\n'
printf '    ║║║║╚═╗╠═╝╠╦╝  ║║║║╠╦╝╠═╣╠═╝  phone → laptop\n'
printf '    ╚╩╝╩╚═╝╩  ╩╚═  ╚╩╝╩╚═╩ ╩╩    sign → real OS action\n\n'
printf '    display :98 (Xvfb 1920x1080)   pose source: kp42 replay\n'
printf '    daemon  :8742                  replay :8877\n\n'
exec tail -f /dev/null
SH
chmod +x "$DEMO/title.sh"

# --- seed bank + commands (pre-paired scratch HOME) -------------------
WF_DEMO_HOME="$DEMOHOME" WF_DEMO_DIR="$DEMO" python3 seed.py \
  >>"$LOGS/seed.log" 2>&1 || { echo "seed failed"; cat "$LOGS/seed.log"; exit 1; }

# --- daemon -----------------------------------------------------------
HOME="$DEMOHOME" WF_PORT=$WFD_PORT DISPLAY=$D \
  python3 ../../server/wfd.py >>"$LOGS/wfd.log" 2>&1 &
echo $! >> "$pids_file"
for i in $(seq 1 50); do
  curl -s -o /dev/null "http://127.0.0.1:$WFD_PORT/health" && break; sleep 0.1
done

# --- replay service ---------------------------------------------------
python3 replay_server.py >>"$LOGS/replay.log" 2>&1 &
echo $! >> "$pids_file"
for i in $(seq 1 30); do
  curl -s -o /dev/null "http://127.0.0.1:$REPLAY_PORT/health" && break; sleep 0.1
done

# --- windows ----------------------------------------------------------
spawn_term() {  # title, command
  DISPLAY=$D xfce4-terminal --title "$1" --font="Monospace 11" \
    --execute "$2" >>"$LOGS/term-$1.log" 2>&1 &
  echo $! >> "$pids_file"
}
spawn_term wf-editor "python3 $PWD/sink.py"
spawn_term wf-log    "tail -f $LOGS/wfd.log"
spawn_term wf-proof  "watch -n 1 -t 'ls -la $OUT $DEMOHOME/Pictures 2>/dev/null; echo; cat $DEMO/markers.log 2>/dev/null'"
spawn_term wf-title  "$DEMO/title.sh"

# the tabbed browser for the context chapter — two real tabs
DISPLAY=$D google-chrome --user-data-dir=$DEMO/chr-tabs \
  --no-first-run --disable-features=TranslateUI \
  --no-sandbox --disable-gpu --disable-dev-shm-usage \
  --new-window about:blank \
  >>"$LOGS/chrome-tabs.log" 2>&1 &
echo $! >> "$pids_file"

# the phone composer — --app window, pre-paired key comes from the daemon
KEY=$(cat "$DEMOHOME/.config/wf/key")
sleep 1.5   # let tabs-chrome come up first
DISPLAY=$D google-chrome --user-data-dir=$DEMO/chr-app \
  --no-first-run --disable-features=TranslateUI \
  --no-sandbox --disable-gpu --disable-dev-shm-usage \
  --remote-debugging-port=$CDP_PORT \
  --app="http://127.0.0.1:$WFD_PORT/pair.html" \
  --window-size=430,860 --window-position=24,40 \
  >>"$LOGS/chrome-app.log" 2>&1 &
echo $! >> "$pids_file"

# --- layout -----------------------------------------------------------
sleep 3
export DISPLAY=$D
wmctrl -r "wf-editor" -e 0,780,30,1120,350   2>/dev/null
wmctrl -r "wf-log"    -e 0,780,700,1120,250  2>/dev/null
wmctrl -r "wf-proof"  -e 0,780,960,1120,110  2>/dev/null
wmctrl -r "wf-title"  -e 0,24,916,430,140    2>/dev/null
# second tab for the context chapter
xdotool search --onlyvisible --name "New Tab" >/dev/null 2>&1
wmctrl -r "New Tab" -e 0,780,390,1120,300    2>/dev/null
xdotool search --name "Google Chrome" | while read -r w; do
  wmctrl -i -r "$w" -e 0,780,390,1120,300 2>/dev/null
done

date +%s > "$DEMO/rec-t0"
ffmpeg -y -f x11grab -video_size 1920x1080 -framerate 30 -i "$D".0 \
  -c:v libx264 -preset veryfast -pix_fmt yuv420p "$DEMO/raw.mp4" \
  >>"$LOGS/ffmpeg.log" 2>&1 &
echo $! >> "$pids_file"

echo "SCENE READY  display=$D  daemon=:$WFD_PORT  replay=:$REPLAY_PORT  cdp=:$CDP_PORT"
echo "  key: $KEY"
echo "  recording -> $DEMO/raw.mp4"
