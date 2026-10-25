#!/usr/bin/env bash
# verify_close_window — sign -> bound wmctrl closes a REAL window.
# A fresh xmessage target is spawned for the proof; after the sign the
# window must be gone from the client list (and the process reaped).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/close_window"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

have xmessage || verdict UNVERIFIABLE "xmessage absent"
DISPLAY="$D" setsid xmessage -center "realrun-close-me" </dev/null >/dev/null 2>&1 &
sleep 1.2
DISPLAY="$D" wmctrl -l > "$PROOF/before.txt"
grep -q realrun-close-me "$PROOF/before.txt" || verdict FAILED "target window never appeared"

teach_bind_fire RR-CLOSE "close window" "wmctrl -c realrun-close-me" || {
    rc=$?; [ "$rc" = 3 ] && verdict PENDING "endpoints not wired"
    verdict FAILED "teach_bind_fire rc=$rc"; }

for i in $(seq 1 40); do
    DISPLAY="$D" wmctrl -l | grep -q realrun-close-me || break; sleep 0.2
done
DISPLAY="$D" wmctrl -l > "$PROOF/after.txt"
shot "$PROOF/after.png"
diff -u "$PROOF/before.txt" "$PROOF/after.txt" > "$PROOF/diff.txt" || true

if ! grep -q realrun-close-me "$PROOF/after.txt"; then
    verdict VERIFIED "sign closed the real window (gone from _NET_CLIENT_LIST)"
else
    verdict FAILED "window still present after sign"
fi
