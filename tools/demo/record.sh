#!/usr/bin/env bash
# record.sh — capture the private :97 display into proofs/demo/*.mp4.
#
#   record.sh <name> <secs>   one-shot: record <secs> seconds, clean stop
#   record.sh start <name>    begin a background recording (for run_all)
#   record.sh stop            SIGINT ffmpeg so the mp4 moov atom lands
#
# ffmpeg -f x11grab -> libx264 crf 22 yuv420p. The display is Xvfb :97 —
# the same desktop chrome + the evidence terminal live on.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$HERE/lib.sh"; load_env
mkdir -p "$PROOFS"

PIDF="$DEMO_STATE/rec.pid"

case "${1:-}" in
    start)
        NAME="${2:?record.sh start <name>}"
        OUT="$PROOFS/$NAME.mp4"
        rec_start "$OUT"
        echo "$REC_PID" > "$PIDF"
        echo "$OUT" > "$DEMO_STATE/rec.out"
        ;;
    stop)
        [ -f "$PIDF" ] || die "no recording running ($PIDF missing)"
        REC_PID=$(cat "$PIDF"); rm -f "$PIDF"
        OUT=$(cat "$DEMO_STATE/rec.out" 2>/dev/null)
        rec_stop
        ;;
    "")
        echo "usage: record.sh <name> <secs> | start <name> | stop" >&2
        exit 2
        ;;
    *)
        NAME="$1"; SECS="${2:?record.sh <name> <secs>}"
        OUT="$PROOFS/$NAME.mp4"
        rec_start "$OUT"
        sleep "$SECS"
        rec_stop
        ;;
esac
