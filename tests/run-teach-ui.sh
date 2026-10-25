#!/bin/sh
# run-teach-ui.sh — headless UI test for the merged app shell
# (web/index.html + js/* modules).
#
#   1. serves tests/stub_wfd.py + web/ on :8743
#   2. drives a REAL headless Chrome through /selftest — the #/teach wizard
#      inside the merged shell teaches a fake sign (name → 3 daemon-captured
#      kp42 takes via the WFFakeTrack seam → @ picker → PUT /bank), then
#      exercises +take, rename and delete against the stub bank
#   3. checks the old-daemon path: stub --old 404s /bank → the signs sheet
#      must show the honest "older wrap" banner, not fake success
#
# Needs: python3, google-chrome (headless). No node, no deps.
# Usage: tests/run-teach-ui.sh [port]   exit 0 = all green
set -u
cd "$(dirname "$0")/.."
PORT="${1:-8743}"
RESULT="${WF_STUB_RESULT:-/tmp/wf-selftest-result.json}"
rm -f "$RESULT" /tmp/wf-stub.log /tmp/wf-stub-old.log /tmp/wf-chrome.log /tmp/wf-dom-old.html

CHROME_BIN="$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)"
[ -n "$CHROME_BIN" ] || { echo "no chrome/chromium found"; exit 2; }

# stray chromes/stubs from a killed earlier run keep polling and racing us
pkill -f "user-data-dir=/tmp/wf-chrome-teach" 2>/dev/null
pkill -f "stub_wfd.py --port $PORT" 2>/dev/null
rm -rf /tmp/wf-chrome-teach /tmp/wf-chrome-teach-old /tmp/wf-chrome-teach-load
sleep 0.2

python3 tests/stub_wfd.py --port "$PORT" >/tmp/wf-stub.log 2>&1 &
STUB=$!
OLDSTUB=""; CPID=""
cleanup() { for p in "$STUB" "$OLDSTUB" "$CPID"; do [ -n "$p" ] && kill "$p" 2>/dev/null; done; }
trap cleanup EXIT

i=0; while [ $i -lt 60 ]; do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  i=$((i+1)); sleep 0.1
done
curl -sf "http://127.0.0.1:$PORT/health" >/dev/null || { echo "stub never came up"; cat /tmp/wf-stub.log; exit 1; }

echo "== selftest: teach a fake sign end-to-end =="
# fresh profile every run — a reused dir can restore the previous
# session's /selftest tab and race the stub with a second run.
rm -rf /tmp/wf-chrome-teach
timeout 120 "$CHROME_BIN" --headless=new --disable-gpu --no-sandbox \
  --user-data-dir=/tmp/wf-chrome-teach --no-first-run \
  --disable-session-crashed-bubble --hide-crash-restore-bubble \
  --enable-logging=stderr --v=0 \
  "http://127.0.0.1:$PORT/selftest" >/tmp/wf-chrome.log 2>&1 &
CPID=$!

i=0; while [ $i -lt 900 ]; do [ -f "$RESULT" ] && break; i=$((i+1)); sleep 0.1; done
kill "$CPID" 2>/dev/null; CPID=""
[ -f "$RESULT" ] || { echo "FAIL: no result posted"; tail -30 /tmp/wf-chrome.log; exit 1; }

python3 - "$RESULT" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
for s in r.get("steps", []):
    print("  ·", s)
if r.get("errs"):
    print("  console errors:", *r["errs"], sep="\n    ")
sys.exit(0 if r.get("pass") else 1)
PY
[ $? -eq 0 ] || { echo "== stub log =="; cat /tmp/wf-stub.log; exit 1; }
echo "  selftest PASS"

# chrome console must be quiet of uncaught errors
if grep -iE "Uncaught|Unhandled" /tmp/wf-chrome.log \
   | grep -qv "chrome-extension://"; then
  echo "FAIL: uncaught errors in chrome console"
  # only errors from OUR pages count — chrome-extension:// noise is
  # from the host's installed extensions, not the app.
  grep -iE "Uncaught|Unhandled" /tmp/wf-chrome.log \
    | grep -v "chrome-extension://" | head; exit 1
fi

echo "== old-daemon feature detect =="
python3 tests/stub_wfd.py --port "$((PORT + 1))" --old >/tmp/wf-stub-old.log 2>&1 &
OLDSTUB=$!
i=0; while [ $i -lt 60 ]; do
  curl -sf "http://127.0.0.1:$((PORT + 1))/health" >/dev/null 2>&1 && break
  i=$((i+1)); sleep 0.1
done
OLDRESULT="$RESULT.old"; rm -f "$OLDRESULT"
timeout 90 "$CHROME_BIN" --headless=new --disable-gpu --no-sandbox \
  --user-data-dir=/tmp/wf-chrome-teach-old --no-first-run \
  --enable-logging=stderr --v=0 \
  "http://127.0.0.1:$((PORT + 1))/oldtest" >/tmp/wf-chrome-old.log 2>&1 &
CPID=$!
i=0; while [ $i -lt 450 ]; do [ -f "$OLDRESULT" ] && break; i=$((i+1)); sleep 0.1; done
kill "$CPID" 2>/dev/null; CPID=""
[ -f "$OLDRESULT" ] || { echo "FAIL: no old-daemon result posted"; \
  cat /tmp/wf-stub-old.log; exit 1; }
python3 - "$OLDRESULT" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
for s in r.get("steps", []):
    print("  ·", s)
if r.get("errs"):
    print("  console errors:", *r["errs"], sep="\n    ")
sys.exit(0 if r.get("pass") else 1)
PY
[ $? -eq 0 ] || { echo "== old stub log =="; cat /tmp/wf-stub-old.log; exit 1; }
echo "  older-daemon degraded states verified"

echo "== clean-load check =="
LOADRESULT="$RESULT.load"; rm -f "$LOADRESULT"
timeout 60 "$CHROME_BIN" --headless=new --disable-gpu --no-sandbox \
  --user-data-dir=/tmp/wf-chrome-teach-load --no-first-run \
  --enable-logging=stderr --v=0 \
  "http://127.0.0.1:$PORT/loadtest" >/tmp/wf-chrome-load.log 2>&1 &
CPID=$!
i=0; while [ $i -lt 300 ]; do [ -f "$LOADRESULT" ] && break; i=$((i+1)); sleep 0.1; done
kill "$CPID" 2>/dev/null; CPID=""
[ -f "$LOADRESULT" ] || { echo "FAIL: no load result posted"; exit 1; }
python3 - "$LOADRESULT" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
for s in r.get("steps", []):
    print("  ·", s)
if r.get("errs"):
    print("  console errors:", *r["errs"], sep="\n    ")
sys.exit(0 if r.get("pass") else 1)
PY
[ $? -eq 0 ] || exit 1

echo "ALL GREEN — merged shell: teach flow + old-daemon degrade + clean load"
