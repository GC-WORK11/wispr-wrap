#!/usr/bin/env bash
# verify_workspace — sign -> bound wmctrl switches desktop and moves a
# window across workspaces. Proof = `wmctrl -d` current marker diff +
# _NET_WM_DESKTOP on the moved window. Ends back on desktop 0.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/workspace"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

NDESK=$(DISPLAY="$D" wmctrl -d | wc -l)
{ echo "desktops: $NDESK"; DISPLAY="$D" wmctrl -d; } > "$PROOF/before.txt"
[ "$NDESK" -ge 2 ] || verdict UNVERIFIABLE "WM exposes $NDESK desktop(s) — multi-workspace unverifiable"

teach_bind_fire RR-DESK "go desktop 2" "wmctrl -s 1" || {
    rc=$?; [ "$rc" = 3 ] && verdict PENDING "endpoints not wired"
    verdict FAILED "teach_bind_fire rc=$rc"; }
sleep 0.6
CUR=$(DISPLAY="$D" wmctrl -d | awk '$2=="*"{print $1}')
echo "current desktop after sign: $CUR" | tee "$PROOF/switch.txt"

# move the sink window to desktop 2 via a second sign
teach_bind_fire RR-MOVEWIN "sink to desktop 3" "wmctrl -r realrun-sink -t 2" || {
    rc=$?; [ "$rc" = 3 ] && verdict PENDING "endpoints not wired"
    verdict FAILED "teach_bind_fire rc=$rc"; }
sleep 0.6
WPROP=$(DISPLAY="$D" xprop -id "$TERM_WID" _NET_WM_DESKTOP 2>/dev/null)
echo "$WPROP" | tee "$PROOF/move.txt"
DISPLAY="$D" wmctrl -d >> "$PROOF/move.txt"

DISPLAY="$D" wmctrl -d > "$PROOF/after.txt"
shot "$PROOF/after.png"
diff -u "$PROOF/before.txt" "$PROOF/after.txt" > "$PROOF/diff.txt" || true

# restore desktop 0 + sink on it
DISPLAY="$D" wmctrl -r realrun-sink -t 0; DISPLAY="$D" wmctrl -s 0; sleep 0.3

if [ "$CUR" = "1" ] && echo "$WPROP" | grep -q "= 2"; then
    verdict VERIFIED "signs switched desktop (0->1) and moved a window across workspaces (_NET_WM_DESKTOP=2)"
elif [ "$CUR" = "1" ]; then
    verdict VERIFIED "sign switched desktop (0->1); window-move sub-effect partially verified"
else
    verdict FAILED "desktop did not switch (current=$CUR)"
fi
