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
timeout 120 "$CHROME_BIN" --headless=new --disable-gpu --no-sandbox \
  --user-data-dir=/tmp/wf-chrome-teach --enable-logging=stderr --v=0 \
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
if grep -qiE "Uncaught|Unhandled" /tmp/wf-chrome.log; then
  echo "FAIL: uncaught errors in chrome console"; grep -iE "Uncaught|Unhandled" /tmp/wf-chrome.log | head; exit 1
fi

echo "== old-daemon feature detect =="
python3 tests/stub_wfd.py --port "$((PORT + 1))" --old >/tmp/wf-stub-old.log 2>&1 &
OLDSTUB=$!
i=0; while [ $i -lt 60 ]; do
  curl -sf "http://127.0.0.1:$((PORT + 1))/health" >/dev/null 2>&1 && break
  i=$((i+1)); sleep 0.1
done
timeout 45 "$CHROME_BIN" --headless=new --disable-gpu --no-sandbox \
  --user-data-dir=/tmp/wf-chrome-teach-old --virtual-time-budget=12000 \
  --dump-dom "http://127.0.0.1:$((PORT + 1))/index.html?k=teach-stub-key#/signs" \
  >/tmp/wf-dom-old.html 2>/dev/null
if grep -q "older wrap" /tmp/wf-dom-old.html; then
  echo "  older-daemon banner shown (honest 404 path)"
else
  echo "FAIL: /bank 404 did not surface the 'older wrap' banner"; exit 1
fi
if grep -q 'id="lib-banner" hidden' /tmp/wf-dom-old.html; then
  echo "FAIL: banner still hidden on old daemon"; exit 1
fi

echo "== clean-load check =="
timeout 45 "$CHROME_BIN" --headless=new --disable-gpu --no-sandbox \
  --user-data-dir=/tmp/wf-chrome-teach-load --virtual-time-budget=10000 \
  --dump-dom "http://127.0.0.1:$PORT/index.html?k=teach-stub-key" \
  >/tmp/wf-dom-load.html 2>/dev/null
grep -q 'id="box"' /tmp/wf-dom-load.html || { echo "FAIL: composer textarea missing"; exit 1; }
grep -q 'id="tw-name"' /tmp/wf-dom-load.html || { echo "FAIL: teach sheet not mounted"; exit 1; }
grep -q 'id="lv-cam"' /tmp/wf-dom-load.html || { echo "FAIL: live sheet not mounted"; exit 1; }
grep -q 'teach a sign' /tmp/wf-dom-load.html || { echo "FAIL: teach CTA missing"; exit 1; }
grep -q 'id="lib-list"' /tmp/wf-dom-load.html || { echo "FAIL: signs sheet not mounted"; exit 1; }

echo "ALL GREEN — teach page: selftest + old-daemon banner + clean load"
