#!/usr/bin/env bash
# run.sh — boot a private bench daemon + run every phase, write proofs.
#
# The shared realrun daemon (:8874) is left alone — this brings up an
# IDENTICAL wfd.py (same file, same env shape) on $BENCH_PORT with its own
# scratch HOME so a concurrent realrun session can't contaminate the
# percentiles (its posts would count in the p50/p95 too). The X display,
# sink editor and xinput shim are the shared realrun ones (:97).
#
#   bash tools/bench/run.sh            # all phases
#   bash tools/bench/run.sh --teardown # stop the bench daemon
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BENCH_STATE="${BENCH_STATE:-/tmp/wf-bench}"
BENCH_PORT="${BENCH_PORT:-8894}"
ENV=/tmp/wf-realrun/env
# shellcheck disable=SC1090
. "$ENV" 2>/dev/null || true            # D / SINK / TERM_WID / MARKERS
D="${D:-:97}"
WFHOME="$BENCH_STATE/home"
BINDIR="/tmp/wf-realrun/bin"          # the xinput shim lives here

if [ "${1:-}" = "--teardown" ]; then
    [ -f "$BENCH_STATE/pid" ] && kill "$(cat "$BENCH_STATE/pid")" 2>/dev/null
    rm -f "$BENCH_STATE/pid"
    echo "bench daemon down"
    exit 0
fi

mkdir -p "$WFHOME" "$BENCH_STATE/markers"
if [ -f "$BENCH_STATE/pid" ] && kill -0 "$(cat "$BENCH_STATE/pid")" 2>/dev/null; then
    kill "$(cat "$BENCH_STATE/pid")" 2>/dev/null; sleep 0.3
fi
env -i HOME="$WFHOME" DISPLAY="$D" PATH="$BINDIR:/usr/bin:/bin" \
    WF_PORT="$BENCH_PORT" WF_NO_LOG_TEXT=1 XDG_SESSION_TYPE=x11 \
    setsid python3 "$ROOT/server/wfd.py" > "$BENCH_STATE/wfd.log" 2>&1 &
echo $! > "$BENCH_STATE/pid"
for _ in $(seq 1 50); do
    curl -sf "http://127.0.0.1:$BENCH_PORT/health" >/dev/null 2>&1 && break
    sleep 0.2
done
curl -sf "http://127.0.0.1:$BENCH_PORT/health" >/dev/null \
    || { tail -20 "$BENCH_STATE/wfd.log" >&2; echo "bench daemon did not come up" >&2; exit 2; }
KEY="$(cat "$WFHOME/.config/wf/key")"
echo "bench daemon on :$BENCH_PORT (HOME=$WFHOME, DISPLAY=$D)"

python3 "$HERE/bench.py" --base "http://127.0.0.1:$BENCH_PORT" --key "$KEY" \
    --env "$ENV" --marker-dir "$BENCH_STATE/markers" \
    --chrome --send --kp "$@" \
    --out "$ROOT/proofs/bench-app.json"
