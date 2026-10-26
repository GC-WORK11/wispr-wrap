#!/usr/bin/env bash
# run.sh - boot an isolated real stack, capture the landing media, tear everything down.
#   bash landing/scripts/capture/run.sh
# Needs node >= 20, google-chrome, ffmpeg (libx264, libwebp), Xvfb, xfwm4, xdotool, wish, xfce4-terminal.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"; cd "$ROOT"
export REALRUN_DISPLAY="${REALRUN_DISPLAY:-:310}" REALRUN_PORT="${REALRUN_PORT:-9310}" REALRUN_PROOFS="${REALRUN_PROOFS:-/tmp/agy-rr-proofs}"
export CAP_CDP="${CAP_CDP:-9316}" CAP_WORK="${CAP_WORK:-/tmp/agy-cap}"
export CAP_ENV="/tmp/wf-realrun-$(basename "$ROOT" | tr -cd 'a-zA-Z0-9-')/env"
export SITE_FAKE_CAM=/tmp/site-fakecam.y4m
# neutral fake camera clip (a dim noisy frame, no person), same recipe as tools/site/run-all.sh
[ -f "$SITE_FAKE_CAM" ] || ffmpeg -y -loglevel error -f lavfi -i "color=c=0x3b3834:s=640x480:r=15:d=2" \
  -vf "noise=alls=8:allf=t,vignette=PI/4.5,format=yuv420p" "$SITE_FAKE_CAM"
teardown() { bash tools/realrun/setup.sh --teardown >/dev/null 2>&1; }
trap teardown EXIT
bash tools/realrun/setup.sh >/tmp/agy-rr-setup.log 2>&1 || { echo "stack failed: see /tmp/agy-rr-setup.log"; exit 2; }
node --experimental-websocket "$HERE/app.mjs"
