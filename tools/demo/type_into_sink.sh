#!/usr/bin/env bash
# type_into_sink.sh TEXT — bound argv for the TYPE-HELLO demo sign.
#
# xdotool-types TEXT into the realrun SinkEdit window (WM_CLASS SinkEdit,
# a real Tk text app whose buffer mirrors to $REALRUN_STATE/sink.txt on
# every KeyRelease). Effect-verified: we check the mirror file actually
# grew — `xdotool type --window` returns 0 even when the freshly-restarted
# Tk window silently drops XSendEvent keystrokes, so on no-op we focus the
# editor and retype (real keys, real X events).
#
# Bound via PUT /bank argv — spawned by the daemon with its own env
# (DISPLAY=:97 + realrun PATH already set), no shell beyond this script.
set -u
TEXT="${1:-hello-from-wispr-wrap}"
# daemon-spawned env carries HOME/DISPLAY but not SINK — read the realrun
# env file so we verify against the same mirror the editor writes to.
SINK="${WF_SINK:-${SINK:-}}"
if [ -z "$SINK" ] && [ -f /tmp/wf-realrun/env ]; then
    SINK=$(. /tmp/wf-realrun/env 2>/dev/null; printf '%s' "$SINK")
fi
[ -n "$SINK" ] || SINK=/tmp/wf-realrun/sink.txt

# Pin the target display: the daemon env already carries it, but a stray
# inherited DISPLAY from the caller's shell must never steer keystrokes
# to the wrong screen. The realrun env file holds the owned display.
D="${WF_DISPLAY:-${DISPLAY:-}}"
if [ -f /tmp/wf-realrun/env ]; then
    _D=$(. /tmp/wf-realrun/env 2>/dev/null; printf '%s' "$D")
    [ -n "$_D" ] && D="$_D"
fi
[ -n "$D" ] || { echo "type_into_sink: no target display" >&2; exit 1; }
export DISPLAY="$D"

WID=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
    WID=$(xdotool search --class SinkEdit 2>/dev/null | tail -1)
    [ -n "$WID" ] && break
    sleep 0.2
done
[ -n "$WID" ] || { echo "type_into_sink: no SinkEdit window" >&2; exit 1; }

size() { stat -c %s "$SINK" 2>/dev/null || echo 0; }

before=$(size)
# 1) background delivery first — no focus steal mid-scene
xdotool type --window "$WID" --clearmodifiers --delay 12 -- "$TEXT" 2>/dev/null
sleep 0.4
if [ "$(size)" -le "$before" ]; then
    # 2) the honest path: focus the editor and type real keys
    xdotool windowactivate --sync "$WID" 2>/dev/null || true
    sleep 0.2
    xdotool type --clearmodifiers --delay 12 -- "$TEXT"
    sleep 0.4
fi
[ "$(size)" -gt "$before" ] || { echo "type_into_sink: sink unchanged" >&2; exit 1; }
