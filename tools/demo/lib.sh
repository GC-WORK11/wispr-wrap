# lib.sh — shared helpers for tools/demo (the recorded-demo lane).
#
# The demo is driven by REPLAYED KEYPOINTS: tools/demo/clips/*.json are
# queued on the loopback replay service (:8877), the composer's own
# kp42 pump (Pose.replay -> KpLink) POSTs them to the REAL daemon as
# /live/kp — the daemon's own endpointer/DTW/act layers do the rest.
# Only the camera is replaced; nothing downstream is faked. Evidence is
# labelled "replayed keypoints".
#
# Contract: boot.sh writes $DEMO_STATE/env (mode 600 — holds the pairing
# key). Scripts source this file and call load_env.
#
#   DEMO_STATE (default /tmp/wf-demo)
#   PROOFS     (default <repo>/proofs/demo)
#
# Depends on the realrun stack env ($REALRUN_STATE/env) for BASE/KEY/D/
# SINK/MARKERS — boot.sh refreshes it via tools/realrun/setup.sh.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DEMO_STATE="${DEMO_STATE:-/tmp/wf-demo}"
DEMO_ENV="${DEMO_ENV:-$DEMO_STATE/env}"
PROOFS="${PROOFS:-$ROOT/proofs/demo}"
REALRUN_STATE="${REALRUN_STATE:-/tmp/wf-realrun}"
CLIPS="$HERE/clips"
REPLAY="${REPLAY:-http://127.0.0.1:8877}"

log()  { printf '[demo %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die()  { printf '[demo] FATAL: %s\n' "$*" >&2; exit 2; }
have() { command -v "$1" >/dev/null 2>&1; }
req()  { have "$1" || die "required tool missing: $1"; }

load_env() {
    [ -f "$REALRUN_STATE/env" ] || die "realrun stack env missing ($REALRUN_STATE/env) — run tools/demo/boot.sh"
    # the realrun env exports its own HERE/ROOT/STATE — pin ours first and
    # restore afterwards or "$HERE/record.sh" resolves into tools/realrun.
    local _h="$HERE" _r="$ROOT" _c="$CLIPS"
    # shellcheck disable=SC1090
    . "$REALRUN_STATE/env"          # D PORT BASE KEY SINK MARKERS TERM_WID ...
    [ -f "$DEMO_ENV" ] && . "$DEMO_ENV"   # CHROME_WID REPLAY REPLAY_OWNER
    HERE="$_h"; ROOT="$_r"; CLIPS="$_c"
    export DISPLAY="$D"
    mkdir -p "$PROOFS"
    # replay route tag = this daemon's port = the page's Origin port
    RT=(-H "X-Replay-Route: $PORT")
}

# --- HTTP (authed daemon calls, evidence-logged) ---------------------------
# Every curl is bounded: a wedged-but-listening socket otherwise stalls a
# scene indefinitely (seen: foreign agent restarted the shared daemon
# mid-run -> 22 min dead air). 8s is generous for loopback; callers retry.
CURL=(curl -sS --connect-timeout 3 --max-time 8)
CALL_BODY=""
call() { # call METHOD PATH [body] -> prints ONLY "<code>" on stdout
    # evidence lines go to stderr so $(call) captures the code alone and
    # `call ... >/dev/null` keeps the curl/response in the log.
    local m="$1" p="$2" body="${3:-}"
    CALL_BODY="$(mktemp /tmp/demo-body.XXXXXX)"
    local code args=()
    if [ -n "$body" ]; then
        args=(-H 'Content-Type: application/json' --data-binary "$body")
        log "+ curl -X $m $BASE$p $body" >&2
    else
        log "+ curl -X $m $BASE$p" >&2
    fi
    code=$("${CURL[@]}" -o "$CALL_BODY" -w '%{http_code}' -X "$m" \
        -H "X-WF-Key: $KEY" "${args[@]}" "$BASE$p")
    log "  -> $code $(head -c 220 "$CALL_BODY")" >&2
    printf '%s' "$code"
}
call_body() { cat "$CALL_BODY"; }

# --- replay queue (:8877) ---------------------------------------------------
# The demo lane tags clips with X-Replay-Route: $PORT — under
# replay_split.py only the composer whose Origin port matches drains them
# (shared box: several composers may pull :8877; untagged traffic lands in
# the "default" queue for everyone else, exactly like upstream).
RT=(-H "X-Replay-Route: ${PORT:-8874}")
qclear() { "${CURL[@]}" -X POST "${RT[@]}" "$REPLAY/clear" >/dev/null 2>&1; }
qpush() { # qpush <clip.json> -> prints ONLY the http code on stdout
    local n code
    n=$(python3 -c "import json;print(len(json.load(open('$1'))['frames']))" 2>/dev/null || echo '?')
    code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' -X POST \
        "${RT[@]}" -H 'Content-Type: application/json' \
        --data-binary "@$1" "$REPLAY/clip")
    # log AFTER the curl — and never to stdout: callers capture $(qpush) and
    # compare it to 200, so stdout must carry the code alone.
    printf '[demo %s] + replay /clip <- %s (%s frames) -> %s\n' \
        "$(date +%H:%M:%S)" "$(basename "$1")" "$n" "$code" >&2
    printf '%s' "$code"
}
qstats() { "${CURL[@]}" "${RT[@]}" "$REPLAY/stats" 2>/dev/null; }

# --- daemon state waits -----------------------------------------------------
_jqstate() { # python one-liner over GET /live/state; args = expr in `s`
    "${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | python3 -c "
import json,sys
s=json.load(sys.stdin)
print(bool($1))"
}
wait_state() { # wait_state 'expr' [tries] [sleep] — poll until expr true
    local expr="$1" tries="${2:-60}" sl="${3:-0.25}" i
    for i in $(seq 1 "$tries"); do
        [ "$(_jqstate "$expr")" = "True" ] && return 0
        sleep "$sl"
    done
    log "wait_state timeout: $expr"
    return 1
}
wait_listening()  { wait_state 's.get("listening")' "${1:-60}"; }
wait_notlistening(){ wait_state 'not s.get("listening")' "${1:-60}"; }
wait_marker() { # wait_marker PATH [tries]
    local p="$1" n="${2:-50}" i
    for i in $(seq 1 "$n"); do [ -e "$p" ] && return 0; sleep 0.2; done
    log "marker timeout: $p"; return 1
}
last_match_gloss() {
    "${CURL[@]}" -H "X-WF-Key: $KEY" "$BASE/live/state" | python3 -c "
import json,sys
m=(json.load(sys.stdin).get('last_match') or {})
print(m.get('gloss',''))"
}

# --- scene driver -----------------------------------------------------------
# push_clip <clip> — clear queue, push, return. The page drains it at ~12Hz;
# foreign consumers can steal a frame here and there (shared :8877), so the
# caller asserts the daemon-side effect and retries via push_clip_until.
push_clip() { qclear; sleep 0.15; [ "$(qpush "$1")" = 200 ]; }

# page_drain_wait — the replayed path only flows while the page's live/teach
# sheet is open AND its pull loop is fed; verify the queue is draining.
queue_drained() { [ "$("${CURL[@]}" "${RT[@]}" "$REPLAY/health" | python3 -c "import json,sys;print(json.load(sys.stdin)['queued'])" 2>/dev/null)" = 0 ]; }
wait_drain() { # wait_drain [tries] — queue empty again
    local i; for i in $(seq 1 "${1:-120}"); do queue_drained && return 0; sleep 0.25; done
    log "queue did not drain (is the live sheet open?)"; return 1
}

# ensure_listening — a stale 'listening=true' never survives a daemon
# restart or a foreign clip storm; re-palm whenever the flag is down.
ensure_listening() {
    wait_state 's.get("listening")' 8 0.25 && return 0
    local i
    for i in 1 2 3; do
        push_clip "$CLIPS/palm-hold.json" || continue
        wait_drain 60
        wait_state 's.get("listening")' 40 0.25 && return 0
    done
    log "could not enter listening"
    return 1
}

# --- windows on :97 ----------------------------------------------------------
# Shared box: other agents run their own chrome on the same display. Resolve
# OUR window by the chrome pid recorded at boot (pids.chrome), falling back
# to the page title — never `--class chrome | tail -1` (that grabs a
# foreign window when several chromes share :97).
chrome_wid() {
    local cp w
    cp=$(cat "$DEMO_STATE/pids.chrome" 2>/dev/null)
    if [ -n "$cp" ]; then
        for w in $(DISPLAY="$D" xdotool search --pid "$cp" 2>/dev/null); do
            DISPLAY="$D" xdotool getwindowname "$w" 2>/dev/null | grep -q "Wispr Wrap" && { echo "$w"; return 0; }
        done
    fi
    DISPLAY="$D" xdotool search --name "Wispr Wrap" 2>/dev/null | tail -1
}
chrome_nav() { # chrome_nav '#/live' — same-doc hash nav (no reload) via ctrl+l
    local frag="$1" wid
    wid=$(chrome_wid)
    [ -n "$wid" ] || { log "chrome_nav: no chrome window"; return 1; }
    DISPLAY="$D" xdotool windowactivate --sync "$wid" 2>/dev/null
    sleep 0.15
    DISPLAY="$D" xdotool key ctrl+l
    sleep 0.15
    DISPLAY="$D" xdotool type --delay 25 -- "http://127.0.0.1:$PORT/$frag"
    DISPLAY="$D" xdotool key Return
    sleep 0.8
}

layout() { # chrome left 940x800, evidence terminal right 340x800
    local cw ew
    cw=$(chrome_wid)
    [ -n "$cw" ] && DISPLAY="$D" wmctrl -i -r "$cw" -e "0,0,0,940,800"
    ew=$(DISPLAY="$D" xdotool search --name "wf-demo-evidence" 2>/dev/null | tail -1)
    [ -n "$ew" ] && DISPLAY="$D" wmctrl -i -r "$ew" -e "0,940,0,340,800"
    return 0
}

# --- recording ---------------------------------------------------------------
REC_PID=""
rec_start() { # rec_start <file.mp4>
    ffmpeg -hide_banner -loglevel error -f x11grab -video_size 1280x800 \
        -framerate 15 -i "$D" -c:v libx264 -crf 22 -preset veryfast \
        -pix_fmt yuv420p -y "$1" </dev/null >"$1.fflog" 2>&1 &
    REC_PID=$!
    log "recording -> $1 (pid $REC_PID)"
}
rec_stop() { # rec_stop <file.mp4>
    [ -n "$REC_PID" ] || return 0
    kill -INT "$REC_PID" 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        kill -0 "$REC_PID" 2>/dev/null || break; sleep 0.3
    done
    kill -0 "$REC_PID" 2>/dev/null && kill "$REC_PID" 2>/dev/null
    wait "$REC_PID" 2>/dev/null
    log "recorded ${1:-} ($(du -h "$1" 2>/dev/null | cut -f1))"
    REC_PID=""
}
