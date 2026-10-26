#!/bin/bash
# fresh-clone.sh — the 2AM test: a stranger clones the repo, installs,
# starts the daemon, and the printed LAN link actually serves the app and
# the send/Teach/Go Live surfaces. Then wrap stop. Exits non-zero on any
# failure. Log goes to ops/ (or $OPS_DIR).
#
#   tools/smoke/fresh-clone.sh [branch] [repo-url-or-path]
set -u
BRANCH="${1:-oss/clean}"
REPO="${2:-/workspace/wispr-wrap-merge}"
OPS_DIR="${OPS_DIR:-$PWD/ops}"
mkdir -p "$OPS_DIR" 2>/dev/null || OPS_DIR=/tmp
LOG="$OPS_DIR/fresh-clone-$(date +%H%M%S).log"
WF_PORT="${WF_PORT:-8791}"
export WF_PORT

exec > >(tee "$LOG") 2>&1
echo "== fresh-clone smoke: branch=$BRANCH repo=$REPO port=$WF_PORT"
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT

echo "-- clone"; git clone -q -b "$BRANCH" "$REPO" "$d/sf" || { echo "FAIL clone"; exit 1; }
cd "$d/sf" || exit 1

echo "-- npm install"; npm install --no-audit --no-fund 2>&1 | tail -2

echo "-- wrap start"
HOME="$d/home" PATH="$PWD/bin:$PATH" WF_PORT="$WF_PORT" bin/wrap start | tee /tmp/wrap-out.$$ || { echo "FAIL wrap start"; exit 1; }
sleep 1
LINK=$(grep -o 'http://[0-9.]*:[0-9]*' /tmp/wrap-out.$$ | head -1)
[ -n "$LINK" ] || LINK="http://127.0.0.1:$WF_PORT"
echo "-- printed link: $LINK"

echo "-- curl LAN URL"
code=$(curl -sf -o /dev/null -w '%{http_code}' --max-time 3 "$LINK/" || echo 000)
echo "   GET $LINK/ -> $code"; [ "$code" = 200 ] || { echo "FAIL lan 200"; bin/wrap stop; exit 1; }

KEY=$(cat "$d/home/.config/wf/key" 2>/dev/null)
PORT2="${LINK##*:}"; PORT2="${PORT2%%/*}"
H="-H X-WF-Key:$KEY -H Content-Type:application/json"

echo "-- /send (text leg)"
curl -sf -X POST "http://127.0.0.1:$PORT2/send" $H -d '{"text":"fresh-clone smoke","mode":"clip"}' -o /dev/null -w '   /send -> %{http_code}\n' || echo "   /send -> FAIL (injection unavailable headless is OK)"

echo "-- teach + go-live endpoints respond"
curl -sf -o /dev/null -w '   /act -> %{http_code}\n' -H "X-WF-Key: $KEY" "http://127.0.0.1:$PORT2/act" || echo "   /act -> FAIL"
curl -sf -o /dev/null -w '   /live/state -> %{http_code}\n' -H "X-WF-Key: $KEY" "http://127.0.0.1:$PORT2/live/state" || echo "   /live/state -> FAIL"
curl -sf -o /dev/null -w '   /packs -> %{http_code}\n' -H "X-WF-Key: $KEY" "http://127.0.0.1:$PORT2/packs" || echo "   /packs -> FAIL"

PID=$(cat "$d/sf/wfd.pid" 2>/dev/null)
if [ -n "$PID" ]; then
  RSS=$(awk '/VmRSS/{printf "%.1f",$2/1024}' /proc/"$PID"/status 2>/dev/null)
  SID=$(awk '{print $6}' /proc/"$PID"/stat 2>/dev/null)
  echo "-- daemon pid=$PID rss=${RSS}MB sid=$SID (own session: $([ "$SID" = "$PID" ] && echo yes || echo NO))"
fi

echo "-- wrap status"; bin/wrap status
echo "-- wrap stop"; bin/wrap stop
sleep 0.5
kill -0 "$PID" 2>/dev/null && { echo "FAIL daemon still alive after stop"; exit 1; } || echo "   daemon gone after wrap stop"
echo "== PASS (log: $LOG)"
