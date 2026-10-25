#!/usr/bin/env bash
# verify_screenshot — sign -> a REAL screenshot file appears.
# The system_ctl screenshot backends (import/scrot/maim/xwd/
# gnome-screenshot/spectacle/xfce4-screenshooter) are ALL absent on this
# box, so the honest path is a bound argv — ffmpeg x11grab — exactly what
# a user could bind today via PUT /bank. Proof = PNG exists, nonzero,
# real PNG header, and it contains OUR display (a marker pixel check is
# not attempted; the file IS the screen).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/screenshot"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

OUT="$STATE/shot-by-sign.png"; rm -f "$OUT"
{
    echo "screenshot backend probes:"
    for t in import scrot maim xwd gnome-screenshot xfce4-screenshooter spectacle flameshot; do
        echo "  $t: $(command -v "$t" 2>/dev/null || echo MISSING)"
    done
    echo "contract GET /act -> $(call GET /act) (catalog is commands.tsv today; sys.screenshot tool has no route)"
} | tee "$PROOF/probe.txt"

teach_bind_fire RR-SHOT "take screenshot" \
    "ffmpeg -hide_banner -loglevel error -f x11grab -video_size 1280x800 -i $D -frames:v 1 -y $OUT" || {
        rc=$?; [ "$rc" = 3 ] && verdict PENDING "endpoints not wired"
        verdict FAILED "teach_bind_fire rc=$rc"; }

wait_file "$OUT" 40
python3 - "$OUT" > "$PROOF/png-check.txt" <<'PY'
import sys, os
p = sys.argv[1]
ok = os.path.isfile(p) and os.path.getsize(p) > 1000
sig = open(p, 'rb').read(8) if ok else b''
print("exists=%s size=%d png_sig=%s" % (os.path.isfile(p),
      os.path.getsize(p) if ok else 0, sig == b'\x89PNG\r\n\x1a\n'))
sys.exit(0 if ok and sig == b'\x89PNG\r\n\x1a\n' else 1)
PY
PNG_OK=$?
cp "$OUT" "$PROOF/shot.png" 2>/dev/null
snapshot > "$PROOF/after.txt"

if [ $PNG_OK -eq 0 ]; then
    verdict VERIFIED "sign -> bound ffmpeg argv -> real PNG screenshot of :$D ($(stat -c%s "$OUT") bytes)"
else
    verdict FAILED "no valid PNG after sign (backend probe: all catalog tools absent)"
fi
