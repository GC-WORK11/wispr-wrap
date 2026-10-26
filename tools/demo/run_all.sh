#!/usr/bin/env bash
# run_all.sh — the orchestrated, recorded Wispr Wrap demo.
#
# What the recording shows (all REAL, only the camera is replaced):
#   chrome on :97 runs the real composer; its kp42 source is the replay
#   service (:8877) via the shipped Pose.replay seam — the page POSTs
#   /live/kp to the real daemon exactly as camera frames would. The
#   daemon's endpointer, python-dtw bank, commands.tsv router, and the
#   tier-2 confirm gate all run unmodified. An xfce4-terminal pane shows
#   the curl evidence + OS effects live.
#
# Scenes (each -> proofs/demo/<scene>.mp4 + run.log evidence):
#   01-listen     palm-hold -> listening=true ; namaste -> false
#   02-gate       perform while NOT listening -> nothing commits
#   03-teach      teach+bind OPEN-NOTES, TYPE-HELLO, LOCK (3 takes each,
#                 real /teach + /bank; gloss appears taught in GET /bank)
#   04-perform    OPEN-NOTES fires touch marker; TYPE-HELLO types into
#                 the real SinkEdit editor (sink.txt grows)
#   05-reject     untrained squiggle -> last_match accepted=false
#   06-confirm    LOCK (tier 2) parks in pending -> POST /act/confirm fires
#   07-namaste    palms together -> listening=false ; final evidence
#
# Re-runnable: markers cleared, glosses re-taught, recordings overwritten.
#   --no-boot   reuse a running env (boot.sh already done)
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$HERE/lib.sh"

BOOT=1
[ "${1:-}" = "--no-boot" ] && BOOT=0
[ "$BOOT" = 1 ] && bash "$HERE/boot.sh"
load_env

RUNLOG="$DEMO_STATE/run.log"; : > "$RUNLOG"
EVID_PID=""
cleanup() {
    # scene recordings run via record.sh start (subprocess owns REC_PID);
    # its pidfile is the source of truth so an early exit never orphans ffmpeg.
    [ -f "$DEMO_STATE/rec.pid" ] && bash "$HERE/record.sh" stop 2>/dev/null
    rec_stop 2>/dev/null || true
    [ -n "$EVID_PID" ] && kill "$EVID_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM
exec > >(tee -a "$RUNLOG") 2>&1

PASS=0; FAIL=0
scene() { log "===================== SCENE $1 ====================="; }
note()  { log "      $1"; }
ok()    { PASS=$((PASS+1)); log "  PASS  $1"; }
bad()   { FAIL=$((FAIL+1)); log "  FAIL  $1"; }

rec()      { bash "$HERE/record.sh" start "$1"; }
rec_done() { bash "$HERE/record.sh" stop; }

# clip via the REPLAY path — returns when the page has drained the queue.
play() { # play <clip> [tries] — push until queue drains (page pulled it all)
    local f="$1" i
    case "$f" in /*) ;; *) f="$CLIPS/$f";; esac   # accept bare name or full path
    for i in 1 2 3; do
        push_clip "$f" && wait_drain 120 && return 0
        note "clip $1 push/drain attempt $i failed — retry"
    done
    return 1
}

# push a clip then wait for a daemon-side effect, retrying the clip
# (a shared :8877 consumer can steal frames — bounded honest retries).
play_until() { # play_until <clip> <state-expr> <desc> [tries]
    local f="$1" expr="$2" desc="$3" tries="${4:-3}" i
    for i in $(seq 1 "$tries"); do
        play "$f"
        wait_state "$expr" 60 0.25 && { ok "$desc"; return 0; }
        note "  attempt $i: no effect yet ($desc)"
    done
    bad "$desc"; return 1
}

# ---------- start ----------------------------------------------------------
scene "boot"
note "realrun daemon $BASE · display $D · replay :8877 · evidence: REPLAYED KEYPOINTS"

# evidence terminal pane (right side of the recorded frame)
DISPLAY="$D" setsid xfce4-terminal --title wf-demo-evidence \
    --geometry 46x60 -x bash "$HERE/evidence_terminal.sh" \
    "$RUNLOG" "$MARKERS" "$SINK" </dev/null >/dev/null 2>&1 &
EVID_PID=$!
sleep 1.2
layout
sleep 0.5

# go live: hash nav to the live sheet. (?k= was already moved into
# localStorage by app.js on first load — a reload would still be paired.)
note "nav chrome -> #/live (composer live sheet)"
chrome_nav "#/live"
sleep 2.0

# prove the page's kp pump is live: queue an idle clip, watch it drain
before=$(qstats | python3 -c "import json,sys;print(json.load(sys.stdin)['served'])" 2>/dev/null || echo 0)
push_clip "$CLIPS/idle.json" || true
sleep 2
after=$(qstats | python3 -c "import json,sys;print(json.load(sys.stdin)['served'])" 2>/dev/null || echo 0)
note "replay served $before -> $after (page pulled $((after-before)) frames)"
if [ "$after" -le "$before" ]; then
    log "FATAL: composer page is not pulling replayed frames — is #/live open?"
    exit 1
fi
note "composer streaming replayed keypoints -> POST /live/kp on $BASE"

# ---------- 01 listen/stop -------------------------------------------------
scene "01-listen  (palm-open hold -> listening; namaste -> stop)"
rec "01-listen"
play_until palm-hold.json 's.get("listening")' \
    "palm-hold -> daemon mode=PALM-OPEN -> listening=true" 3
"${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | python3 -m json.tool
play_until namaste.json 'not s.get("listening")' \
    "namaste -> mode=NAMASTE -> listening=false" 3
rec_done

# ---------- 02 gate: perform while not listening ---------------------------
scene "02-gate  (same sign while NOT listening must not commit)"
rec "02-gate"
rm -f "$MARKERS/open-notes"
note "perform OPEN-NOTES with listening=false — expect seg=idle, no marker"
play perform-OPEN-NOTES.json
sleep 1.0
if [ -f "$MARKERS/open-notes" ]; then
    bad "marker appeared while not listening (listen gate broken)"
else
    ok "no marker while not listening — the palm gate gates actions"
fi
"${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | python3 -c "import json,sys;s=json.load(sys.stdin);print('  last_match:',s['last_match'])"
rec_done

# ---------- 03 teach + bind 3 signs -----------------------------------------
scene "03-teach  (3 takes each via real /teach/begin + replayed clips)"
rec "03-teach"
BINDINGS='{
  "OPEN-NOTES":{"argv":["/usr/bin/touch","'"$MARKERS"'/open-notes"],"tier":0,"label":"open notes"},
  "TYPE-HELLO":{"argv":["bash","'"$HERE"'/type_into_sink.sh","hello-from-the-demo-sign"],"tier":0,"label":"type hello"},
  "LOCK":{"argv":["/usr/bin/touch","'"$MARKERS"'/lock-confirmed"],"tier":2,"label":"lock screen"}
}'
for G in OPEN-NOTES TYPE-HELLO LOCK; do
    note "--- teach $G ---"
    call DELETE "/bank?gloss=$G" >/dev/null
    play idle.json    # flush segmenter + force any stale-session reset
    sleep 0.3
    TAUGHT=0
    for try in 1 2 3; do
        call POST /teach/begin "{\"name\":\"$G\",\"takes\":3,\"label\":\"$G\"}" >/dev/null
        play "teach-$G.json"
        # the page keeps streaming to the daemon for ~10s after the queue
        # drains — wait for the daemon-side take counter, not just /pull.
        wait_state 's["teach"]["got"]>=3 or not s["teach"]["active"]' 90 0.25
        got=$("${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | \
              python3 -c "import json,sys;print(json.load(sys.stdin)['teach']['got'])")
        [ "$got" -ge 3 ] && { TAUGHT=1; break; }
        note "  teach $G try $try: got=$got/3 — re-begin"
        call POST /teach/end '{}' >/dev/null; sleep 0.4
    done
    call POST /teach/end '{}' >/dev/null
    if [ "$TAUGHT" != 1 ]; then bad "teach $G never reached 3 takes"; continue; fi
    # bind through the real PUT /bank (writes commands.tsv)
    body=$(python3 -c "import json;d=json.loads('''$BINDINGS''')['$G'];d['gloss']='$G';print(json.dumps(d))")
    call PUT /bank "$body" >/dev/null
    call GET /bank >/dev/null
    if call_body | python3 -c "import json,sys;r=[x for x in json.load(sys.stdin)['signs'] if x['gloss']=='$G'];assert r and r[0].get('taught') and r[0]['n_exemplars']>=3 and r[0].get('bound'), r"; then
        ok "$G taught (3 exemplars) + bound: $(call_body | python3 -c "import json,sys;r=[x for x in json.load(sys.stdin)['signs'] if x['gloss']=='$G'][0];print(r.get('argv'))")"
    else
        bad "$G not taught/bound in GET /bank"
    fi
done
rec_done

# ---------- 04 perform ------------------------------------------------------
# perform_expect <clip> <probe-file> <desc> — ensure listening, push the
# clip, poll for the OS effect; retry twice (a daemon restart mid-scene
# drops `listening`, so each retry re-palms first).
perform_expect() {
    local clip="$1" probe="$2" desc="$3" i
    for i in 1 2 3; do
        ensure_listening || { note "  perform $desc: still not listening"; continue; }
        push_clip "$clip"
        wait_marker "$probe" 90 && return 0
        note "  perform $desc attempt $i: no effect yet"
    done
    return 1
}

scene "04-perform  (palm -> perform -> real OS effects)"
rec "04-perform"
rm -f "$MARKERS/open-notes"
: > "$SINK"
perform_expect "$CLIPS/perform-OPEN-NOTES.json" "$MARKERS/open-notes" \
    "OPEN-NOTES" \
    && ok "OPEN-NOTES matched -> argv fired -> $MARKERS/open-notes" \
    || bad "OPEN-NOTES never fired"
"${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | python3 -c "import json,sys;print('  last_match:',json.load(sys.stdin)['last_match'])"

note "TYPE-HELLO -> xdotool types into the real SinkEdit editor"
# probe = sink.txt containing the typed string (not just mtime)
sink_probe() { grep -q "hello-from-the-demo-sign" "$SINK" 2>/dev/null; }
for i in 1 2 3; do
    ensure_listening || continue
    push_clip "$CLIPS/perform-TYPE-HELLO.json"
    for _ in $(seq 1 80); do sink_probe && break; sleep 0.25; done
    sink_probe && break
    note "  TYPE-HELLO attempt $i: sink unchanged"
done
if sink_probe; then
    ok "TYPE-HELLO fired -> text in sink.txt: $(cat "$SINK")"
else
    bad "TYPE-HELLO never reached sink.txt"
fi
rec_done

# ---------- 05 reject -------------------------------------------------------
scene "05-reject  (untrained sign -> honest rejection)"
rec "05-reject"
ensure_listening
push_clip "$CLIPS/reject.json"
rej=0
for _ in $(seq 1 60); do
    acc=$("${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | python3 -c "import json,sys;m=json.load(sys.stdin).get('last_match') or {};print('Y' if (m.get('accepted') is False and not m.get('mode')) else 'N')" 2>/dev/null)
    [ "$acc" = Y ] && { rej=1; break; }
    sleep 0.25
done
[ "$rej" = 1 ] && ok "rejected: $("${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | python3 -c "import json,sys;m=json.load(sys.stdin)['last_match'];print(m['gloss'],m['dist'],m['accepted'],m.get('reason'))")" \
              || bad "no honest rejection in last_match"
rec_done

# ---------- 06 tier-2 confirm ------------------------------------------------
scene "06-confirm  (tier-2 action parks for confirm -> /act/confirm fires)"
rec "06-confirm"
rm -f "$MARKERS/lock-confirmed"
ensure_listening
pend=0
for try in 1 2 3; do
    ensure_listening || continue   # a mid-scene daemon restart drops the flag
    push_clip "$CLIPS/perform-LOCK.json"
    for _ in $(seq 1 60); do
        PID=$("${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | python3 -c "import json,sys;p=json.load(sys.stdin)['pending'];print(p[0]['id'] if p else '')" 2>/dev/null)
        [ -n "$PID" ] && { pend=1; break; }
        sleep 0.25
    done
    [ "$pend" = 1 ] && break
done
if [ "$pend" = 1 ]; then
    note "LOCK matched tier-2 -> parked pending id=$PID (NOT fired yet: $([ -f "$MARKERS/lock-confirmed" ] && echo BAD || echo none))"
    call POST /act/confirm "{\"id\":\"$PID\"}" >/dev/null
    wait_marker "$MARKERS/lock-confirmed" 40 \
        && ok "confirm released -> $MARKERS/lock-confirmed" \
        || bad "confirm released but marker missing"
else
    bad "LOCK never landed in pending"
fi
rec_done

# ---------- 07 namaste + final evidence --------------------------------------
scene "07-namaste  (stop listening) + final evidence"
rec "07-namaste"
play_until namaste.json 'not s.get("listening")' "namaste -> listening=false" 3
note "final daemon state:"
"${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | python3 -m json.tool
note "GET /bank:"
call GET /bank >/dev/null; call_body | python3 -m json.tool
note "GET /act:"
call GET /act >/dev/null; call_body | python3 -m json.tool
note "markers: $(ls "$MARKERS" 2>/dev/null | tr '\n' ' ')"
note "sink.txt: $(cat "$SINK" 2>/dev/null)"
rec_done

log "===================== RESULT: $PASS pass / $FAIL fail ====================="
printf '%s\t%d pass / %d fail — replayed keypoints through chrome -> real wfd.py :%s -> real OS effects\n' \
    "$( [ "$FAIL" = 0 ] && echo VERIFIED || echo FAILED )" "$PASS" "$FAIL" "$PORT" > "$PROOFS/RESULT"
cat "$PROOFS/RESULT"
# evidence: run.log never carries the pairing key (boot.sh's ?k= line is
# printed to the caller's stdout, not teed here) — safe to publish.
cp "$RUNLOG" "$PROOFS/run.log"
ls -la "$PROOFS"
exit "$([ "$FAIL" = 0 ] && echo 0 || echo 1)"
