#!/usr/bin/env bash
# type_into_sink.sh TEXT — bound argv for the TYPE-HELLO demo sign.
#
# xdotool-types TEXT into the realrun SinkEdit window (WM_CLASS SinkEdit,
# a real Tk text app whose buffer mirrors to $REALRUN_STATE/sink.txt on
# every KeyRelease). Uses --window so the editor gets the keys without a
# focus steal mid-scene; falls back to activate+type if it refuses.
#
# Bound via PUT /bank argv — spawned by the daemon with its own env
# (DISPLAY=:97 + realrun PATH already set), no shell beyond this script.
set -u
TEXT="${1:-hello-from-wispr-wrap}"
WID=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
    WID=$(xdotool search --class SinkEdit 2>/dev/null | tail -1)
    [ -n "$WID" ] && break
    sleep 0.2
done
[ -n "$WID" ] || { echo "type_into_sink: no SinkEdit window" >&2; exit 1; }
if ! xdotool type --window "$WID" --clearmodifiers --delay 12 -- "$TEXT" 2>/dev/null; then
    xdotool windowactivate --sync "$WID" 2>/dev/null || true
    xdotool type --clearmodifiers --delay 12 -- "$TEXT"
fi
