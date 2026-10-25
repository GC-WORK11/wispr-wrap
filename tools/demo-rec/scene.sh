#!/bin/bash
# scene.sh — build the whole recorded scene on Xvfb :110 @ 1920x1080.
#
#   ./scene.sh          start everything (idempotent: kills a prior scene)
#   ./scene.sh --clean  tear the scene down only
#
# Display choice is deliberate: sibling agents on this box scan :97-:105
# for their own Xvfb (tools/realrun/setup.sh) — :110 is outside every
# scan range so nobody here takes over each other's display.
#
# Layout (1920x1080):
#   +---------------------------+---------------------------------------+
#   |  phone composer (Chrome   |  wf-editor    xfce4-terminal (sink)   |
#   |  --app, 430x860)          |  wf-tabs      Chrome window, 2 tabs   |
#   |                           |  wf-log       daemon log (tail -f)    |
#   |  wf-title (banner)        |  wf-proof     watch out/uploads/…     |
#   +---------------------------+---------------------------------------+
#
# Everything under /tmp/demo — the demo HOME is scratch, the bank and
# commands.tsv it uses are seeded here, nothing touches ~/.
set -u
cd "$(dirname "$0")"
DEMO=/tmp/demo
WFD_PORT=8742
REPLAY_PORT=8877                 # hardcoded in web/js/pose.js — must be ours
LOGS=$DEMO/logs
OUT=$DEMO/out
DEMOHOME=$DEMO/home

# --- free display + CDP port --------------------------------------------
# Sibling agents on this box scan :97-:105 for their own Xvfb and have
# grabbed :93/:110/:111/:147 ad hoc — hardcoding a display collided.
# We probe a wide range, take the first truly free one, and start Xvfb
# immediately: the X lock + a live xdpyinfo are how every lane here
# knows a display is owned.
pick_display() {
  local d
  for d in 116 118 120 122 124 126 128 130 132 134 136 138 140 142 144 \
           146 148 150 152 154 156 158 160 165 170 175 180 185 190 195; do
    [ -e "/tmp/.X${d}-lock" ] && continue
    DISPLAY=":$d" xdpyinfo >/dev/null 2>&1 && continue
    echo ":$d"; return 0
  done
  return 1
}
pick_port() {  # first unbound port from the list
  local p
  for p in "$@"; do
    ss -tln "sport = :$p" 2>/dev/null | grep -q LISTEN || { echo "$p"; return 0; }
  done
  return 1
}

clean() {
  D=$(cat "$DEMO/display" 2>/dev/null || true)
  if [ -f "$pids_file" ]; then
    while read -r p; do kill "$p" 2>/dev/null; done < "$pids_file"
    rm -f "$pids_file"
  fi
  # scoped kills only: OUR recorded display and OUR /tmp/demo paths —
  # never pkill patterns a sibling could match.
  [ -n "${D:-}" ] && pkill -f "Xvfb $D " 2>/dev/null
  pkill -f "user-data-dir=$DEMO/chr" 2>/dev/null
  pkill -f "$DEMO/run-sink.sh" 2>/dev/null
  sleep 0.4
}
pids_file=$DEMO/pids
[ "${1:-}" = "--clean" ] && { clean; echo "cleaned"; exit 0; }
clean
D=$(pick_display) || { echo "no free display in probe range"; exit 1; }
CDP_PORT=$(pick_port 9224 9225 9226 9227 9228 9229 9230 9234 9238 9242) \
  || { echo "no free CDP port"; exit 1; }
echo "$D" > "$DEMO/display"
mkdir -p "$LOGS" "$OUT"
cat > "$DEMO/env" <<EOF
DEMO_DISPLAY=$D
WFD_PORT=$WFD_PORT
REPLAY_PORT=$REPLAY_PORT
CDP_PORT=$CDP_PORT
EOF

rm -rf "$DEMOHOME" "$DEMO/chr-app" "$DEMO/chr-tabs"
mkdir -p "$DEMOHOME/.config/wf" "$LOGS" "$OUT" "$DEMO/clips" "$DEMOHOME/Pictures" "$DEMO/assets"
: > "$pids_file"
echo $$ >> "$pids_file"

run() { "$@" >>"$LOGS/$(basename "$1").log" 2>&1 & echo $! >> "$pids_file"; }

# --- port guards: refuse to record against foreign services ------------
# (siblings run their own wfd on 8874-8884 and replay-style stubs could
#  answer the page's 8877 probe — a poisoned seam would fake the demo)
port_pid() { ss -tlnp "sport = :$1" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1; }
ours() {  # pid belongs to THIS scene: cwd is this dir or cmdline mentions /tmp/demo
  local pid=$1
  [ "$(readlink "/proc/$pid/cwd" 2>/dev/null)" = "$PWD" ] && return 0
  tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -q "/tmp/demo" && return 0
  return 1
}
guard_port() {  # $1=port — bound by a leftover of ours: kill; by anyone else: abort
  local pid; pid=$(port_pid "$1")
  [ -z "$pid" ] && return 0
  if ours "$pid"; then
    kill "$pid" 2>/dev/null; sleep 0.3; return 0
  fi
  local cmd; cmd=$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null)
  echo "FATAL: :$1 is owned by a foreign process ($pid: ${cmd:0:90})" >&2
  echo "       refusing to record against someone else's service" >&2
  exit 1
}
guard_port $WFD_PORT
guard_port $REPLAY_PORT   # the page hardcodes :8877 — if a foreign
                          # service owns it the seam would feed garbage

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

cat > "$DEMO/title.sh" <<SH
#!/bin/sh
printf '\n'
printf '    ╦ ╦╦╔═╗╔═╗╦═╗  ╦ ╦╦═╗╔═╗╔═╗  ═══ live e2e demo\n'
printf '    ║║║║╚═╗╠═╝╠╦╝  ║║║║╠╦╝╠═╣╠═╝  phone → laptop\n'
printf '    ╚╩╝╩╚═╝╩  ╩╚═  ╚╩╝╩╚═╩ ╩╩    sign → real OS action\n\n'
printf '    display $D (Xvfb 1920x1080)  pose source: kp42 replay\n'
printf '    daemon  :$WFD_PORT                  replay :$REPLAY_PORT\n\n'
exec tail -f /dev/null
SH
chmod +x "$DEMO/title.sh"

# sink wrapper: a real editor must never vanish silently — keep the
# window and show the exit status if sink.py ever dies.
cat > "$DEMO/run-sink.sh" <<SH
#!/bin/sh
export WF_SINK_LOG=$OUT/sink.log
python3 "$PWD/sink.py"
rc=\$?
echo; echo "  [sink exited rc=\$rc — log: \$WF_SINK_LOG]"
exec /bin/sh
SH
chmod +x "$DEMO/run-sink.sh"

# proof watcher: real fs + real daemon state, refreshed by watch(1).
cat > "$DEMO/proof.sh" <<SH
#!/bin/sh
clear
printf '== phone files land here (\$HOME/wf-uploads) ==\n'
ls -la "$DEMOHOME/wf-uploads" 2>/dev/null | tail -4
printf '\n== screenshots land here (\$HOME/Pictures) ==\n'
ls -la "$DEMOHOME/Pictures" 2>/dev/null | tail -4
printf '\n== editor sink log ==\n'
tail -4 "$OUT/sink.log" 2>/dev/null
printf '\n== daemon: last action + feed (GET /live/state) ==\n'
curl -s -H "X-WF-Key: \$(cat "$DEMOHOME/.config/wf/key")" \
  "http://127.0.0.1:$WFD_PORT/live/state" | \
  python3 -c 'import json,sys
try:
  s=json.load(sys.stdin)
except Exception:
  print("  <daemon down>"); raise SystemExit
la=s.get("last_action") or {}
print("  last_action:", json.dumps({k:la.get(k) for k in
      ("kind","glosses","label","ok","error","result") if la.get(k) is not None})[:240])
print("  listening:", s.get("listening"), " bank:", s.get("bank_size"),
      " focus:", s.get("focus"))
print("  feed:", " ".join((e.get("type") or "?") for e in (s.get("feed") or [])[-8:]))'
printf '\n== markers ==\n'
tail -4 "$DEMO/markers.log" 2>/dev/null
SH
chmod +x "$DEMO/proof.sh"

# two real local pages for the context chapter — distinct titles so
# ctrl+Tab is visibly provable.
cat > "$DEMO/assets/tab-a.html" <<'HTML'
<!doctype html><meta charset="utf-8"><title>demo tab A · dinner notes</title>
<style>body{font:16px/1.7 system-ui;margin:60px;color:#223}h1{font-size:34px}
p{max-width:46em}</style>
<h1>🍝 dinner notes</h1>
<p>pasta night thursday — pick up basil, tomatoes, parmesan.
this is <b>tab A</b> of a real two-tab Chrome window; the NEXT sign
switches to tab B (the window title changes when it does).</p>
HTML
cat > "$DEMO/assets/tab-b.html" <<'HTML'
<!doctype html><meta charset="utf-8"><title>demo tab B · reading list</title>
<style>body{font:16px/1.7 system-ui;margin:60px;color:#322}h1{font-size:34px}
p{max-width:46em}</style>
<h1>📚 reading list</h1>
<p>1. “Designing with the mind in mind” · 2. “Seeing Voices” ·
3. the wl-3k pose paper. this is <b>tab B</b> — you got here because the
same NEXT sign meant “next tab” inside Chrome instead of typing.</p>
HTML

# attach assets: a real file (lands in ~/wf-uploads) + a real PNG
# (clipboard-bound image — squeeze() in the page posts /send-image).
cat > "$DEMO/assets/notes.txt" <<'TXT'
standup notes — from the phone
- shipped the pairing flow
- live mode: palm to listen, namaste to stop
- open question: gesture vocab per app
TXT
python3 - <<'PY'
import zlib, struct
w, h = 360, 240
rows = []
for y in range(h):
    row = b"\x00"
    for x in range(w):
        # sunrise gradient + a sun disc + horizon — a real image
        r = int(20 + 200 * (y / h)); g = int(40 + 120 * (y / h)); b = int(90 + 60 * (1 - y / h))
        dx, dy = x - w // 2, y - int(h * 0.62)
        if dx * dx + dy * dy < 42 * 42: r, g, b = 255, 214, 90
        if y > int(h * 0.72): r, g, b = 30, 60, 90
        row += bytes((r, g, b))
    rows.append(row)
raw = b"".join(rows)
def chunk(tag, data):
    c = tag + data
    return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
png = (b"\x89PNG\r\n\x1a\n"
       + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
       + chunk(b"IDAT", zlib.compress(raw, 6))
       + chunk(b"IEND", b""))
open("/tmp/demo/assets/sunset.png", "wb").write(png)
print("wrote sunset.png", len(png), "bytes")
PY

# --- seed bank + commands (pre-paired scratch HOME) -------------------
WF_DEMO_HOME="$DEMOHOME" WF_DEMO_DIR="$DEMO" python3 seed.py \
  >>"$LOGS/seed.log" 2>&1 || { echo "seed failed"; cat "$LOGS/seed.log"; exit 1; }
python3 clips.py "$DEMO/clips" >>"$LOGS/clips.log" 2>&1 \
  || { echo "clips failed"; cat "$LOGS/clips.log"; exit 1; }

# --- daemon -----------------------------------------------------------
HOME="$DEMOHOME" WF_PORT=$WFD_PORT DISPLAY=$D \
  python3 ../../server/wfd.py >>"$LOGS/wfd.log" 2>&1 &
echo $! >> "$pids_file"
for i in $(seq 1 50); do
  curl -s -o /dev/null "http://127.0.0.1:$WFD_PORT/health" && break; sleep 0.1
done
curl -s -o /dev/null "http://127.0.0.1:$WFD_PORT/health" \
  || { echo "daemon never came up"; tail -20 "$LOGS/wfd.log"; exit 1; }

# --- replay service ---------------------------------------------------
python3 replay_server.py >>"$LOGS/replay.log" 2>&1 &
echo $! >> "$pids_file"
for i in $(seq 1 30); do
  curl -s -o /dev/null "http://127.0.0.1:$REPLAY_PORT/health" && break; sleep 0.1
done
# the page probes exactly this service — prove it's OURS, not a foreign one
curl -s "http://127.0.0.1:$REPLAY_PORT/health" | grep -q '"queued"' \
  || { echo "replay on :$REPLAY_PORT is not this service — aborting"; exit 1; }

# --- windows ----------------------------------------------------------
spawn_term() {  # title, argv... (--execute takes separate args, not one string)
  local t=$1; shift
  DISPLAY=$D xfce4-terminal --title "$t" --font="Monospace 11" \
    --execute "$@" >>"$LOGS/term-$t.log" 2>&1 &
  echo $! >> "$pids_file"
}
spawn_term wf-editor sh "$DEMO/run-sink.sh"
spawn_term wf-log    tail -f "$LOGS/wfd.log"
spawn_term wf-proof  watch -n 1 -t "$DEMO/proof.sh"
spawn_term wf-title  sh "$DEMO/title.sh"

# prove the editor sink is really alive before we record — last run's
# silent sink death is exactly the class of lie this demo exists to kill.
for i in $(seq 1 40); do [ -s "$OUT/sink.log" ] && break; sleep 0.25; done
[ -s "$OUT/sink.log" ] || { echo "sink.py never wrote its log — scene broken"; exit 1; }

# the tabbed browser for the context chapter — two real tabs
DISPLAY=$D google-chrome --user-data-dir=$DEMO/chr-tabs \
  --no-first-run --disable-features=TranslateUI \
  --no-sandbox --disable-gpu --disable-dev-shm-usage \
  --new-window "file://$DEMO/assets/tab-a.html" "file://$DEMO/assets/tab-b.html" \
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
wmctrl -r "demo tab"  -e 0,780,390,1120,300  2>/dev/null

date +%s > "$DEMO/rec-t0"
ffmpeg -y -f x11grab -video_size 1920x1080 -framerate 30 -i "$D".0 \
  -c:v libx264 -preset veryfast -pix_fmt yuv420p "$DEMO/raw.mp4" \
  >>"$LOGS/ffmpeg.log" 2>&1 &
echo $! >> "$pids_file"

echo "SCENE READY  display=$D  daemon=:$WFD_PORT  replay=:$REPLAY_PORT  cdp=:$CDP_PORT"
echo "  key: $KEY"
echo "  recording -> $DEMO/raw.mp4"
