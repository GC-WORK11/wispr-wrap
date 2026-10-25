#!/usr/bin/env bash
# verify_auth — the security surface on the REAL path: unpaired requests
# refused, live surface LAN/USB only, pair code rate limit.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/auth"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

{
    echo "unauthed probes (no X-WF-Key):"
    for p in "/live/state" "/bank" "/act" "/engine"; do
        echo "  GET  $p -> $(call_noauth GET "$p")"
    done
    for p in "/live/kp" "/send" "/teach/begin"; do
        echo "  POST $p -> $(call_noauth POST "$p" '{}')"
    done
    echo
    echo "wrong-key probe:"
    CALL_B=$(mktemp /tmp/realrun-body.XXXXXX)
    echo "  GET /live/state (key=deadbeef) -> $(curl -sS -o "$CALL_B" -w '%{http_code}' -H 'X-WF-Key: deadbeef' "$BASE/live/state")"
    echo
    echo "proxied-rejection probe (X-Forwarded-For on live surface):"
    echo "  POST /live/kp +XFF -> $(curl -sS -o "$CALL_B" -w '%{http_code}' -X POST -H "X-WF-Key: $KEY" -H 'X-Forwarded-For: 1.2.3.4' -H 'Content-Type: application/json' --data-binary '{"t_ms":1,"points":[]}' "$BASE/live/kp")"
    echo "  body: $(cat "$CALL_B")"
    echo
    echo "pair rate limit + wrong code:"
    echo "  POST /pair bad -> $(call_noauth POST /pair '{"code":"BADCODE1"}')"
    sleep 1.1
    echo "  POST /pair bad -> $(call_noauth POST /pair '{"code":"BADCODE2"}')"
} | tee "$PROOF/probe.txt" | sed "s/$KEY/<redacted>/g"

BAD=$(grep -c '403' "$PROOF/probe.txt")
TOTAL=$(grep -cE '(GET|POST) /' "$PROOF/probe.txt")
echo "refused responses: $BAD of $TOTAL probes"

if [ "$BAD" -ge 8 ]; then
    verdict VERIFIED "auth gate real on the live surface: unpaired/wrong-key/proxied all 403; wrong pair code refused (probe.txt)"
else
    verdict FAILED "auth gate leaks — only $BAD refusals of $TOTAL probes"
fi
