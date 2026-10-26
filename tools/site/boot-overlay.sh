#!/usr/bin/env bash
# boot-overlay.sh — a SECOND real daemon for the SITE lane: this worktree's web/ + server/, with
# Devin PRO's not-yet-merged backend (server/packs.py, server/teach_train.py and the wfd.py with
# GET /packs, POST /packs/install, teach_end.train) overlaid from /workspace/wispr-wrap-merge.
# Real daemon, real X11 (reuses the display + SinkEdit from tools/realrun/setup.sh), scratch HOME.
# usage: bash tools/site/boot-overlay.sh [port=9011]   -> writes /tmp/wf-site-overlay/env (mode 600)
set -eu
OPORT="${1:-9011}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PRO=/workspace/wispr-wrap-merge/server
BASEENV=/tmp/wf-realrun-wispr-wrap-site/env
[ -f "$BASEENV" ] || { echo "boot the realrun stack first (tools/realrun/setup.sh)" >&2; exit 2; }
. "$BASEENV"; D0="$D"; SINK0="$SINK"; TERM0="$TERM_WID"; TERMSINK0="$TERMSINK"; MARK0="$MARKERS"; BIN0="$STATE/bin"
OV=/tmp/wf-site-overlay
if [ -f "$OV/pid" ]; then kill "$(cat "$OV/pid")" 2>/dev/null || true; sleep 0.5; fi
rm -rf "$OV"; mkdir -p "$OV/home" "$OV/srv"
cp -r "$ROOT/server" "$OV/srv/server"
for f in wfd.py packs.py teach_train.py; do [ -f "$PRO/$f" ] && cp "$PRO/$f" "$OV/srv/server/$f"; done
# OVERLAY_REF=<git ref>: use that ref's whole server/ (read-only extract, nothing is merged into this branch)
if [ -n "${OVERLAY_REF:-}" ]; then
  rm -rf "$OV/srv/server"; git -C "$ROOT" archive "$OVERLAY_REF" server | tar -x -C "$OV/srv"
fi
ln -s "$ROOT/web" "$OV/srv/web"
[ -d "$ROOT/tools" ] && ln -s "$ROOT/tools" "$OV/srv/tools" || true
env -i HOME="$OV/home" DISPLAY="$D0" PATH="$BIN0:$PATH" WF_PORT="$OPORT" WF_NO_LOG_TEXT=1 XDG_SESSION_TYPE=x11 \
  python3 "$OV/srv/server/wfd.py" > "$OV/wfd.log" 2>&1 &
echo $! > "$OV/pid"
for _ in $(seq 1 60); do curl -sf "http://127.0.0.1:$OPORT/health" >/dev/null 2>&1 && break; sleep 0.2; done
curl -sf "http://127.0.0.1:$OPORT/health" >/dev/null || { tail -20 "$OV/wfd.log" >&2; echo "overlay daemon did not start" >&2; exit 3; }
for _ in $(seq 1 50); do [ -s "$OV/home/.config/wf/key" ] && break; sleep 0.2; done
KEY2="$(cat "$OV/home/.config/wf/key")"
cat > "$OV/env" <<EOT
D=$D0
PORT=$OPORT
BASE=http://127.0.0.1:$OPORT
KEY=$KEY2
WFHOME=$OV/home
SINK=$SINK0
TERMSINK=$TERMSINK0
MARKERS=$MARK0
TERM_WID=$TERM0
STATE=$OV
EOT
chmod 600 "$OV/env"
echo "overlay daemon up on :$OPORT (env: $OV/env). packs: $(curl -s -H "X-WF-Key: $KEY2" http://127.0.0.1:$OPORT/packs | head -c 120)"
