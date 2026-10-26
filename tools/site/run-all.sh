#!/usr/bin/env bash
# run-all.sh — every SITE proof, real daemon + real Chrome + real X11, one command.
#   bash tools/site/run-all.sh            # boots what is missing, runs everything, prints a table
#   ONLY="tiles quick" bash tools/site/run-all.sh
# Needs: node >= 20, python3, google-chrome, xdotool, wmctrl, xclip, ffmpeg, Xvfb (all on a normal dev box).
# Proofs land in proofs/ui-v2/site-* (390x844@2x PNGs, RESULT.txt, a few mp4s). Nothing here touches the network.
# Hands are synthetic keypoint clips and the camera is a fake device; everything else is the real product.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$ROOT"
PORT="${SITE_PORT:-9010}"; OPORT="${SITE_OVERLAY_PORT:-9011}"; DISP="${SITE_DISPLAY:-:210}"
export SITE_CDP="${SITE_CDP:-9016}"
[ -f /tmp/site-fakecam.y4m ] || ffmpeg -y -loglevel error -f lavfi -i "color=c=0x3b3834:s=640x480:r=15:d=2" -vf "noise=alls=8:allf=t,vignette=PI/4.5,format=yuv420p" /tmp/site-fakecam.y4m
export SITE_FAKE_CAM=/tmp/site-fakecam.y4m
if ! curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  REALRUN_DISPLAY="$DISP" REALRUN_PORT="$PORT" REALRUN_PROOFS=/tmp/site-rr-proofs bash tools/realrun/setup.sh >/tmp/site-rr-setup.log 2>&1 || { echo "stack failed: see /tmp/site-rr-setup.log"; exit 2; }
fi
if ! curl -sf "http://127.0.0.1:$OPORT/health" >/dev/null 2>&1; then
  OVERLAY_REF="${OVERLAY_REF:-origin/devin/live-merge}" bash tools/site/boot-overlay.sh "$OPORT" >/dev/null || { echo "overlay daemon failed"; exit 2; }
fi
OV="env SITE_ENV=/tmp/wf-site-overlay/env"
declare -A CMD=(
  [tiles]="$OV node --experimental-websocket tools/site/flow-tiles.mjs"
  [packs]="$OV node --experimental-websocket tools/site/flow-packs.mjs"
  [quick]="$OV node --experimental-websocket tools/site/flow-quick.mjs"
  [teach]="$OV node --experimental-websocket tools/site/flow-teach.mjs"
  [picker]="$OV SITE_CDP=9017 node --experimental-websocket tools/site/flow-picker.mjs"
  [boot]="env SITE_CDP=9019 node --experimental-websocket tools/site/flow-boot.mjs"
  [logo]="python3 tools/site/logo/checks-logo.py"
  [offline]="$OV node --experimental-websocket tools/site/flow-offline.mjs"   # stops the overlay daemon: keep it last
)
ORDER=(tiles packs quick teach picker boot logo offline)
printf '%-8s %5s %5s\n' flow PASS FAIL
for n in "${ORDER[@]}"; do
  [ -n "${ONLY:-}" ] && [[ " $ONLY " != *" $n "* ]] && continue
  log="/tmp/site-run-$n.log"; ${CMD[$n]} >"$log" 2>&1
  printf '%-8s %5s %5s\n' "$n" "$(grep -c '^PASS' "$log")" "$(grep -c '^FAIL' "$log")"
  grep '^FAIL' "$log" | cut -c1-200 | sed 's/^/    /'
done
