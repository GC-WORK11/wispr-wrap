#!/bin/bash
# cut.sh — split /tmp/demo/raw.mp4 into per-chapter clips + demo.mp4.
#
#   ./cut.sh            # uses /tmp/demo/chapters.tsv written by drive.py
#
# Reads chapters.tsv (name <TAB> seconds-since-rec-start <TAB> note),
# cuts [mark, next-mark) per chapter with a small tail pad, re-encodes
# (keyframe-exact cuts), concatenates everything to demo.mp4, and writes
# CLIPS.tsv with real durations.
#
# Outputs (duplicated):
#   /tmp/demo/clips/*.mp4, /tmp/demo/demo.mp4, /tmp/demo/CLIPS.tsv
#   <repo>/demo/rough-cuts/*.mp4, <repo>/demo/demo.mp4, <repo>/demo/CLIPS.tsv
# (repo demo/ is gitignored — videos stay local.)
set -u
cd "$(dirname "$0")"
DEMO=/tmp/demo
RAW=$DEMO/raw.mp4
CH=$DEMO/chapters.tsv
CL=$DEMO/clips
REPO_DEMO="$(cd ../.. && pwd)/demo"
RCL=$REPO_DEMO/rough-cuts

[ -f "$RAW" ] || { echo "no $RAW"; exit 1; }
[ -f "$CH" ] || { echo "no $CH — run drive.py first"; exit 1; }
mkdir -p "$CL" "$RCL"
rm -f "$CL"/ch-*.mp4 "$CL"/list.txt "$DEMO/CLIPS.tsv" "$RCL"/*.mp4

# read marks
declare -a NAMES TIMES NOTES
while IFS=$'\t' read -r name t note; do
  [ -n "$name" ] || continue
  NAMES+=("$name"); TIMES+=("$t"); NOTES+=("${note:-}")
done < "$CH"
TOTAL=${#NAMES[@]}
[ "$TOTAL" -ge 1 ] || { echo "no chapters"; exit 1; }

DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$RAW")

pad=0.6
for i in "${!NAMES[@]}"; do
  s=${TIMES[$i]}
  if [ "$i" -lt "$((TOTAL - 1))" ]; then
    e=${TIMES[$((i + 1))]}
  else
    e=$DUR
  fi
  e=$(python3 -c "print(min(float('$e') + $pad, float('$DUR')))")
  s=$(python3 -c "print(max(0.0, float('$s') - 0.4))")
  out="$CL/ch-$(printf '%02d' "$i")-${NAMES[$i]}.mp4"
  ffmpeg -v error -ss "$s" -to "$e" -i "$RAW" \
    -c:v libx264 -preset fast -crf 22 -pix_fmt yuv420p "$out" || {
      echo "cut failed: ${NAMES[$i]}"; continue; }
  d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$out")
  printf "%s\t%.1fs\t%s\n" "$(basename "$out")" "$d" "${NOTES[$i]}" \
    >> "$DEMO/CLIPS.tsv"
  echo "file '$out'" >> "$CL/list.txt"
done

ffmpeg -v error -f concat -safe 0 -i "$CL/list.txt" -c copy \
  "$DEMO/demo.mp4" || { echo "concat failed"; exit 1; }
D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$DEMO/demo.mp4")
echo "demo.mp4 = ${D}s"
python3 -c "import sys; d=float('$D'); sys.exit(0 if d < 300 else 1)" \
  || echo "WARN: demo.mp4 exceeds 5 minutes"

cp -f "$CL"/ch-*.mp4 "$RCL/" 2>/dev/null
cp -f "$DEMO/demo.mp4" "$REPO_DEMO/demo.mp4"
cp -f "$DEMO/CLIPS.tsv" "$REPO_DEMO/CLIPS.tsv"
echo "--- clips ---"; cat "$DEMO/CLIPS.tsv"
echo "repo copy -> $REPO_DEMO (rough-cuts/ + demo.mp4)"
