#!/usr/bin/env bash
# boot.sh — boot the recorded-demo environment. Idempotent.
#
#   1. (re)boot the realrun stack (Xvfb :97 + xfwm4 + real wfd.py :8874
#      with scratch HOME + SinkEdit editor + terminals) via
#      tools/realrun/setup.sh
#   2. ensure the kp42 replay service answers :8877 (tools/demo-rec
#      replay_server.py — started nohup if down, reused if already up)
#   3. launch google-chrome on :97, windowed 1280x800, at the composer
#      "http://127.0.0.1:8874/?k=$KEY" (key read from the scratch HOME)
#   4. wait until daemon + replay + chrome window are all healthy
#
# Writes $DEMO_STATE/env (mode 600 — it references the pairing key file,
# /tmp only, NEVER committed).
#   --teardown   kill chrome + the demo-owned replay server (stack stays)
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$HERE/lib.sh"

DEMO_STATE="${DEMO_STATE:-/tmp/wf-demo}"
PROFILE="$DEMO_STATE/chrome-profile"
mkdir -p "$DEMO_STATE"

# ---------- teardown ---------------------------------------------------------
teardown() {
    for pidf in "$DEMO_STATE"/pids.*; do
        [ -f "$pidf" ] || continue
        kill "$(cat "$pidf" 2>/dev/null)" 2>/dev/null
        rm -f "$pidf"
    done
    pkill -f "user-data-dir=$PROFILE" 2>/dev/null
    rm -f "$DEMO_STATE/env"
}
teardown
[ "${1:-}" = "--teardown" ] && { log "down"; exit 0; }

for t in python3 curl Xvfb xdotool wmctrl ffmpeg google-chrome xfce4-terminal jq; do
    req "$t"
done

# ---------- 1. realrun stack --------------------------------------------------
log "boot realrun stack (:97 / :8874)"
bash "$ROOT/tools/realrun/setup.sh"
# the realrun env exports its own HERE/ROOT — re-pin ours after sourcing
_dh="$HERE"; _dr="$ROOT"
. "$REALRUN_STATE/env"
HERE="$_dh"; ROOT="$_dr"; unset _dh _dr

# ---------- 2. replay service :8877 -------------------------------------------
# The composer hardcodes http://127.0.0.1:8877. On this shared box the lane
# must be ORIGIN-PARTITIONED or several composers drain one queue (clips
# pushed for demo A land in daemon B). tools/demo/replay_split.py is a
# drop-in superset: untagged traffic keeps upstream behaviour exactly.
REPLAY_OWNER=0
SPLIT=$("${CURL[@]}" -f "$REPLAY/health" | python3 -c "import json,sys;print('1' if json.load(sys.stdin).get('split') else '0')" 2>/dev/null || echo 0)
if "${CURL[@]}" -f "$REPLAY/health" >/dev/null 2>&1 && [ "$SPLIT" = 1 ]; then
    log "replay_split already healthy on :8877 (reusing)"
else
    if [ "$SPLIT" = 0 ] && "${CURL[@]}" -f "$REPLAY/health" >/dev/null 2>&1; then
        # a non-partitioned server is up: replace it (fully compatible —
        # foreign consumers keep working on the default queue)
        pid=$(ss -tlnp 2>/dev/null | grep ':8877 ' | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
        log "replacing unpartitioned replay server (pid $pid) with replay_split.py"
        [ -n "$pid" ] && kill "$pid" 2>/dev/null && sleep 0.4
    fi
    log "starting replay_split.py on :8877"
    nohup python3 "$HERE/replay_split.py" \
        > "$DEMO_STATE/replay.log" 2>&1 &
    echo $! > "$DEMO_STATE/pids.replay"
    REPLAY_OWNER=1
    for _ in $(seq 1 40); do
        "${CURL[@]}" -f "$REPLAY/health" >/dev/null 2>&1 && break; sleep 0.2
    done
fi
"${CURL[@]}" -f "$REPLAY/health" >/dev/null 2>&1 || die "replay service did not come up on :8877"

# ---------- 3. chrome on :97 ----------------------------------------------------
rm -rf "$PROFILE"; mkdir -p "$PROFILE"
URL="http://127.0.0.1:$PORT/?k=$KEY"
log "launching chrome -> $URL"
DISPLAY="$D" setsid google-chrome \
    --user-data-dir="$PROFILE" \
    --no-sandbox --disable-dev-shm-usage \
    --no-first-run --no-default-browser-check \
    --disable-session-crashed-bubble --hide-crash-restore-bubble \
    --no-proxy-server --disable-background-networking \
    --disable-component-update --mute-audio \
    --window-size=1280,800 --window-position=0,0 \
    "$URL" </dev/null > "$DEMO_STATE/chrome.log" 2>&1 &
echo $! > "$DEMO_STATE/pids.chrome"

WID=""
for _ in $(seq 1 75); do
    WID=$(for w in $(DISPLAY="$D" xdotool search --pid "$(cat "$DEMO_STATE/pids.chrome" 2>/dev/null)" 2>/dev/null); do DISPLAY="$D" xdotool getwindowname "$w" 2>/dev/null | grep -qi chrome && { echo "$w"; break; }; done)
    [ -n "$WID" ] && break
    sleep 0.2
done
[ -n "$WID" ] || { tail -20 "$DEMO_STATE/chrome.log" >&2; die "chrome window never appeared on $D"; }

# wait for the composer page to paint (window title carries the app name)
for _ in $(seq 1 75); do
    TITLE=$(DISPLAY="$D" xdotool getwindowname "$WID" 2>/dev/null)
    case "$TITLE" in *"Wispr Wrap"*|*"Flow Composer"*) break;; esac
    sleep 0.2
done
log "chrome up: wid=$WID title='$TITLE'"

# ---------- 4. health summary + env -------------------------------------------
"${CURL[@]}" -f "$BASE/health" >/dev/null || die "daemon /health failed on $BASE"
CODE=$("${CURL[@]}" -o /dev/null -w '%{http_code}' -H "X-WF-Key: $KEY" "$BASE/live/state")
[ "$CODE" = 200 ] || die "/live/state -> $CODE (key mismatch?)"

cat > "$DEMO_STATE/env" <<EOF
D=$D
PORT=$PORT
BASE=$BASE
WFHOME=$WFHOME
SINK=$SINK
MARKERS=$MARKERS
TERM_WID=$TERM_WID
CHROME_WID=$WID
REPLAY=$REPLAY
REPLAY_OWNER=$REPLAY_OWNER
STATE=$DEMO_STATE
EOF
chmod 600 "$DEMO_STATE/env"
# NOTE: KEY stays in $REALRUN_STATE/env (mode 600) — lib.sh call() reads it
# from there; never duplicated into demo artifacts.
log "demo env ready: display $D  daemon $BASE  replay :8877 (owner=$REPLAY_OWNER)"
log "chrome window $WID at $URL"
