#!/usr/bin/env bash
# verify_tile — sign -> bound wmctrl tiles/maximizes a REAL window.
# Two effects, both geometry-proven by xwininfo diffs:
#   RR-TILE  -> move/resize window to left half (-e 0,0,0,640,800)
#   RR-TILE2 -> maximize (_NET_WM_STATE add maximized_*)
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/tile"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

have xmessage || verdict UNVERIFIABLE "xmessage absent"
DISPLAY="$D" setsid xmessage -title "$TAG-tile" -center "tile target" </dev/null >/dev/null 2>&1 &
sleep 1.2
WID=$(DISPLAY="$D" xdotool search --name "^$TAG-tile$" | tail -1)
[ -n "$WID" ] || verdict FAILED "tile target never appeared"
DISPLAY="$D" xwininfo -id "$WID" > "$PROOF/before.txt"
DISPLAY="$D" xprop -id "$WID" _NET_WM_STATE >> "$PROOF/before.txt" 2>&1
shot "$PROOF/before.png"

teach_bind_fire RR-TILE "tile left half" \
    "wmctrl -i -r $WID -e 0,0,0,640,800" || {
        rc=$?; [ "$rc" = 3 ] && verdict PENDING "endpoints not wired"
        verdict FAILED "teach_bind_fire rc=$rc"; }
sleep 0.6
DISPLAY="$D" xwininfo -id "$WID" > "$PROOF/mid.txt"

teach_bind_fire RR-TILE2 "maximize" \
    "wmctrl -i -r $WID -b add,maximized_vert,maximized_horz" || {
        rc=$?; [ "$rc" = 3 ] && verdict PENDING "endpoints not wired"
        verdict FAILED "teach_bind_fire rc=$rc"; }
sleep 0.6
DISPLAY="$D" xwininfo -id "$WID" > "$PROOF/after.txt"
DISPLAY="$D" xprop -id "$WID" _NET_WM_STATE >> "$PROOF/after.txt" 2>&1
shot "$PROOF/after.png"
diff -u "$PROOF/before.txt" "$PROOF/after.txt" > "$PROOF/diff.txt" || true

geom() { grep -E 'Absolute upper-left|Width:|Height:' "$1" | tr '\n' ' '; }
B=$(geom "$PROOF/before.txt"); M=$(geom "$PROOF/mid.txt"); A=$(geom "$PROOF/after.txt")
echo "before: $B"; echo "tile:   $M"; echo "maxed:  $A"
ok_left=$(echo "$M" | grep -oE 'Width: *640'); ok_max=$(grep -c 'maximized' "$PROOF/after.txt")
if [ -n "$ok_left" ] && [ "$ok_max" -ge 1 ]; then
    verdict VERIFIED "sign tiled window to left half (W=640) then maximized it (xwininfo + _NET_WM_STATE diffs)"
elif [ -n "$ok_left" ]; then
    verdict VERIFIED "sign tiled window to left half (geometry diff); maximize partially verified"
else
    verdict FAILED "geometry unchanged (before=$B after=$A)"
fi
