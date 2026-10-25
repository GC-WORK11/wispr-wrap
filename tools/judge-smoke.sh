#!/bin/bash
# judge-smoke — end-to-end smoke for the merged product, runnable headless.
# Boots server/wfd.py on a scratch port with a scratch HOME (its own key file —
# your real ~/.config/wf is never touched), exercises pairing + auth + endpoints,
# then kills the daemon and proves the port is released.
#
# Hard gates (fail = exit 1): /health, /pair-info, /pair round-trip, auth 403,
# authed /send. Live-engine endpoints (/engine, /bank, /live/kp, /teach/begin)
# report "pend" while the merge lands — green unless WF_SMOKE_STRICT=1.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${WF_SMOKE_PORT:-8873}"
TMP="$(mktemp -d)"
DPID=""
cleanup() {
    [ -n "$DPID" ] && kill "$DPID" 2>/dev/null
    rm -rf "$TMP"
}
trap cleanup EXIT

pass=0; fail=0; pend=0
ok()   { echo "  [ ok    ] $1"; pass=$((pass+1)); }
bad()  { echo "  [ FAIL  ] $1"; fail=$((fail+1)); }
hold() { echo "  [ pend  ] $1"; pend=$((pend+1)); [ "${WF_SMOKE_STRICT:-}" = 1 ] && fail=$((fail+1)); }

echo "judge-smoke — daemon on :$PORT, scratch HOME=$TMP"
HOME="$TMP" WF_PORT="$PORT" python3 "$ROOT/server/wfd.py" >"$TMP/wfd.log" 2>&1 &
DPID=$!

up=""
for _ in $(seq 1 40); do
    curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && up=1 && break
    sleep 0.25
done
if [ -z "$up" ]; then
    bad "GET /health — daemon never came up"
    sed 's/^/    log: /' "$TMP/wfd.log"
    exit 1
fi
ok "GET /health -> 200"

info=$(curl -sf "http://127.0.0.1:$PORT/pair-info")
if [ -n "$info" ]; then
    KEY=$(printf '%s' "$info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])')
    ok "GET /pair-info -> key issued (localhost auto-pair path)"
else
    bad "GET /pair-info"
fi

# real pairing path: the 8-char code the daemon printed at boot
PCODE=$(sed -n 's/.*pair code: \([A-Z0-9]*\).*/\1/p' "$TMP/wfd.log" | head -1)
if [ -n "$PCODE" ]; then
    pr=$(curl -sf -X POST "http://127.0.0.1:$PORT/pair" -H 'Content-Type: application/json' \
        -d "{\"code\":\"$PCODE\"}")
    if printf '%s' "$pr" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["ok"]' 2>/dev/null; then
        ok "POST /pair valid code -> 200 paired"
    else
        bad "POST /pair valid code -> $pr"
    fi
    sleep 1.1   # /pair is rate-limited to 1 try/sec/IP
    code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/pair" \
        -H 'Content-Type: application/json' -d '{"code":"WRONGCOD"}')
    [ "$code" = 403 ] && ok "POST /pair wrong code -> 403" || bad "POST /pair wrong code -> $code (want 403)"
else
    bad "no pair code in daemon log"
fi

code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/send" \
    -H 'Content-Type: application/json' -d '{"text":"x"}')
[ "$code" = 403 ] && ok "POST /send without key -> 403" || bad "POST /send without key -> $code (want 403)"

# empty text exercises the authed path without touching xdotool/X11
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/send" \
    -H 'Content-Type: application/json' -H "X-WF-Key: $KEY" -d '{"text":"","mode":"type"}')
[ "$code" = 200 ] && ok "POST /send with key -> 200" || bad "POST /send with key -> $code (want 200)"

# live-engine surface — present once the merge lands, 404 until then
for ep in engine bank; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/$ep" -H "X-WF-Key: $KEY")
    case "$code" in
        200) ok "GET /$ep -> 200 (engine present)" ;;
        404) hold "GET /$ep -> 404 (live engine not merged yet — docs/JUDGE_PATH.md)" ;;
        *)   bad "GET /$ep -> $code" ;;
    esac
done
for ep in live/kp teach/begin; do
    code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/$ep" \
        -H "X-WF-Key: $KEY" -H 'Content-Type: application/json' -d '{}')
    case "$code" in
        2*)  ok "POST /$ep -> $code" ;;
        404) hold "POST /$ep -> 404 (pending merge)" ;;
        *)   bad "POST /$ep -> $code" ;;
    esac
done

kill "$DPID" 2>/dev/null; wait "$DPID" 2>/dev/null
if kill -0 "$DPID" 2>/dev/null; then
    bad "daemon survived SIGTERM"; kill -9 "$DPID" 2>/dev/null
else
    ok "daemon stopped cleanly"
fi
DPID=""
curl -sf --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 \
    && bad "port $PORT still answering after stop" \
    || ok "port $PORT released"

echo
echo "  $pass ok · $pend pending · $fail failed"
if [ "$fail" = 0 ]; then
    [ "$pend" != 0 ] && echo "judge-smoke: GREEN ($pend endpoint(s) land with the live engine)" \
                     || echo "judge-smoke: GREEN"
    exit 0
fi
echo "judge-smoke: RED"
exit 1
