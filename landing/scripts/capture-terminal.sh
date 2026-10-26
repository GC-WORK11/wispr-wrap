#!/bin/bash
# capture-terminal.sh — records the REAL output of the quick-start commands
# (git clone, npm install, npm link, wrap start) into landing/src/data/terminal.json.
# Everything runs in a scratch dir under /tmp with a scratch HOME and a private npm
# prefix (nothing global is touched). The daemon uses the first free port in
# 8791-8799 and is always stopped on exit. Re-run: bash landing/scripts/capture-terminal.sh
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$REPO/landing/src/data/terminal.json"
TMP=/tmp/wf-capture-terminal
CLONE="$TMP/wispr-wrap"

# --- pick a free port (never reuse a running daemon) ---
PORT=""
for p in 8791 8792 8793 8794 8795 8796 8797 8798 8799; do
  if ! ss -tln | awk '{print $4}' | grep -Eq "[:.]$p$"; then PORT=$p; break; fi
done
[ -n "$PORT" ] || { echo "no free port in 8791-8799" >&2; exit 1; }

export HOME="$TMP/home"
export npm_config_prefix="$TMP/prefix"
export npm_config_cache="$TMP/npm-cache"
export npm_config_update_notifier=false
export PATH="$TMP/prefix/bin:$PATH"
export WF_PORT="$PORT"

cleanup() {
  [ -x "$CLONE/bin/wrap" ] && "$CLONE/bin/wrap" stop >/dev/null 2>&1
  [ -f "$CLONE/wfd.pid" ] && kill "$(cat "$CLONE/wfd.pid")" 2>/dev/null
}
trap cleanup EXIT

# a previous run's leftovers: stop its daemon (own pidfile only), then start clean
if [ -f "$CLONE/wfd.pid" ]; then kill "$(cat "$CLONE/wfd.pid")" 2>/dev/null; sleep 0.5; fi
rm -rf "$TMP"
mkdir -p "$TMP/home" "$TMP/prefix" "$TMP/steps"
cd "$TMP" || exit 1

# --- 1. clone (progress is forced; \r-redrawn progress keeps only the final line) ---
git clone --progress "$REPO" wispr-wrap > "$TMP/steps/clone.raw" 2>&1
python3 - "$TMP/steps/clone.raw" > "$TMP/steps/clone" <<'PY'
import sys
data = open(sys.argv[1], encoding="utf-8", errors="replace").read()
for line in data.split("\n"):
    if line:
        print(line.split("\r")[-1] if line.split("\r")[-1] else line.rstrip("\r").split("\r")[-1])
PY
COMMIT="$(git -C "$CLONE" rev-parse --short HEAD)"

# --- 2. npm install ---
( cd "$CLONE" && npm install ) > "$TMP/steps/install" 2>&1

# --- 3. npm link (private prefix) ---
( cd "$CLONE" && npm link ) > "$TMP/steps/link" 2>&1
command -v wrap >/dev/null 2>&1 || { echo "wrap not on PATH after npm link" >&2; exit 1; }

# --- 4. wrap start under a pty so isatty() behaves like a real terminal ---
# NOTE: bin/wrap computes ROOT from $0 without resolving symlinks, so through the
# npm-link symlink it looks for server/wfd.py in <prefix>/ and fails ("daemon failed").
# If that happens we record it and run the very same script by its real path instead.
RAN_START="script -qec 'wrap start' /dev/null  (HOME=<scratch>/home WF_PORT=$PORT)"
START_NOTE=""
script -qec "wrap start" /dev/null < /dev/null > "$TMP/steps/start.raw" 2>&1
sleep 0.5
if ! grep -q "scan with your phone camera" "$TMP/steps/start.raw"; then
  START_NOTE="'wrap start' via the npm-link symlink failed: bin/wrap does not resolve symlinks (ROOT=<prefix>); captured by running bin/wrap by real path instead"
  RAN_START="script -qec '<clone>/bin/wrap start' /dev/null  (HOME=<scratch>/home WF_PORT=$PORT)"
  rm -f "$TMP/prefix/wfd.pid" "$TMP/prefix/wfd.log"
  script -qec "$CLONE/bin/wrap start" /dev/null < /dev/null > "$TMP/steps/start.raw" 2>&1
  sleep 0.5
fi
tr -d '\r' < "$TMP/steps/start.raw" > "$TMP/steps/start"

# --- 5. stop + verify nothing is left ---
"$CLONE/bin/wrap" stop > "$TMP/steps/stop" 2>&1
sleep 0.5
LEFT=0
[ -f "$CLONE/wfd.pid" ] && { echo "pidfile still present" >&2; LEFT=1; }
ss -tln | awk '{print $4}' | grep -Eq "[:.]$PORT$" && { echo "port $PORT still listening" >&2; LEFT=1; }
pgrep -f "$CLONE/server/wfd.py" >/dev/null && { echo "daemon process still running" >&2; LEFT=1; }
[ "$LEFT" = 0 ] || exit 1

# --- 6. build terminal.json (+ validation) ---
mkdir -p "$(dirname "$OUT")"
RAN_START="$RAN_START" START_NOTE="$START_NOTE" python3 - "$TMP" "$COMMIT" "$PORT" "$OUT" <<'PY' || exit 1
import json, re, sys, os, datetime
tmp, commit, port, out = sys.argv[1:5]
def rd(n): return open(f"{tmp}/steps/{n}", encoding="utf-8", errors="replace").read()
# privacy: absolute scratch/personal paths -> "~" (only these strings are touched)
rewrites = {}
def scrub(s):
    for p in (tmp, "/workspace", "/home/box"):
        c = s.count(p)
        if c: rewrites[p] = rewrites.get(p, 0) + c; s = s.replace(p, "~")
    return s
start = scrub(rd("start"))
lines = start.split("\n")
idx = next((i for i, l in enumerate(lines) if "scan with your phone camera" in l), None)
m = re.search(r"http://\S+", start)
if idx is None or not m:
    sys.exit("validation failed: no scan line / http link in start output")
qr = lines[:idx]
while qr and not qr[-1].strip(): qr.pop()
while qr and not qr[0].strip(): qr.pop(0)
if len(qr) < 10 or not any("\x1b[" in l for l in qr) and not any(ch in "".join(qr) for ch in "▀▄█"):
    sys.exit("validation failed: no QR block in start output")
link = next(re.search(r"http://\S+\?k=\S+", l).group(0) for l in lines if re.search(r"or open on phone:\s+http://\S+\?k=\S+", l))
steps = [
  ("clone",   "git clone https://github.com/GC-WORK11/wispr-wrap.git", "git clone --progress <local worktree> wispr-wrap"),
  ("install", "npm install", "npm install"),
  ("link",    "npm link", f"npm link  (npm_config_prefix=<scratch>/prefix)"),
  ("start",   "wrap start", os.environ["RAN_START"]),
]
doc = {
  "capturedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
  "commit": commit,
  "steps": [{"id": i, "cmd": c, "ran": r, "out": start if i == "start" else scrub(rd(i))} for i, c, r in steps],
  "startNote": os.environ["START_NOTE"],
  "qr": "\n".join(qr),
  "link": link,
}
json.dump(doc, open(out, "w"), ensure_ascii=False, indent=2); open(out, "a").write("\n")
print(f"wrote {out}: {len(qr)} QR lines, rewrites={rewrites or 'none'}, port={port}, commit={commit}")
PY
