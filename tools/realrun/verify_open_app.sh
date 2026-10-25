#!/usr/bin/env bash
# verify_open_app — sign -> bound argv launches a REAL app window.
# Bound argv: xfce4-terminal (a real .desktop app — the same spawn path
# any user binding gets: direct argv, no shell). Proof = wmctrl window
# list before/after + xprop WM_CLASS of the new window + screenshot.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/open_app"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

DISPLAY="$D" wmctrl -l > "$PROOF/before.txt"
have xfce4-terminal || verdict UNVERIFIABLE "xfce4-terminal absent on box"

teach_bind_fire RR-OPEN "open terminal" \
    "xfce4-terminal --title $TAG-opened" || {
        rc=$?; [ "$rc" = 3 ] && verdict PENDING "teach/bind endpoints not wired"
        verdict FAILED "teach_bind_fire rc=$rc"; }

for i in $(seq 1 40); do
    DISPLAY="$D" wmctrl -l | grep -q $TAG-opened && break; sleep 0.25
done
DISPLAY="$D" wmctrl -l > "$PROOF/after.txt"
shot "$PROOF/after.png"
diff -u "$PROOF/before.txt" "$PROOF/after.txt" > "$PROOF/diff.txt" || true
WID=$(DISPLAY="$D" xdotool search --name "^$TAG-opened$" | tail -1)
[ -n "$WID" ] && DISPLAY="$D" xprop -id "$WID" WM_CLASS WM_NAME > "$PROOF/window.txt"

if [ -n "$WID" ]; then
    verdict VERIFIED "sign spawned a real app window: $(cat "$PROOF/window.txt" | tr '\n' ' ')"
else
    cat "$PROOF/after.txt"
    verdict FAILED "no new window after sign"
fi
