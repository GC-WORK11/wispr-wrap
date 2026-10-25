#!/usr/bin/env bash
# setup.sh — boot the realrun verification stack.
#
#   Xvfb :97 (1280x800) + xfwm4 (a REAL EWMH window manager, 4 workspaces)
#   + the real wfd.py daemon on :8874 with a scratch HOME
#   + real X11 apps as targets (wish SinkEdit text editor, xfce4-terminal)
#   + a PATH-shadow `xinput` (python-xlib XI2 shim — the binary is absent
#     on this box; the shim selects the same XI2 classes on the same
#     server and prints the same text protocol record_action parses)
#
# Writes $REALRUN_STATE/env (mode 600, holds the pairing key — /tmp only,
# NEVER committed). Every verify_*.sh sources it via load_env().
#
# Idempotent: a second call tears down and re-boots. --teardown kills it.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# shellcheck disable=SC1090
. "$HERE/lib.sh"

# Private display: the spec wants :97, but a sibling agent's stack may
# already live there (their windows on a server WE started). A display
# that answers xdpyinfo is FOREIGN-CONTAMINATED — never reuse it; the
# gate's focus/window proofs need an empty, owned display.
if [ -n "${REALRUN_DISPLAY:-}" ]; then
    D="$REALRUN_DISPLAY"
else
    D=""
    for cand in 97 98 99 100 101 102 103 104 105; do
        [ -e "/tmp/.X${cand}-lock" ] && continue
        DISPLAY=":$cand" xdpyinfo >/dev/null 2>&1 && continue
        D=":$cand"; break
    done
    [ -n "$D" ] || die "no free display in :97-:105"
fi
# Private port: first free in 8874..8884 (keeps the spec's 8874 when free).
if [ -n "${REALRUN_PORT:-}" ]; then
    PORT="$REALRUN_PORT"
else
    PORT=""
    for cand in 8874 8875 8876 8877 8878 8879 8880 8881 8882 8883 8884; do
        ss -tln 2>/dev/null | grep -q ":$cand " || { PORT="$cand"; break; }
    done
    [ -n "$PORT" ] || die "no free port in 8874-8884"
fi
WFHOME="$REALRUN_STATE/home"
BINDIR="$REALRUN_STATE/bin"
STATE="$REALRUN_STATE"

mkdir -p "$WFHOME" "$BINDIR" "$REALRUN_PROOFS"

log "boot stack (display $D, port $PORT, home $WFHOME)"

# ---------- teardown (also: --teardown flag) ------------------------------
teardown() {
    log "tearing down"
    for pidf in "$STATE"/pids.*; do
        [ -f "$pidf" ] || continue
        _pid=$(cat "$pidf" 2>/dev/null)
        # 'external' marks a server/WM we found running — never kill those
        [ "$_pid" = "external" ] && { rm -f "$pidf"; continue; }
        [ -n "$_pid" ] && kill "$_pid" 2>/dev/null
        rm -f "$pidf"
    done
    # close only windows on OUR display (sibling agents use the same
    # window names on their own displays — pkill -f would murder theirs)
    for wid in $(DISPLAY="$D" xdotool search --name "^realrun" 2>/dev/null; \
                 DISPLAY="$D" xdotool search --class SinkEdit 2>/dev/null); do
        DISPLAY="$D" wmctrl -ic "$wid" 2>/dev/null
    done
    rm -f "$STATE/env"
}
teardown
[ "${1:-}" = "--teardown" ] && { log "down"; exit 0; }

# ---------- required tools ----------------------------------------------
for t in python3 curl Xvfb xdpyinfo xdotool wmctrl xprop xwininfo xclip ffmpeg setsid ss; do
    req "$t"
done
WM=""
for wm in xfwm4 openbox twm; do
    if have "$wm"; then WM="$wm"; break; fi
done
[ -n "$WM" ] || die "no window manager available (xfwm4/openbox/twm)"

# ---------- xinput shim --------------------------------------------------
cp "$HERE/xinput_shim.py" "$BINDIR/xinput"
chmod +x "$BINDIR/xinput"

# ---------- X server -----------------------------------------------------
if ! DISPLAY="$D" xdpyinfo >/dev/null 2>&1; then
    log "Xvfb $D starting"
    setsid Xvfb "$D" -screen 0 1280x800x24 </dev/null >/dev/null 2>&1 &
    echo $! > "$STATE/pids.xvfb"
    for _ in $(seq 1 50); do
        DISPLAY="$D" xdpyinfo >/dev/null 2>&1 && break; sleep 0.2
    done
    DISPLAY="$D" xdpyinfo >/dev/null 2>&1 || die "Xvfb $D did not come up"
else
    log "reusing live X display $D"
    [ -f "$STATE/pids.xvfb" ] || echo "external" > "$STATE/pids.xvfb"
fi
DISPLAY="$D" xdpyinfo -ext XTEST 2>/dev/null | grep -q XTEST \
    || die "XTEST missing on $D — input injection impossible"

# ---------- window manager ----------------------------------------------
# Verify, don't trust: the check can pass transiently (a sibling agent's
# WM probing the same display) — wait, re-probe, then take over if the
# display is still unmanaged. EWMH focus/desktop proofs REQUIRE a WM.
wm_present() { DISPLAY="$D" xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null \
    | grep -q "window id"; }
if ! wm_present; then
    log "starting WM: $WM"
    DISPLAY="$D" setsid "$WM" --compositor=off >/dev/null 2>&1 || \
        DISPLAY="$D" setsid "$WM" >/dev/null 2>&1 &
    echo $! > "$STATE/pids.wm"
    for _ in $(seq 1 50); do wm_present && break; sleep 0.2; done
else
    # found a WM already managing the display — treat as external, but
    # prove it's stable (two probes a second apart) else take over anyway
    sleep 1
    if wm_present; then
        echo "external" > "$STATE/pids.wm"
    else
        DISPLAY="$D" setsid "$WM" --compositor=off >/dev/null 2>&1 &
        echo $! > "$STATE/pids.wm"
        for _ in $(seq 1 50); do wm_present && break; sleep 0.2; done
    fi
fi
wm_present || die "no window manager on $D — focus/desktop proofs impossible"
log "wm ready: $(DISPLAY="$D" wmctrl -m 2>/dev/null | head -1)"

# ---------- real apps ----------------------------------------------------
SINK="$STATE/sink.txt"
TERMSINK="$STATE/term-sink.txt"
MARKERS="$STATE/markers"; mkdir -p "$MARKERS"
: > "$SINK"; : > "$TERMSINK"

DISPLAY="$D" setsid wish "$HERE/sink.tcl" "$SINK" "realrun-sink" </dev/null >/dev/null 2>&1 &
echo $! > "$STATE/pids.sink"
DISPLAY="$D" setsid xfce4-terminal --title realrun-term \
    -x bash -c "cat > '$TERMSINK'" </dev/null >/dev/null 2>&1 &
echo $! > "$STATE/pids.term"
TERM_WID=""; TERM2_WID=""
for _ in $(seq 1 60); do
    [ -z "$TERM_WID" ] && TERM_WID=$(DISPLAY="$D" xdotool search --class SinkEdit 2>/dev/null | tail -1)
    [ -z "$TERM2_WID" ] && TERM2_WID=$(DISPLAY="$D" xdotool search --name "^realrun-term$" 2>/dev/null | tail -1)
    [ -n "$TERM_WID" ] && [ -n "$TERM2_WID" ] && break
    sleep 0.25
done
[ -n "$TERM_WID" ] || die "SinkEdit editor window never appeared"
[ -n "$TERM2_WID" ] || die "realrun-term window never appeared"

# ---------- daemon -------------------------------------------------------
env -i HOME="$WFHOME" DISPLAY="$D" PATH="$BINDIR:$PATH" \
    WF_PORT="$PORT" WF_NO_LOG_TEXT=1 XDG_SESSION_TYPE=x11 \
    python3 "$ROOT/server/wfd.py" > "$STATE/wfd.log" 2>&1 &
echo $! > "$STATE/pids.wfd"
for _ in $(seq 1 60); do
    curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break; sleep 0.2
done
curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 \
    || { tail -20 "$STATE/wfd.log" >&2; die "wfd did not come up on :$PORT"; }
KEY="$(cat "$WFHOME/.config/wf/key" 2>/dev/null)"
[ -n "$KEY" ] || die "no pairing key at $WFHOME/.config/wf/key"
PAIR_CODE="$(grep -oE 'pair code: [A-Z0-9]+' "$STATE/wfd.log" | awk '{print $3}' | tail -1)"

# ---------- env file -----------------------------------------------------
cat > "$STATE/env" <<EOF
D=$D
PORT=$PORT
BASE=http://127.0.0.1:$PORT
KEY=$KEY
PAIR_CODE=$PAIR_CODE
WFHOME=$WFHOME
SINK=$SINK
TERMSINK=$TERMSINK
MARKERS=$MARKERS
TERM_WID=$TERM_WID
TERM2_WID=$TERM2_WID
STATE=$STATE
EOF
chmod 600 "$STATE/env"
log "daemon up: :$PORT (key + pair code in $STATE/env, mode 600)"

# ---------- capability matrix -------------------------------------------
CAP="$REALRUN_PROOFS/capability.txt"
{
    echo "# realrun capability matrix — $(date -Is)"
    echo "display=$D  wm=$WM  port=$PORT"
    echo
    echo "## X / WM / injection"
    for t in Xvfb xfwm4 openbox twm xdotool wmctrl xprop xwininfo xclip xinput xmodmap ffmpeg xrandr xset xev xmessage wish xfce4-terminal xdg-screensaver xhost; do
        printf "  %-20s %s\n" "$t" "$(command -v "$t" 2>/dev/null || echo MISSING)"
    done
    echo
    echo "## window-manager reality"
    DISPLAY="$D" wmctrl -d 2>/dev/null | sed 's/^/  /'
    echo
    echo "## candidate GUI apps (.desktop-backed)"
    for t in xfce4-terminal thunar google-chrome soffice xmessage xfontsel; do
        printf "  %-20s %s\n" "$t" "$(command -v "$t" 2>/dev/null || echo MISSING)"
    done
    echo
    echo "## system-control backends"
    for t in pactl pulseaudio amixer playerctl brightnessctl light nmcli bluetoothctl rfkill loginctl gsettings gdbus busctl dbus-send copyq clipster import scrot maim gnome-screenshot xfce4-screenshooter spectacle notify-send xflock4; do
        printf "  %-20s %s\n" "$t" "$(command -v "$t" 2>/dev/null || echo MISSING)"
    done
    echo
    echo "## volume probe — pulseaudio null sink"
    if have pulseaudio; then
        if pulseaudio --daemonize -L module-null-sink 2>/dev/null; then
            echo "  pulseaudio daemonized; pactl info:"
            pactl info 2>&1 | sed 's/^/    /' | head -8
        else
            echo "  pulseaudio present but daemonize failed"
        fi
    else
        echo "  pulseaudio: BINARY ABSENT — volume/mute = NEVER-ON-BOX"
    fi
    echo "  wifi/bluetooth: nmcli/bluetoothctl/rfkill all absent = NEVER-ON-BOX"
    echo "  backlight dir: $(ls /sys/class/backlight 2>/dev/null | tr '\n' ' ' || echo empty)"
    echo
    echo "## daemon surface (probed live)"
    for p in /health /pair-info /live/state /bank /act /engine /live/event /live/ping /actions /ops /record /record/status; do
        code=$(curl -s -o /dev/null -w '%{http_code}' -H "X-WF-Key: $KEY" "http://127.0.0.1:$PORT$p")
        printf "  GET %-16s %s\n" "$p" "$code"
    done
} > "$CAP"
sed -i "s/$KEY/<redacted-key>/g" "$CAP"
log "capability matrix -> $CAP"
cat "$CAP"
log "setup complete — run tools/realrun/run_all.sh or verify_*.sh"
