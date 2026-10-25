#!/usr/bin/env bash
# verify_picker — the "@" picker path end to end: the catalog the composer
# actually reads (GET /act) is turned into the SAME action dict web/js/
# actions.js builds, bound to a taught sign, and fired through the real
# daemon -> action provider -> OS. Two catalog surfaces are covered:
#   1. an .desktop app row (kind:app, tool:app.open) -> a NEW real window
#   2. a builtin tool row (kind:builtin, tool:type.text) -> real typing
#      into the focused editor
# No implementation function is called: binding is PUT /bank, firing is
# /live/kp, effects are observed on the X server / sink file.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/picker"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

APP_ID=xfce4-terminal
TYPE_TXT="PICKER-TYPE-OK"

# --- the catalog the @ picker reads --------------------------------------
call GET /act >/dev/null
code=$CALL_CODE
cp "$CALL_BODY" "$PROOF/catalog.json"
[ "$code" = 200 ] || verdict FAILED "GET /act -> $code"
python3 - "$PROOF/catalog.json" "$APP_ID" <<'PY' | tee "$PROOF/catalog.txt"
import json, sys
j = json.load(open(sys.argv[1])); app_id = sys.argv[2]
apps = j.get("apps") or []
tools = j.get("tools") or []
assert apps, "catalog has no apps"
assert any(t.get("id") == "app.open" for t in tools), "no app.open tool"
assert any(t.get("id") == "type.text" for t in tools), "no type.text tool"
assert any(a.get("id") == app_id for a in apps), "app %s not in catalog" % app_id
print("catalog: %d apps, %d tools, provider=%s, display=%s" %
      (len(apps), len(tools), j.get("provider"), j.get("display")))
PY
[ $? -eq 0 ] || verdict UNVERIFIABLE "catalog missing app/tool rows this box needs"

# --- 1. app row -> open a real window ------------------------------------
python3 - "$PROOF/catalog.json" "$APP_ID" "$STATE/pick-app.json" <<'PY'
import json, sys
cat = json.load(open(sys.argv[1])); app_id = sys.argv[2]
app = next(a for a in cat["apps"] if a.get("id") == app_id)
# exact shape built by web/js/actions.js::appAction()
act = {"kind": "app", "tool": "app.open", "type": "open_app",
       "args": {"id": app["id"]}, "id": app["id"],
       "label": "open " + (app.get("name") or app["id"]), "tier": 1}
json.dump({"name": "RR-PICK", "takes": 1, "label": act["label"],
           "action": act}, open(sys.argv[3], "w"))
PY
BEFORE=$(DISPLAY="$D" xdotool search --class "$APP_ID" 2>/dev/null | wc -l)
call POST /teach/begin "@$STATE/pick-app.json" >/dev/null
[ "$CALL_CODE" = 200 ] || verdict FAILED "teach/begin picker app -> $CALL_CODE"
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 teach:RR-PICK >/dev/null
sleep 0.3
call POST /teach/end '{}' >/dev/null
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 palm >/dev/null 2>&1
sleep 0.3
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 sign:RR-PICK >/dev/null 2>&1
for i in $(seq 1 40); do
    HAVE=$(DISPLAY="$D" xdotool search --class "$APP_ID" 2>/dev/null | wc -l)
    [ "$HAVE" -gt "$BEFORE" ] && break
    sleep 0.25
done
call GET /live/state >/dev/null
cp "$CALL_BODY" "$PROOF/state-app.json"
DISPLAY="$D" wmctrl -l > "$PROOF/windows.txt"
echo "app: $APP_ID windows before=$BEFORE after=$HAVE" | tee "$PROOF/app.txt"

# --- 2. builtin tool row -> real typing into the focused editor ----------
python3 - "$PROOF/catalog.json" "$TYPE_TXT" "$STATE/pick-type.json" <<'PY'
import json, sys
cat = json.load(open(sys.argv[1])); txt = sys.argv[2]
tool = next(t for t in cat["tools"] if t.get("id") == "type.text")
# exact shape built by web/js/actions.js::toolAction()
act = {"kind": tool.get("kind", "builtin"), "tool": tool["id"],
       "type": "type_text", "label": tool.get("label", tool["id"]),
       "tier": tool.get("tier", 1), "args": {"text": txt + "\n"}}
json.dump({"name": "RR-PICK2", "takes": 1, "label": act["label"],
           "action": act}, open(sys.argv[3], "w"))
PY
DISPLAY="$D" xdotool windowactivate --sync "$TERM_WID" 2>/dev/null; sleep 0.4
call POST /teach/begin "@$STATE/pick-type.json" >/dev/null
[ "$CALL_CODE" = 200 ] || verdict FAILED "teach/begin picker tool -> $CALL_CODE"
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 teach:RR-PICK2 >/dev/null
sleep 0.3
call POST /teach/end '{}' >/dev/null
python3 "$HERE/kpstream.py" --base "$BASE" --key "$KEY" --fps 40 sign:RR-PICK2 >/dev/null 2>&1
for i in $(seq 1 30); do grep -q "$TYPE_TXT" "$SINK" 2>/dev/null && break; sleep 0.2; done
call GET /live/state >/dev/null
cp "$CALL_BODY" "$PROOF/state-type.json"
TYPED=0; grep -q "$TYPE_TXT" "$SINK" 2>/dev/null && TYPED=1
echo "tool: type.text typed=$TYPED (sink)" | tee "$PROOF/type.txt"
snapshot > "$PROOF/after.txt"

if [ "$HAVE" -gt "$BEFORE" ] && [ "$TYPED" = 1 ]; then
    verdict VERIFIED "GET /act app row -> bank action -> new $APP_ID window ($BEFORE->$HAVE); GET /act type.text row -> bank action -> '$TYPE_TXT' typed into the focused editor"
elif [ "$HAVE" -gt "$BEFORE" ]; then
    verdict FAILED "app row opened a window but the type.text row did not reach the sink"
elif [ "$TYPED" = 1 ]; then
    verdict FAILED "type.text row worked but the app.open row opened no window"
else
    verdict FAILED "neither catalog action produced its OS effect"
fi
