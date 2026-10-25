#!/usr/bin/env bash
# verify_observe — rehearsal/observe mode: matches reported but NO effect.
# signflow_ops (the session layer with set_observe) is not wired to any
# HTTP route in wfd.py — we probe the plausible contract paths, report
# the 404s honestly, and confirm no side effect ever happened.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/observe"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

{
    echo "observe-mode route probes (X-WF-Key authed):"
    for p in /ops /ops/state /ops/observe /ops/stats /live/observe /observe; do
        echo "  GET  $p -> $(call GET "$p")"
    done
    for p in /ops/observe /live/observe /observe; do
        echo "  POST $p -> $(call POST "$p" '{"on":true}')"
    done
    echo
    echo "side-effect check: markers dir during probes"
    ls -la "$MARKERS" | grep -v '^total'
} | tee "$PROOF/probe.txt"

# Any observe surface that answered 200 gets the real test: enable it,
# fire a bound sign, require NO marker. Today nothing answers.
if grep -qE 'GET  /(ops|observe|live/observe)[a-z/]* -> 200' "$PROOF/probe.txt" || \
   grep -qE 'POST /(ops|observe|live/observe)[a-z/]* -> 200' "$PROOF/probe.txt"; then
    verdict UNVERIFIABLE "an observe route answered 200 — needs a dedicated check (see probe.txt)"
else
    verdict PENDING "no observe/rehearsal route in daemon (all 404 — signflow_ops.observe unwired); no side effect observed"
fi
