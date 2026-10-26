# lib.sh — shared helpers for the realrun verification lane (W14).
#
# Contract: setup.sh boots the stack and writes $REALRUN_STATE/env
# (default /tmp/wf-realrun/env, mode 600 — it holds the pairing key, it
# NEVER goes into the repo). Every verify_*.sh sources this file, calls
# load_env, and produces proofs/realrun/<feature>/ with:
#   before.txt after.txt diff.txt shot*.png video.mp4(optional) log RESULT
# RESULT line: <VERIFIED|FAILED|PENDING|UNVERIFIABLE|NEVER-ON-BOX>\t<one line why>
#
# Honesty rules baked in here:
#  - effects are only ever triggered through the REAL path (the daemon's
#    HTTP endpoints, or the OS tools a real binding would spawn) — never
#    by calling an implementation function directly.
#  - sanitize_dir() redacts the pairing key/code out of every artifact
#    and FAILS the verify if a secret is still present afterwards.
#  - a verify that cannot run on this box must say so and exit 77, never
#    fake a result.

# State dir is namespaced PER WORKTREE: several agents share this box
# (/workspace/wispr-wrap, wispr-wrap-pro, wispr-wrap-alt all run the same
# gate) and a shared /tmp/wf-realrun meant one agent's env/pids/windows
# stomped another's — verifiers sourced a FOREIGN env and ran another
# tree's helpers. Never again: the suffix comes from the repo dir name.
_RR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REALRUN_STATE="${REALRUN_STATE:-/tmp/wf-realrun-$(basename "$_RR_ROOT")}"
REALRUN_ENV="${REALRUN_ENV:-$REALRUN_STATE/env}"
REALRUN_PROOFS="${REALRUN_PROOFS:-$_RR_ROOT/proofs/realrun}"

log()  { printf '[realrun %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die()  { printf '[realrun] FATAL: %s\n' "$*" >&2; exit 2; }
have() { command -v "$1" >/dev/null 2>&1; }
req()  { have "$1" || die "required tool missing: $1"; }

load_env() {
    [ -f "$REALRUN_ENV" ] || die "stack is not up — run tools/realrun/setup.sh first ($REALRUN_ENV missing)"
    # shellcheck disable=SC1090
    . "$REALRUN_ENV"
    : "${D:?env missing D}" "${PORT:?}" "${WFHOME:?}" "${KEY:?}" "${SINK:?}" "${MARKERS:?}"
    export DISPLAY="$D"
    BASE="http://127.0.0.1:${PORT}"
}

# --- HTTP --------------------------------------------------------------
# call METHOD PATH [body-or-@file] [extra curl args...]
#   -> prints "<http_code>" AND sets $CALL_CODE; body lands in $CALL_BODY.
#   NB: `code=$(call ...)` runs call in a SUBSHELL — CALL_BODY/CALL_CODE
#   are lost to the parent. When you need the body, run call plainly:
#       call POST /x '{"a":1}' >/dev/null; code=$CALL_CODE; call_body
CALL_BODY=""; CALL_CODE=""
call() {
    local m="$1" p="$2" body="${3:-}"
    if [ $# -ge 3 ]; then shift 3; else shift $#; fi
    CALL_BODY="$(mktemp /tmp/realrun-body.XXXXXX)"
    local code
    if [ -n "$body" ]; then
        case "$body" in
            @*) code=$(curl -sS -o "$CALL_BODY" -w '%{http_code}' -X "$m" \
                     -H "X-WF-Key: $KEY" --data-binary "${body}" "$@" "$BASE$p") ;;
            *)  code=$(curl -sS -o "$CALL_BODY" -w '%{http_code}' -X "$m" \
                     -H "X-WF-Key: $KEY" -H 'Content-Type: application/json' \
                     --data-binary "$body" "$@" "$BASE$p") ;;
        esac
    else
        code=$(curl -sS -o "$CALL_BODY" -w '%{http_code}' -X "$m" \
                 -H "X-WF-Key: $KEY" "$@" "$BASE$p")
    fi
    CALL_CODE="$code"
    printf '%s' "$code"
}
# unauthenticated call (auth-gate probes)
call_noauth() {
    local m="$1" p="$2" body="${3:-}"
    CALL_BODY="$(mktemp /tmp/realrun-body.XXXXXX)"
    local code
    if [ -n "$body" ]; then
        code=$(curl -sS -o "$CALL_BODY" -w '%{http_code}' -X "$m" \
               -H 'Content-Type: application/json' --data-binary "$body" "$BASE$p")
    else
        code=$(curl -sS -o "$CALL_BODY" -w '%{http_code}' -X "$m" "$BASE$p")
    fi
    CALL_CODE="$code"
    printf '%s' "$code"
}
call_body() { cat "$CALL_BODY"; }
call_json() { python3 - "$CALL_BODY" "$@" <<'PY'
import json,sys
try: print(json.load(open(sys.argv[1])).get(sys.argv[2],""))
except Exception: print("")
PY
}

# --- OS-state snapshots --------------------------------------------------
snapshot() { # > writes a full observable-state snapshot to stdout
    {
        echo "# realrun snapshot $(date -Is)"
        echo "## socket :$PORT"
        ss -tln 2>/dev/null | grep -E "(:${PORT}\b|State)" || echo "(no listener)"
        echo "## active window"
        DISPLAY="$D" xdotool getactivewindow getwindowname getwindowclassname 2>&1
        echo "## _NET_ACTIVE_WINDOW"
        DISPLAY="$D" xprop -root _NET_ACTIVE_WINDOW 2>&1
        echo "## window tree"
        DISPLAY="$D" xwininfo -root -tree 2>&1
        echo "## sink (sha + tail)"
        sha256sum "$SINK" 2>/dev/null || echo "(no sink yet)"
        tail -8 "$SINK" 2>/dev/null
        echo "## markers dir"
        ls -la "$MARKERS" 2>/dev/null | grep -v '^total' || true
        echo "## wf config hashes"
        for f in commands.tsv signbank.json approved.json; do
            sha256sum "$WFHOME/.config/wf/$f" 2>/dev/null || echo "absent $f"
        done
        echo "## wf uploads"
        ls -la "$WFHOME/wf-uploads" 2>/dev/null | grep -v '^total' || echo "(none)"
        echo "## CLIPBOARD targets"
        DISPLAY="$D" timeout 3 xclip -selection clipboard -t TARGETS -o 2>&1 | sort -u
    }
}

shot() { # shot out.png — one real frame of the private display
    ffmpeg -hide_banner -loglevel error -f x11grab -video_size 1280x800 \
        -i "$D" -frames:v 1 -y "$1"
}

# ffmpeg recorder: rec_start file.mp4 ; rec_stop
REC_PID=""
rec_start() {
    ffmpeg -hide_banner -loglevel error -f x11grab -video_size 1280x800 \
        -framerate 15 -i "$D" -pix_fmt yuv420p -y "$1" \
        </dev/null >"$1.fflog" 2>&1 &
    REC_PID=$!
}
rec_stop() {
    [ -n "$REC_PID" ] || return 0
    # SIGINT lets ffmpeg finalize the mp4; fall back to TERM
    kill -INT "$REC_PID" 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$REC_PID" 2>/dev/null || break
        sleep 0.3
    done
    kill -0 "$REC_PID" 2>/dev/null && kill "$REC_PID" 2>/dev/null
    wait "$REC_PID" 2>/dev/null
    REC_PID=""
}

# --- focus ----------------------------------------------------------------
focus_term() { # focus the sink terminal; die if it won't take focus
    DISPLAY="$D" xdotool windowactivate --sync "$TERM_WID" 2>/dev/null
    local i
    for i in $(seq 1 20); do
        [ "$(DISPLAY="$D" xdotool getactivewindow 2>/dev/null)" = "$TERM_WID" ] && return 0
        sleep 0.2
    done
    echo "could not focus terminal wid=$TERM_WID" >&2; return 1
}

# --- the shared sign->effect lane --------------------------------------------
# teach_bind_fire GLOSS LABEL "argv string" [tier]
#   Runs the whole REAL chain against the live daemon:
#     POST /teach/begin (takes=1) -> kpstream clip -> /teach/end
#     PUT /bank {gloss,label,argv,tier}  (the real binding write path)
#     kpstream palm (listening on) -> kpstream sign:<GLOSS>
#   Returns the last /live/kp status on stdout is silent; the caller checks
#   the OS effect. Exits non-zero only on contract breakage (4xx on the
#   endpoints themselves -> rc 3 = route pending).
teach_bind_fire() {
    local g="$1" label="$2" argv="$3" tier="${4:-0}"
    local code
    code=$(call POST /teach/begin "{\"name\":\"$g\",\"takes\":1,\"label\":\"$label\"}")
    [ "$code" = 200 ] || return 3
    python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 "teach:$g" >/dev/null
    sleep 0.25
    code=$(call POST /teach/end '{}')
    code=$(call PUT /bank "{\"gloss\":\"$g\",\"label\":\"$label\",\"argv\":\"$argv\",\"tier\":$tier}")
    [ "$code" = 200 ] || return 3
    python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 palm >/dev/null 2>&1
    sleep 0.3
    python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 "sign:$g"
    return 0
}

# wait_file PATH [tries] — poll until file exists
wait_file() { local p="$1" n="${2:-30}" i; for i in $(seq 1 "$n"); do
    [ -e "$p" ] && return 0; sleep 0.2; done; return 1; }

# --- results ----------------------------------------------------------------
RESULT_STATUS=""
result() { # result STATUS reason — writes RESULT into $PROOF, exits via caller
    RESULT_STATUS="$1"
    printf '%s\t%s\n' "$1" "$2" | tee "$PROOF/RESULT"
}
verdict() { # verdict STATUS reason — sanitize proofs, print, exit appropriately
    local st="$1" why="$2"
    result "$st" "$why"
    sanitize_dir "$PROOF" || { echo "RESULT	SANITIZE-FAILED" > "$PROOF/RESULT"; exit 1; }
    case "$st" in
        VERIFIED) exit 0 ;;
        FAILED)   exit 1 ;;
        *)        exit 77 ;;   # PENDING / UNVERIFIABLE / NEVER-ON-BOX
    esac
}

# --- secret hygiene ---------------------------------------------------------
sanitize_dir() { # dir — redact KEY/PAIR_CODE + generic patterns; fail if any remain
    local d="$1" f
    [ -d "$d" ] || return 0
    if [ -n "${KEY:-}" ]; then
        grep -rlF "$KEY" "$d" 2>/dev/null | while read -r f; do
            sed -i "s/$(printf '%s' "$KEY" | sed 's/[][\.*^$/]/\\&/g')/<redacted-key>/g" "$f"
        done
    fi
    if [ -n "${PAIR_CODE:-}" ]; then
        grep -rlF "$PAIR_CODE" "$d" 2>/dev/null | while read -r f; do
            sed -i "s/$(printf '%s' "$PAIR_CODE" | sed 's/[][\.*^$/]/\\&/g')/<redacted-code>/g" "$f"
        done
    fi
    # belt-and-suspenders: known emission shapes in daemon logs / json bodies
    find "$d" -type f \( -name '*.txt' -o -name '*.log' -o -name '*.json' \) \
        -print0 2>/dev/null | xargs -0 -r sed -i -E \
        -e 's/(pair code: *)[A-Z0-9]+/\1<redacted>/g' \
        -e 's/("key": *")([0-9a-f]{40})"/\1<redacted>"/g' \
        -e 's/("code": *")([A-Z0-9]{8})"/\1<redacted>"/g'
    if [ -n "${KEY:-}" ] && grep -rqF "$KEY" "$d" 2>/dev/null; then
        echo "sanitize FAILED: pairing key still present under $d" >&2; return 1
    fi
    if [ -n "${PAIR_CODE:-}" ] && grep -rqF "$PAIR_CODE" "$d" 2>/dev/null; then
        echo "sanitize FAILED: pair code still present under $d" >&2; return 1
    fi
    return 0
}
