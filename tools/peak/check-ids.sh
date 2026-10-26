#!/usr/bin/env bash
# check-ids.sh — the DOM contract guard. PRO's demo kit, tests/selftest.html and
# the realrun verifiers address the app by element id; a restyle must never
# drop one. Compares ids defined in web/ now against a base ref (default: the
# commit this branch forked the UI from).
# usage: tools/peak/check-ids.sh [base-ref]
set -u
cd "$(dirname "$0")/../.."
BASE="${1:-origin/devin-pro/app}"
ids() { # stdin: concatenated sources -> sorted unique ids
  grep -oE "id=\"[A-Za-z][A-Za-z0-9_-]*\"|id=\\\\?\"[A-Za-z][A-Za-z0-9_-]*|\.id *= *'[A-Za-z][A-Za-z0-9_-]*'|id: *'[A-Za-z][A-Za-z0-9_-]*'" \
   | grep -oE "[A-Za-z][A-Za-z0-9_-]*['\"]?$" | tr -d "'\"" | sort -u
}
old=$( (git show "$BASE:web/index.html"; for f in $(git ls-tree -r --name-only "$BASE" web/js); do git show "$BASE:$f"; done) | ids)
new=$( (cat web/index.html web/js/*.js) | ids)
missing=$(comm -23 <(echo "$old") <(echo "$new"))
echo "ids at $BASE: $(echo "$old" | wc -l)   ids now: $(echo "$new" | wc -l)"
if [ -n "$missing" ]; then echo "MISSING IDS (would break tooling):"; echo "$missing"; exit 1; fi
echo "OK: every id from $BASE still exists"
