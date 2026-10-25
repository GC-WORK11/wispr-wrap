#!/usr/bin/env bash
# evidence_terminal.sh — runs INSIDE the xfce4-terminal on :97 during the
# recorded demo: shows the live evidence stream (the curl calls and JSON
# replies run_all is making against the real daemon :8874) plus a ticker
# line of OS-side effects (marker files, sink text) as they land.
set -u
LOG="${1:-/tmp/wf-demo/run.log}"
MARKERS="${2:-/tmp/wf-realrun/markers}"
SINK="${3:-/tmp/wf-realrun/sink.txt}"
clear
printf '=== wispr wrap demo — live evidence (REPLAYED keypoints) ===\n'
printf 'composer page streams replayed kp42 -> POST /live/kp on :8874\n'
printf 'daemon :8874 · replay :8877 · markers %s\n\n' "$MARKERS"
# tail the run log (the actual commands+replies) and print OS effects
# as they appear — all in one pane for the recording.
(
    declare -A seen
    while :; do
        for f in "$MARKERS"/*; do
            [ -e "$f" ] || continue
            if [ -z "${seen[$f]:-}" ]; then
                seen[$f]=1
                printf '\a>>> OS EFFECT: marker file appeared: %s\n' "$f"
            fi
        done
        sleep 0.4
    done
) &
(
    last=""
    while :; do
        cur=$(tail -c 200 "$SINK" 2>/dev/null | tr '\n' '|')
        if [ "$cur" != "$last" ] && [ -n "$cur" ]; then
            printf '\a>>> OS EFFECT: sink editor text = %s\n' "$cur"
            last="$cur"
        fi
        sleep 0.6
    done
) &
tail -n +1 -F "$LOG"
