#!/usr/bin/env bash
# verify_volume — volume/mute through a REAL audio stack.
# The mission allows a pulseaudio null-sink; this box has no pulseaudio,
# no pactl, no amixer -> NEVER-ON-BOX with probe evidence, never faked.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/volume"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

{
    echo "probes:"
    for t in pulseaudio pactl amixer; do
        echo "  $t: $(command -v "$t" 2>/dev/null || echo MISSING)"
    done
    echo
    if have pulseaudio; then
        echo "attempting: pulseaudio --daemonize -L module-null-sink"
        pulseaudio --daemonize -L module-null-sink 2>&1 && \
            { echo "daemonize OK"; pactl info 2>&1 | head -10; } || \
            echo "daemonize FAILED rc=$?"
    else
        echo "pulseaudio binary absent — cannot build a null-sink"
    fi
    echo
    echo "contract probe: PUT /bank volume argv + sign is the only volume path"
    echo "(daemon has no /act/sys volume route today)"
    echo "GET /act status: $(call GET /act)"
} | tee "$PROOF/probe.txt"
snapshot > "$PROOF/before.txt" 2>/dev/null
cp "$PROOF/before.txt" "$PROOF/after.txt"; : > "$PROOF/diff.txt"

if have pactl || have amixer; then
    verdict UNVERIFIABLE "audio backend exists but untested — see probe.txt"
else
    verdict NEVER-ON-BOX "no pulseaudio/pactl/amixer on this box — volume/mute impossible to exercise"
fi
