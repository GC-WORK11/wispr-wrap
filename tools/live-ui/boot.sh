#!/usr/bin/env bash
# boot.sh — a private, real stack for the Go Live proofs (Kiro LIVE lane).
#
#   Xvfb (own display, 1280x800) + xfwm4 (a real EWMH window manager)
#   + a real editor window (Tk "Notes", WM_CLASS SinkEdit, buffer mirrored to a file)
#   + the real daemon (server/wfd.py) on its own port with a scratch HOME
#   + a fake camera feed file (abstract soft-focus interior; no people, never committed)
#
# The daemon serves web/ from a scratch "serve root" so the page under test is
# exactly: this tree's server/ + a chosen web/ tree (default: this tree's web/).
#   LIVE_WEB_SRC=/path/to/web   serve that web/ instead (e.g. a sibling lane's
#                               working copy), with THIS tree's js/live.js and
#                               css/live.css overlaid (symlinks, live-edited).
#
# Writes $LIVE_STATE/env (mode 600; holds the pairing key; /tmp only, NEVER
# committed). --teardown kills only what this script started.
#
# Ports: daemon 8960 (first free in 8960-8964). Display: first free in :121-:129.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
STATE="${LIVE_STATE:-/tmp/kiro-live}"
SERVE="$STATE/serve"
WFHOME="$STATE/home"
mkdir -p "$STATE"
chmod 700 "$STATE"

log() { printf '[live-ui] %s\n' "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }

teardown() {
    # the laptop's Chrome was started by the daemon inside our scratch HOME; its
    # main process runs as /usr/bin/google-chrome-stable with our marker switch
    # (anchored, so this can never match a shell whose command line mentions it)
    pkill -f "^/usr/bin/google-chrome-stable .*(--kiro-live-laptop|--remote-debugging-port=8962)" 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        pgrep -f "^/usr/bin/google-chrome-stable .*(--kiro-live-laptop|--remote-debugging-port=8962)" >/dev/null || break
        sleep 0.3
    done
    for pidf in "$STATE"/pids.*; do
        [ -f "$pidf" ] || continue
        pid="$(cat "$pidf" 2>/dev/null)"
        [ -n "$pid" ] && kill "$pid" 2>/dev/null
        rm -f "$pidf"
    done
    sleep 0.3
    rm -f "$STATE/env"
}
teardown
if [ "${1:-}" = "--teardown" ]; then log "down"; exit 0; fi

for t in python3 curl Xvfb xdpyinfo xdotool wmctrl xfwm4 wish ffmpeg ss google-chrome; do
    command -v "$t" >/dev/null 2>&1 || die "missing tool: $t"
done

# ---------- display + port (never reuse someone else's) ----------
D=""
for n in 121 122 123 124 125 126 127 128 129; do
    [ -e "/tmp/.X$n-lock" ] && continue
    [ -e "/tmp/.X11-unix/X$n" ] && continue
    D=":$n"; break
done
[ -n "$D" ] || die "no free display in :121-:129"
PORT=""
for p in 8960 8961 8962 8963 8964; do
    ss -tln 2>/dev/null | grep -qE "[:.]$p\b" || { PORT="$p"; break; }
done
[ -n "$PORT" ] || die "no free port in 8960-8964"

# ---------- serve root: server/ from this tree, web/ from LIVE_WEB_SRC ----------
rm -rf "$SERVE"; mkdir -p "$SERVE"
ln -s "$ROOT/server" "$SERVE/server"
WEB_SRC="${LIVE_WEB_SRC:-$ROOT/web}"
[ -f "$WEB_SRC/index.html" ] || die "no index.html in $WEB_SRC"
if [ "$WEB_SRC" = "$ROOT/web" ]; then
    ln -s "$ROOT/web" "$SERVE/web"
else
    mkdir -p "$SERVE/web"
    # copy the shell (small), link the vendored model dir (big, read-only)
    (cd "$WEB_SRC" && find . -path ./vendor -prune -o -type f -print) | while read -r f; do
        mkdir -p "$SERVE/web/$(dirname "$f")"; cp "$WEB_SRC/$f" "$SERVE/web/$f"
    done
    ln -s "$WEB_SRC/vendor" "$SERVE/web/vendor"
    ln -sf "$ROOT/web/js/live.js" "$SERVE/web/js/live.js"
    ln -sf "$ROOT/web/css/live.css" "$SERVE/web/css/live.css"
fi

# ---------- fake camera feed (abstract, no people; regenerated, never committed) ----------
# 4:2:0 matters: Chrome's file-backed fake camera shows solid green for 4:4:4 MJPEG.
CAM="$STATE/cam.mjpeg"
if [ ! -s "$CAM" ]; then
    ffmpeg -hide_banner -loglevel error -y -f lavfi \
        -i "gradients=s=720x960:r=15:d=10:c0=0x1f1d1a:c1=0x6f675b:c2=0x9c9384:c3=0x3b3832:c4=0x57524a:c5=0x2a2724:nb_colors=6:speed=0.006:seed=21:type=linear:x0=60:y0=40:x1=680:y1=920" \
        -vf "gblur=sigma=30,noise=alls=6:allf=t,vignette=angle=PI/3.2,eq=saturation=0.9:gamma=1.05,format=yuvj420p" \
        -pix_fmt yuvj420p -q:v 4 -f mjpeg "$CAM" || die "ffmpeg could not make the fake camera feed"
fi

# ---------- X server + WM ----------
setsid Xvfb "$D" -screen 0 1280x800x24 -nolisten tcp </dev/null >/dev/null 2>&1 &
echo $! > "$STATE/pids.xvfb"
for _ in $(seq 1 50); do DISPLAY="$D" xdpyinfo >/dev/null 2>&1 && break; sleep 0.2; done
DISPLAY="$D" xdpyinfo >/dev/null 2>&1 || die "Xvfb $D did not come up"
DISPLAY="$D" setsid xfwm4 --compositor=off </dev/null >/dev/null 2>&1 &
echo $! > "$STATE/pids.wm"
for _ in $(seq 1 50); do
    DISPLAY="$D" xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null | grep -q "window id" && break; sleep 0.2
done
# a calm desktop colour so screenshots of the laptop read clearly
DISPLAY="$D" xsetroot -solid '#e9e7e3' 2>/dev/null || true

# ---------- the laptop's editor (where Text mode types) ----------
SINK="$STATE/notes.txt"; : > "$SINK"
DISPLAY="$D" setsid wish "$HERE/sink.tcl" "$SINK" "Notes" </dev/null >/dev/null 2>&1 &
echo $! > "$STATE/pids.sink"
SINK_WID=""
for _ in $(seq 1 60); do
    SINK_WID="$(DISPLAY="$D" xdotool search --class SinkEdit 2>/dev/null | tail -1)"
    [ -n "$SINK_WID" ] && break; sleep 0.25
done
[ -n "$SINK_WID" ] || die "editor window never appeared"
DISPLAY="$D" xdotool windowsize "$SINK_WID" 760 460 windowmove "$SINK_WID" 60 60 2>/dev/null || true

# ---------- daemon (scratch HOME, own port, no text in logs) ----------
rm -rf "$WFHOME"; mkdir -p "$WFHOME"

# ---------- the laptop's Chrome (what "C" opens and "V" opens YouTube in) ----------
# This box has no user-namespace sandbox and no dbus, so Chrome needs --no-sandbox;
# a PATH-shadow wrapper keeps the daemon's real launch path (the .desktop Exec ->
# `google-chrome` on PATH, and xdg-open for URLs). The profile is seeded like a
# laptop where Chrome has been used before (no first-run / default-browser dialogs).
# --kiro-live-laptop is an inert marker so teardown can find this Chrome. Nothing
# instruments it: the proofs read the laptop's screen and window titles only.
BIN="$STATE/bin"; rm -rf "$BIN"; mkdir -p "$BIN"
cat > "$BIN/google-chrome" <<'CHR'
#!/bin/sh
exec /usr/bin/google-chrome-stable --no-sandbox --no-first-run --no-default-browser-check \
  --disable-dev-shm-usage --password-store=basic --disable-features=Translate,DefaultBrowserPromptRefresh \
  --start-maximized --lang=en-US --kiro-live-laptop "$@"
CHR
chmod +x "$BIN/google-chrome"
mkdir -p "$WFHOME/.config/google-chrome/Default" "$WFHOME/.local/share/applications"
: > "$WFHOME/.config/google-chrome/First Run"
printf '%s' '{"browser":{"enabled_labs_experiments":[]}}' > "$WFHOME/.config/google-chrome/Local State"
printf '%s' '{"browser":{"has_seen_welcome_page":true,"check_default_browser":false},"profile":{"exit_type":"Normal","exited_cleanly":true},"session":{"restore_on_startup":5}}' > "$WFHOME/.config/google-chrome/Default/Preferences"
cat > "$WFHOME/.local/share/applications/google-chrome.desktop" <<DSK
[Desktop Entry]
Version=1.0
Name=Google Chrome
Exec=$BIN/google-chrome %U
Type=Application
Terminal=false
MimeType=text/html;x-scheme-handler/http;x-scheme-handler/https;
DSK
printf '[Default Applications]\nx-scheme-handler/http=google-chrome.desktop\nx-scheme-handler/https=google-chrome.desktop\ntext/html=google-chrome.desktop\n' > "$WFHOME/.config/mimeapps.list"
env -i HOME="$WFHOME" DISPLAY="$D" PATH="$BIN:/usr/bin:/bin" xdg-settings set default-web-browser google-chrome.desktop 2>/dev/null || true

env -i HOME="$WFHOME" DISPLAY="$D" PATH="$BIN:/usr/local/bin:/usr/bin:/bin" \
    WF_PORT="$PORT" WF_NO_LOG_TEXT=1 XDG_SESSION_TYPE=x11 LANG=C.UTF-8 \
    python3 "$SERVE/server/wfd.py" > "$STATE/wfd.log" 2>&1 &
echo $! > "$STATE/pids.wfd"
for _ in $(seq 1 80); do
    curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break; sleep 0.2
done
curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 || { tail -20 "$STATE/wfd.log" >&2; die "daemon did not come up on :$PORT"; }
KEY="$(cat "$WFHOME/.config/wf/key" 2>/dev/null)"
[ -n "$KEY" ] || die "no pairing key in scratch HOME"

umask 077
cat > "$STATE/env" <<EOF
D=$D
PORT=$PORT
BASE=http://127.0.0.1:$PORT
KEY=$KEY
WFHOME=$WFHOME
SINK=$SINK
SINK_WID=$SINK_WID
CAM=$CAM
SERVE=$SERVE
WEB_SRC=$WEB_SRC
EOF
chmod 600 "$STATE/env"
log "up: display $D, daemon :$PORT, web from $WEB_SRC, state $STATE"
