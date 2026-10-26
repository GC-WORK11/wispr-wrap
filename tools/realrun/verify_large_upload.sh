#!/usr/bin/env bash
# verify_large_upload.sh — PEAK backend: prove /send-file streams to disk
# with flat RSS, enforces the cap before reading, sanitises names, and
# cleans up aborted partials. Uses the realrun env (PEAK_ENV or the
# default state dir). Does NOT need a browser — pure curl + /proc.
set -u
ENV_FILE="${PEAK_ENV:-/tmp/wf-realrun-$(basename "$PWD")/env}"
[ -f "$ENV_FILE" ] || { echo "no env at $ENV_FILE — run setup.sh first"; exit 2; }
# shellcheck disable=SC1090
source "$ENV_FILE"

PID=$(pgrep -f "server/wfd.py" | while read -r p; do
  grep -q "PORT=$PORT\|WF_PORT=$PORT" "/proc/$p/environ" 2>/dev/null && echo "$p"
done | head -1)
[ -z "$PID" ] && PID=$(pgrep -f "server/wfd.py" | head -1)
UP="$WFHOME/wf-uploads"
hwm() { awk '/VmHWM/{print $2}' "/proc/$PID/status" 2>/dev/null; }
fail=0
pass() { echo "PASS $1"; }
bad()  { echo "FAIL $1"; fail=1; }

mkdir -p /tmp/vlu
echo "daemon pid=$PID  uploads=$UP"
start_hwm=$(hwm)

# sizes: 1K, 5M, 60M
for spec in "1k:1024:1" "5m:1048576:5" "60m:1048576:60"; do
  nm=${spec%%:*}; bs=$(echo "$spec" | cut -d: -f2); cnt=$(echo "$spec" | cut -d: -f3)
  dd if=/dev/urandom of="/tmp/vlu/$nm.bin" bs="$bs" count="$cnt" 2>/dev/null
  want=$(sha256sum "/tmp/vlu/$nm.bin" | awk '{print $1}')
  resp=$(curl -s -X POST "$BASE/send-file" -H "X-WF-Key: $KEY" \
         -H "X-File-Name: vlu_$nm.bin" --data-binary @"/tmp/vlu/$nm.bin")
  path=$(echo "$resp" | python3 -c "import sys,json;print(json.load(sys.stdin).get('path',''))" 2>/dev/null)
  if [ -n "$path" ] && [ "$(sha256sum "$path" | awk '{print $1}')" = "$want" ]; then
    pass "$nm upload byte-identical"
  else
    bad "$nm upload (resp=$resp)"
  fi
done
end_hwm=$(hwm)
# RSS should not grow by more than ~20 MB across a 60 MB streamed upload
if [ -n "$start_hwm" ] && [ -n "$end_hwm" ] && [ $((end_hwm - start_hwm)) -lt 20480 ]; then
  pass "peak RSS flat (VmHWM ${start_hwm}->${end_hwm} kB)"
else
  bad "peak RSS grew (VmHWM ${start_hwm}->${end_hwm} kB)"
fi

# cap: advertise 2 GiB, send a sliver -> fast 413, body never read
code=$(curl -s -o /tmp/vlu/cap.json -w "%{http_code}" --max-time 5 \
       -X POST "$BASE/send-file" -H "X-WF-Key: $KEY" -H "X-File-Name: huge.bin" \
       -H "Content-Length: 2147483648" -H "Expect:" -d "" 2>/dev/null)
if [ "$code" = "413" ] && grep -q '"error"' /tmp/vlu/cap.json; then
  pass ">cap 413 with human error"; else bad ">cap (code=$code)"; fi

# filename traversal -> stays inside uploads
resp=$(curl -s -X POST "$BASE/send-file" -H "X-WF-Key: $KEY" \
       -H "X-File-Name: ..%2F..%2Fetc%2Fpasswd" --data-binary @/tmp/vlu/1k.bin)
path=$(echo "$resp" | python3 -c "import sys,json;print(json.load(sys.stdin).get('path',''))" 2>/dev/null)
case "$path" in "$UP"/*passwd) pass "traversal name sanitised to basename";;
  *) bad "traversal (path=$path)";; esac

# abort at ~15% leaves no partial
before=$(ls -a "$UP" 2>/dev/null | grep -c '.wf-up-')
( dd if=/dev/zero bs=1048576 count=20 2>/dev/null | timeout 0.3 \
  curl -s -X POST "$BASE/send-file" -H "X-WF-Key: $KEY" \
  -H "X-File-Name: aborted.bin" -H "Content-Length: 20971520" --data-binary @- ) 2>/dev/null
sleep 1
after=$(ls -a "$UP" 2>/dev/null | grep -c '.wf-up-')
if [ "$after" -le "$before" ] && ! ls "$UP" 2>/dev/null | grep -q aborted; then
  pass "aborted upload left no partial"; else bad "aborted upload left a partial"; fi

echo "----"
[ "$fail" = 0 ] && echo "ALL PASS" || echo "SOME FAILED"
exit "$fail"
