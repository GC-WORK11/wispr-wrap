#!/bin/bash
# fetch-vendor — download + verify vendored MediaPipe artifacts.
# Runs at dev/packaging time only; the daemon and composer never fetch at runtime.
#
#   tools/fetch-vendor.sh          fetch missing/mismatched files, verify all hashes
#   tools/fetch-vendor.sh --check  verify only (offline-safe), nonzero exit on drift
#
# Sources + pins live in web/vendor/mediapipe/MANIFEST:
#   <sha256>  <relpath under web/vendor/mediapipe>  <url>
# Lines starting with # are ignored (documented-but-not-vendored files).
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VDIR="$ROOT/web/vendor/mediapipe"
MANIFEST="$VDIR/MANIFEST"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

have() { command -v "$1" >/dev/null 2>&1; }
have curl || { echo "needs curl"; exit 1; }

sha() { sha256sum "$1" | cut -d' ' -f1; }

fail=0
while read -r want path url; do
    [ -z "${want:-}" ] && continue
    case "$want" in \#*) continue;; esac
    dest="$VDIR/$path"
    if [ -f "$dest" ] && [ "$(sha "$dest")" = "$want" ]; then
        echo "ok      $path"
        continue
    fi
    if [ "$CHECK_ONLY" = 1 ]; then
        if [ -f "$dest" ]; then echo "BADHASH $path"; else echo "missing $path"; fi
        fail=1
        continue
    fi
    mkdir -p "$(dirname "$dest")"
    tmp="$dest.dl.$$"
    echo "fetch   $path"
    if ! curl -sfL --retry 3 -o "$tmp" "$url"; then
        echo "FAILED  $path (download) — $url"
        rm -f "$tmp"; fail=1; continue
    fi
    if [ "$(sha "$tmp")" != "$want" ]; then
        echo "FAILED  $path (sha256 mismatch — aborting, file left out)"
        rm -f "$tmp"; fail=1; continue
    fi
    chmod 644 "$tmp"
    mv "$tmp" "$dest"
    echo "ok      $path"
done < "$MANIFEST"

exit "$fail"
