#!/usr/bin/env bash
# verify_send_media — the ORIGINAL wispr-wrap phone->laptop media sends,
# proven against the real daemon on a real X display:
#   POST /send-image (PNG bytes) -> clipboard really carries image/png
#     (the wfclip.py inject job + Ctrl+V path), and
#   POST /send-file (bytes + X-File-Name) -> a real file under
#     ~/wf-uploads with byte-identical content.
# A Tk text sink cannot render an image paste, so the clipboard TARGETS
# advertisement + upload file are the honest OS-state assertions.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/send_media"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

# --- auth gate --------------------------------------------------------------
bad=$(call_noauth POST /send-image "$(head -c 64 /dev/urandom | base64 -w0)")
echo "unauthed /send-image -> $bad (expect 403)" | tee "$PROOF/auth-gate.txt"

# --- pair (real contract) ---------------------------------------------------
info=$(curl -sf "$BASE/pair-info") || { verdict UNVERIFIABLE "no /pair-info"; }
PC="$(echo "$info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')"
PAIR_CODE="$PC"
sleep 1.1
call POST /pair "{\"code\":\"$PC\"}" >/dev/null
[ "$CALL_CODE" = 200 ] && [ "$(call_json key)" = "$KEY" ] \
    || { verdict FAILED "pair failed ($CALL_CODE)"; }

focus_term || verdict FAILED "could not focus the editor window"

# --- image send -------------------------------------------------------------
# a real (synthetic, no people) PNG generated on the spot
IMG="$PROOF/send.png"
python3 - "$IMG" <<'PY'
import struct, sys, zlib
w, h = 64, 48
rows = b""
for y in range(h):
    rows += b"\x00" + bytes((x * 4 % 256 for x in range(w))) + \
            bytes((y * 5 % 256 for _ in range(w))) + b"\xff" * w
# minimal truecolor PNG: IHDR + IDAT(raw scanlines) + IEND
raw = b"".join(b"\x00" + b"".join(
    bytes((x * 4 % 256, y * 5 % 256, 128)) for x in range(w))
    for y in range(h))
def chunk(t, d):
    c = t + d
    return struct.pack(">I", len(d)) + c + struct.pack(">I",
                                                      zlib.crc32(c) & 0xffffffff)
png = b"\x89PNG\r\n\x1a\n" + \
      chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
      chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
open(sys.argv[1], "wb").write(png)
PY
IMG_SHA=$(sha256sum "$IMG" | cut -d' ' -f1)
call POST /send-image --data-binary "@$IMG" \
    -H "Content-Type: image/png" >/dev/null 2>&1 || true
# call() builds a JSON post; for binary we curl directly:
CURL_CODE=$(curl -s -o "$PROOF/img-resp.json" -w "%{http_code}" \
    -X POST "$BASE/send-image" -H "X-WF-Key: $KEY" \
    -H "Content-Type: image/png" --data-binary "@$IMG")
echo "POST /send-image -> $CURL_CODE $(cat "$PROOF/img-resp.json")"
[ "$CURL_CODE" = 200 ] || verdict FAILED "send-image $CURL_CODE"
grep -q '"ok": *true\|"ok":true' "$PROOF/img-resp.json" \
    || verdict FAILED "send-image not ok: $(cat "$PROOF/img-resp.json")"

# the inject job puts the PNG on the X clipboard (~1.2s, then restores):
# poll TARGETS quickly — image/png must really be advertised
clip_ok=""
for i in $(seq 1 60); do
    tg=$(DISPLAY="$D" xclip -selection clipboard -o -t TARGETS 2>/dev/null)
    if echo "$tg" | grep -q "image/png"; then clip_ok=1; break; fi
    sleep 0.05
done
echo "$tg" > "$PROOF/clipboard-targets.txt"
[ -n "$clip_ok" ] || { shot "$PROOF/after.png"; \
    verdict FAILED "clipboard never advertised image/png"; }
echo "clipboard advertised image/png (real X clipboard state)"

# --- file send ---------------------------------------------------------------
PAYLOAD="realrun-media-file-$(date +%s).txt"
BODY="realrun file-send $(date -Is) — phone -> laptop upload proof"
printf '%s' "$BODY" > "$PROOF/$PAYLOAD"
CURL_CODE=$(curl -s -o "$PROOF/file-resp.json" -w "%{http_code}" \
    -X POST "$BASE/send-file" -H "X-WF-Key: $KEY" \
    -H "X-File-Name: $PAYLOAD" -H "Content-Type: application/octet-stream" \
    --data-binary "@$PROOF/$PAYLOAD")
echo "POST /send-file -> $CURL_CODE $(cat "$PROOF/file-resp.json")"
[ "$CURL_CODE" = 200 ] || verdict FAILED "send-file $CURL_CODE"
FPATH=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["path"])' \
        "$PROOF/file-resp.json")
# daemon reports a path inside ITS home ($WFHOME); the response path is on
# the daemon's view — the uploads dir lives under the scratch HOME.
FOUND=""
for i in $(seq 1 30); do
    FOUND=$(find "$WFHOME" -name "*$PAYLOAD" -type f 2>/dev/null | head -1)
    [ -n "$FOUND" ] && break; sleep 0.2
done
[ -n "$FOUND" ] || verdict FAILED "uploaded file never appeared under $WFHOME"
if cmp -s "$PROOF/$PAYLOAD" "$FOUND"; then
    echo "file landed at $FOUND, bytes identical" | tee "$PROOF/file-ok.txt"
else
    verdict FAILED "uploaded file content differs ($FOUND)"
fi

shot "$PROOF/after.png"
verdict VERIFIED "send-image put image/png on the real X clipboard; send-file wrote byte-identical file to ~/wf-uploads"
