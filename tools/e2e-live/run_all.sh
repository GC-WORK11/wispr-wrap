#!/usr/bin/env bash
# run_all — the one-command e2e gate for the merged live pipeline.
#
#   starts server/wfd.py on an ALT port with a temp HOME (so ~/.config/wf
#   is a scratch dir — real config untouched), seeds a stub commands.tsv
#   whose actions are `/usr/bin/touch <marker>` files (never xdotool) and
#   a Rust-format signbank.json, then runs:
#
#     1. tools/e2e-live/replay.py    — 15Hz /live/kp e2e, teach->perform->assert
#     2. server/tests/test_parity.py — signbank.py vs matcher.rs semantics
#
# Prints a PASS/FAIL/SKIP table and kills the daemon on the way out.
# Exit: 0 = ran, nothing failed (skips shown plainly); 77 = every suite
# skipped (live lane not landed yet); 1 = a real failure.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

PORT="${WF_E2E_PORT:-8893}"
command -v python3 >/dev/null || { echo "python3 required"; exit 1; }
free_port() { # preferred port -> echoes a port that binds free
    python3 - "$1" <<'PY'
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1]))); s.close()
    print(sys.argv[1])
except OSError:
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    print(s.getsockname()[1]); s.close()
PY
}
# if the default alt port is taken, grab a free one
PORT="$(free_port "$PORT")"

TMP="$(mktemp -d /tmp/wf-e2e.XXXXXX)"
WF_HOME="$TMP/home"
CONFIG="$WF_HOME/.config/wf"
MARKERS="$TMP/markers"
mkdir -p "$CONFIG" "$MARKERS"
WFD_PID=""

cleanup() {
    [ -n "$WFD_PID" ] && kill "$WFD_PID" 2>/dev/null && wait "$WFD_PID" 2>/dev/null
    rm -rf "$TMP"
}
trap cleanup EXIT INT TERM

# --- stub command table: gloss -> marker file, argv-spawn, no shell ----
: > "$CONFIG/commands.tsv"
for g in SWIPE-R SWIPE-L PUSH-UP CIRCLE Z-SHAPE V-CHECK; do
    printf '%s\t%s marker\t/usr/bin/touch %s\n' "$g" "$g" \
        "$MARKERS/$g" >> "$CONFIG/commands.tsv"
done

# --- seed a Rust-format bank (normalized exemplars of the synthetic signs)
PYTHONPATH="$HERE" python3 - "$CONFIG/signbank.json" <<'PY'
import sys
import refmatch, synth
bank = refmatch.RefBank(reject_threshold=0.45)
for name in synth.sign_paths():
    # two exemplars: the truth path + a jittered variant (teach ~x2)
    p = synth.truth_path(name, n=18)
    bank.add(name, p)
    import random
    rng = random.Random(3)
    bank.add(name, [(x + rng.gauss(0, 0.008), y + rng.gauss(0, 0.008)) for x, y in p])
bank.save(sys.argv[1])
print(f"seeded bank: {len(bank.templates)} glosses -> {sys.argv[1]}")
PY

wait_health() { # port [key] -> 0 when OUR daemon serves (authed check
    # when a key is passed — a stale squatter on the port returns /health
    # 200 too, but 403s the authed probe, so this never mistakes it).
    python3 - "$1" "${2:-}" <<'PY'
import sys, time, urllib.request
port, key = sys.argv[1], sys.argv[2]
for _ in range(80):
    try:
        if urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=1).status != 200:
            raise RuntimeError("health")
        if key:
            r = urllib.request.Request(f"http://127.0.0.1:{port}/live/state")
            r.add_header("X-WF-Key", key)
            if urllib.request.urlopen(r, timeout=1).status != 200:
                raise RuntimeError("authed probe")
        sys.exit(0)
    except Exception:
        time.sleep(0.1)
sys.exit(1)
PY
}

# --- boot the real daemon -----------------------------------------------
env HOME="$WF_HOME" WF_PORT="$PORT" WF_CONFIG_DIR="$CONFIG" \
    python3 "$ROOT/server/wfd.py" > "$TMP/wfd.log" 2>&1 &
WFD_PID=$!
if ! wait_health "$PORT"; then
    echo "wfd.py did not come up on :$PORT — log:"
    tail -20 "$TMP/wfd.log"
    exit 1
fi
KEY="$(cat "$CONFIG/key" 2>/dev/null || true)"
if [ -z "$KEY" ]; then
    echo "no pairing key at $CONFIG/key — daemon config layout changed?"
    tail -20 "$TMP/wfd.log"
    exit 1
fi
echo "daemon up on :$PORT (temp config $CONFIG)"

# --- run the suites -----------------------------------------------------
declare -a NAMES RCS
run() { # name, cmd...
    local name="$1"; shift
    "$@" ; local rc=$?
    NAMES+=("$name"); RCS+=("$rc")
}

run "replay vs wfd.py /live/kp" \
    python3 "$HERE/replay.py" --port "$PORT" --key "$KEY" \
        --marker-dir "$MARKERS" --json-out "$TMP/replay.json"

run "parity signbank vs matcher.rs" \
    env PARITY_JSON="$TMP/parity.json" \
    python3 "$ROOT/server/tests/test_parity.py"

# --- contract-double leg: the same suite against stubdaemon -------------
# Proves the harness itself end-to-end (teach -> stream -> endpoint ->
# DTW -> commands.tsv marker) and gives W1 an executable contract spec.
STUB_PORT="$(free_port "$((PORT + 1))")"
PYTHONPATH="$HERE" python3 "$HERE/stubdaemon.py" --port "$STUB_PORT" \
    --config "$CONFIG" > "$TMP/stub.log" 2>&1 &
STUB_PID=$!
# authed probe: /health alone is satisfied by ANY squatter on the port —
# /live/state with our key proves the process answering is our stub.
if wait_health "$STUB_PORT" "$KEY"; then
    mkdir -p "$TMP/markers-stub"
    run "replay vs contract-double" \
        python3 "$HERE/replay.py" --port "$STUB_PORT" --key "$KEY" \
            --marker-dir "$TMP/markers-stub" \
            --json-out "$TMP/replay-stub.json" --timeout 3
    kill "$STUB_PID" 2>/dev/null; wait "$STUB_PID" 2>/dev/null
else
    echo "stubdaemon did not come up:"; tail -10 "$TMP/stub.log"
    NAMES+=("replay vs contract-double"); RCS+=(1)
fi

# --- verdict table --------------------------------------------------------
echo
echo "================ e2e-live verdict ================"
overall=0; allskip=1
for i in "${!NAMES[@]}"; do
    rc="${RCS[$i]}"
    [ "$rc" -ne 77 ] && allskip=0
    [ "$rc" -ne 0 ] && [ "$rc" -ne 77 ] && overall=1
    case "$rc" in
        0)  st=PASS ;; 77) st=SKIP ;; *)  st=FAIL ;;
    esac
    printf "  %-4s  %s\n" "$st" "${NAMES[$i]}"
done
echo "================================================="
[ "$allskip" -eq 1 ] && exit 77
exit "$overall"
