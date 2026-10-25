#!/usr/bin/env bash
# verify_focus_app — sign -> bound wmctrl raises a different REAL window.
# Proof = _NET_ACTIVE_WINDOW / WM_NAME before vs after, both directions.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/focus_app"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

focus_term   # known starting point: editor focused
sleep 0.3
{
    echo "active window:"
    DISPLAY="$D" xdotool getactivewindow
    DISPLAY="$D" xprop -id "$(DISPLAY="$D" xdotool getactivewindow)" WM_NAME WM_CLASS
} > "$PROOF/before.txt" 2>&1

teach_bind_fire RR-FOCUS "focus terminal" "wmctrl -a $TAG-term" || {
    rc=$?; [ "$rc" = 3 ] && verdict PENDING "endpoints not wired"
    verdict FAILED "teach_bind_fire rc=$rc"; }

for i in $(seq 1 40); do
    NAME=$(DISPLAY="$D" xprop -id "$(DISPLAY="$D" xdotool getactivewindow 2>/dev/null)" WM_NAME 2>/dev/null)
    echo "$NAME" | grep -q $TAG-term && break; sleep 0.2
done
{
    echo "active window after RR-FOCUS sign:"
    DISPLAY="$D" xdotool getactivewindow
    DISPLAY="$D" xprop -id "$(DISPLAY="$D" xdotool getactivewindow)" WM_NAME WM_CLASS
} > "$PROOF/after.txt" 2>&1
shot "$PROOF/after.png"
diff -u "$PROOF/before.txt" "$PROOF/after.txt" > "$PROOF/diff.txt" || true

if grep -q $TAG-term "$PROOF/after.txt"; then
    verdict VERIFIED "sign moved focus to $TAG-term (active window + WM_NAME diff)"
else
    verdict FAILED "focus did not move (after: $(cat "$PROOF/after.txt" | tr '\n' ' '))"
fi
