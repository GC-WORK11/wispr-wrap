#!/usr/bin/env bash
# run_all.sh — the realrun proof-gate master: boot the stack, verify every
# feature end to end on the real display + real daemon, print the table.
#
#   tools/realrun/run_all.sh [--teardown] [--no-boot]
#
#   --teardown  kill the stack when done (default: leave up for reruns)
#   --no-boot   skip setup (stack already up via setup.sh)
#
# Verdict semantics (RESULT file per feature, one per proofs/realrun/<f>):
#   VERIFIED      real run + real OS effect observed + proof artifacts
#   PENDING       the feature's route/endpoint isn't in the daemon yet
#                 (N1 mid-flight) — nothing asserted, nothing faked
#   NEVER-ON-BOX  the box lacks what the feature needs (documented probe)
#   UNVERIFIABLE  ran but the box can't produce the observation
#   FAILED        real run, real brokenness — the worst verdict, kept loud
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"

TEARDOWN=0; BOOT=1
for a in "$@"; do
    case "$a" in
        --teardown) TEARDOWN=1 ;;
        --no-boot) BOOT=0 ;;
    esac
done

[ "$BOOT" -eq 1 ] && bash "$HERE/setup.sh"
load_env

VERIFIERS=(
    pair_send live_sign open_app focus_app close_window tile workspace
    volume screenshot macro context observe record confirm auth
)

declare -A STATUS WHY
failures=0
for v in "${VERIFIERS[@]}"; do
    log "=== $v ==="
    bash "$HERE/verify_$v.sh" >/dev/null 2>&1
    rc=$?
    if [ -f "$REALRUN_PROOFS/$v/RESULT" ]; then
        STATUS[$v]=$(cut -f1 "$REALRUN_PROOFS/$v/RESULT")
        WHY[$v]=$(cut -f2- "$REALRUN_PROOFS/$v/RESULT")
    else
        STATUS[$v]=FAILED
        WHY[$v]="verifier exited rc=$rc without a RESULT"
    fi
    [ "${STATUS[$v]}" = FAILED ] && failures=$((failures + 1))
done

# ---------- table ----------------------------------------------------------
{
    echo
    echo "================ realrun proof-gate table ================"
    printf "  %-14s %-13s %s\n" "FEATURE" "VERDICT" "WHY"
    for v in "${VERIFIERS[@]}"; do
        printf "  %-14s %-13s %s\n" "$v" "${STATUS[$v]}" "${WHY[$v]:0:100}"
    done
    echo "=========================================================="
    echo "  proofs: $REALRUN_PROOFS  (artifacts are local-only; PROOFS.md in repo)"
} | tee "$REALRUN_PROOFS/TABLE.txt"

# ---------- regenerate PROOFS.md status column -----------------------------
python3 - "$REALRUN_PROOFS" "$HERE/PROOFS.md" <<'PY' 2>/dev/null || true
import os, re, sys
proofs, pmd = sys.argv[1], sys.argv[2]
if not os.path.isfile(pmd):
    sys.exit(0)
status = {}
for v in os.listdir(proofs):
    r = os.path.join(proofs, v, "RESULT")
    if os.path.isfile(r):
        parts = open(r).read().strip().split("\t", 1)
        status[v] = parts[0]
doc = open(pmd).read()
for name, st in status.items():
        row = name.replace("_", "\\_")
        doc = re.sub(r"(\|\s*`?%s`?\s*\|[^|]*\|[^|]*\|[^|]*\|)\s*[A-Z\-]+\s*(\|)"
                     % row, r"\1 %s \2" % st, doc)
open(pmd, "w").write(doc)
PY

[ "$TEARDOWN" -eq 1 ] && bash "$HERE/setup.sh" --teardown
exit "$failures"
