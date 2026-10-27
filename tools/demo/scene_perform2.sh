#!/usr/bin/env bash
# scene_perform2.sh — a freshly taught sign firing in Go Live, landing in a
# real editor. Teaches HELLO-W via the real /teach flow, binds it to raise
# LibreOffice Writer and type at its cursor, then performs it.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
source "$HERE/lib.sh"; load_env
CLIPS="$HERE/clips"
PASS=0; FAIL=0
note() { log "      $1"; }
ok()   { PASS=$((PASS+1)); log "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); log "  FAIL  $1"; }

scene() { log "===================== SCENE $1 ====================="; }
scene "f6-perform  (teach sign -> bind -> perform -> types into LibreOffice Writer)"

G=HELLO-W
call DELETE "/bank?gloss=$G" >/dev/null
push_clip "$CLIPS/idle.json"; wait_drain 40; sleep 0.3
TAUGHT=0
for try in 1 2 3; do
    call POST /teach/begin "{\"name\":\"$G\",\"takes\":3,\"label\":\"hello\"}" >/dev/null
    push_clip "$CLIPS/teach-TYPE-HELLO.json"; wait_drain 90
    wait_state 's["teach"]["got"]>=3 or not s["teach"]["active"]' 90 0.25
    got=$("${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | \
          python3 -c "import json,sys;print(json.load(sys.stdin)['teach']['got'])")
    [ "$got" -ge 3 ] && { TAUGHT=1; break; }
    note "  teach $G try $try: got=$got/3"
    call POST /teach/end '{}' >/dev/null; sleep 0.4
done
call POST /teach/end '{}' >/dev/null
[ "$TAUGHT" = 1 ] || { bad "teach never reached 3 takes"; exit 1; }
ok "$G taught via the real /teach flow (3 takes)"

# bind AFTER teaching (PUT preserves exemplars, adds the argv)
call PUT /bank "{\"gloss\":\"$G\",\"argv\":[\"bash\",\"-c\",\"export DISPLAY=$D; wmctrl -a 'LibreOffice Writer' && sleep 0.4 && xdotool type --clearmodifiers --delay 30 'hello ' \"],\"tier\":0,\"label\":\"hello\"}" >/dev/null
ok "$G bound -> raise Writer + type at cursor"

ACT0=$(grep -c "act $G" $STATE/wfd.log 2>/dev/null || echo 0)
rec_start "$PROOFS/f6-perform.mp4"
ensure_listening || { bad "could not enter listening"; rec_stop "$PROOFS/f6-perform.mp4"; exit 1; }
push_clip "$CLIPS/perform-TYPE-HELLO.json"
FIRED=0
for _ in $(seq 1 80); do
    [ "$(grep -c "act $G" $STATE/wfd.log 2>/dev/null || echo 0)" -gt "$ACT0" ] && { FIRED=1; break; }
    sleep 0.25
done
sleep 2.5
rec_stop "$PROOFS/f6-perform.mp4"
if [ "$FIRED" = 1 ]; then
    ok "freshly taught sign fired -> Writer raised + 'hello' typed at its cursor"
else
    bad "taught sign never fired"
fi
