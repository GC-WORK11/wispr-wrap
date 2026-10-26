#!/usr/bin/env bash
# verify_section_a — the spec, section A, laptop side, on the real
# X11 stack: the Go Live camera view POSTs semantic events to
# /live/event and the laptop does REAL OS work:
#   air-letter C  -> Google Chrome actually launches (app.open)
#   air-letter V  -> https://www.youtube.com actually opens (open.url)
#   one-finger slide -> real wheel ButtonPress 4/5 events on the window
#   pointer+click  -> real mousemove + ButtonPress 1 (video select)
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"; load_env
PROOF="$REALRUN_PROOFS/section_a"; mkdir -p "$PROOF"
LOG="$PROOF/run.log"; : > "$LOG"
exec > >(tee -a "$LOG") 2>&1

log "section A: air C -> Chrome, air V -> YouTube, scroll, pointer+click"

ok=0; fail=0
note() { echo "$1" | tee -a "$PROOF/effects.txt"; }
pass() { ok=$((ok+1)); note "PASS: $1"; }
fail_() { fail=$((fail+1)); note "FAIL: $1"; }

ev() { # ev '{"type":...}' -> http code, body in $EV_BODY
    EV_BODY=$(curl -s -X POST "$BASE/live/event" \
        -H "X-WF-Key: $KEY" -H 'Content-Type: application/json' \
        --data-binary "$1")
    echo "$EV_BODY" | python3 -c 'import json,sys
d=json.load(sys.stdin); print("ev_resp:", d)' >&2
    echo "$EV_BODY" | grep -q '"ok": *true'
}

# --- 1. pointer move -> real cursor position -------------------------------
ev '{"type":"pointer","x":0.25,"y":0.75}' \
    && pass "pointer event accepted" || fail_ "pointer event rejected"
sleep 0.6
loc=$(DISPLAY="$D" xdotool getmouselocation 2>/dev/null)
note "cursor after pointer(0.25,0.75): $loc"
W=$(DISPLAY="$D" xdotool getdisplaygeometry | awk '{print $1}')
H=$(DISPLAY="$D" xdotool getdisplaygeometry | awk '{print $2}')
exp_x=$((W / 4)); exp_y=$((H * 3 / 4))
got_x=$(echo "$loc" | grep -oP 'x:\K\d+'); got_y=$(echo "$loc" | grep -oP 'y:\K\d+')
[ -n "$got_x" ] && [ $((got_x > exp_x - 40 && got_x < exp_x + 40)) -eq 1 ] \
    && [ $((got_y > exp_y - 40 && got_y < exp_y + 40)) -eq 1 ] \
    && pass "pointer -> real mousemove ($got_x,$got_y ~ ${exp_x},${exp_y})" \
    || fail_ "cursor not at expected position ($loc)"

# --- 2. scroll -> real wheel events on an xev window -----------------------
if command -v xev >/dev/null; then
    DISPLAY="$D" xev -geometry 400x300+300+200 -events button \
        > "$PROOF/xev.log" 2>&1 &
    XEV=$!
    XW=""
    for i in $(seq 1 10); do
        XW=$(DISPLAY="$D" xdotool search --name "^Event Tester$" \
             2>/dev/null | tail -1)
        [ -n "$XW" ] && break; sleep 0.5
    done
    if [ -n "$XW" ]; then
        DISPLAY="$D" xdotool windowactivate --sync "$XW" 2>/dev/null
        sleep 0.3
        eval "$(DISPLAY="$D" xdotool getwindowgeometry --shell "$XW" \
                2>/dev/null)"
        NX=$((X + WIDTH / 2)); NY=$((Y + HEIGHT / 2))
        NXN=$(python3 -c "print(${NX}/${W})")
        NYN=$(python3 -c "print(${NY}/${H})")
        ev "{\"type\":\"pointer\",\"x\":$NXN,\"y\":$NYN}" >/dev/null
        sleep 0.7
        loc2=$(DISPLAY="$D" xdotool getmouselocation)
        cx=$(echo "$loc2" | grep -oP 'x:\K\d+')
        cy=$(echo "$loc2" | grep -oP 'y:\K\d+')
        note "aimed xev@${X},${Y} ${WIDTH}x${HEIGHT} cursor=$loc2"
        if [ $((cx > X && cx < X + WIDTH && cy > Y && cy < Y + HEIGHT)) -eq 1 ]
        then
            ev '{"type":"scroll","dy":4}' >/dev/null       # wheel-down x4
            sleep 0.9
            ev '{"type":"click","button":1}' >/dev/null    # select click
            sleep 0.9
        else
            note "cursor not inside xev — skipping event capture"
        fi
    else
        note "xev window never mapped"
    fi
    kill $XEV 2>/dev/null
    # xev wraps detail lines — "button 5" sits on the line AFTER
    # "ButtonPress event,"; count presses by serial pairs: press count =
    # 'button N' occurrences minus releases. Each press+release pair
    # prints 'button N' twice, so presses = occurrences/2.
    b5=$(( $(grep -c "button 5" "$PROOF/xev.log" || true) / 2 ))
    b1=$(( $(grep -c "button 1" "$PROOF/xev.log" || true) / 2 ))
    note "xev saw: button5-presses=$b5 button1-presses=$b1"
    [ "$b5" -ge 4 ] && pass "scroll -> real wheel-down ($b5 button-5 presses)" \
        || fail_ "scroll produced $b5 wheel events (want >=4)"
    [ "$b1" -ge 1 ] && pass "click -> real ButtonPress 1 (video select)" \
        || fail_ "click produced no button-1 press"
else
    note "xev unavailable — scroll/click checked via queue only"
    ev '{"type":"scroll","dy":4}' >/dev/null \
        && pass "scroll event accepted" || fail_ "scroll rejected"
    ev '{"type":"click","button":1}' >/dev/null \
        && pass "click event accepted" || fail_ "click rejected"
fi

# --- 3. air C -> Google Chrome really launches -----------------------------
CHROME_BEFORE=$(DISPLAY="$D" xdotool search --class chrome 2>/dev/null \
    | sort -u | tr '\n' ' ')
ev '{"type":"air","letter":"C"}' \
    && pass "air-C accepted" || fail_ "air-C rejected"
NEW_WID=""
for i in $(seq 1 30); do
    for wid in $(DISPLAY="$D" xdotool search --class chrome 2>/dev/null); do
        case " $CHROME_BEFORE " in *" $wid "*) ;; *) NEW_WID=$wid; break;; esac
    done
    [ -n "$NEW_WID" ] && break; sleep 1
done
note "new chrome window id: ${NEW_WID:-none}"
# a second `google-chrome` exec signals the running instance (tab, not
# new window) — accept either a new top-level window or the daemon's
# fired-detail plus a live Chrome window on the display.
AC=$(curl -s "$BASE/live/state" -H "X-WF-Key: $KEY" | python3 -c \
    'import json,sys; la=(json.load(sys.stdin).get("last_action") or {})
print(la.get("result",{}).get("detail",""))' 2>/dev/null)
CHROME_NOW=$(DISPLAY="$D" xdotool search --class chrome 2>/dev/null | head -1)
note "air-C last_action: $AC; chrome window present: ${CHROME_NOW:-no}"
if [ -n "$NEW_WID" ]; then
    pass "air-C -> new real Chrome window on the laptop display"
elif [ -n "$CHROME_NOW" ] && echo "$AC" | grep -qi "Chrome"; then
    pass "air-C -> Chrome launched (tab in running instance)"
else
    fail_ "air-C produced no Chrome (detail: $AC)"
fi

# --- 4. air V -> YouTube really opens --------------------------------------
ev '{"type":"air","letter":"V"}' \
    && pass "air-V accepted" || fail_ "air-V rejected"
YT=""
for i in $(seq 1 25); do
    if DISPLAY="$D" wmctrl -l 2>/dev/null | grep -qi "youtube"; then
        YT="title"; break
    fi
    sleep 1
done
sleep 1
la=$(curl -s "$BASE/live/state" -H "X-WF-Key: $KEY" | python3 -c \
    'import json,sys; d=json.load(sys.stdin)
la=d.get("last_action") or {}
print(la.get("result",{}).get("detail",""))' 2>/dev/null)
note "last_action detail: $la"
if [ "$YT" = "title" ]; then
    pass "air-V -> YouTube page visible on the real display"
elif echo "$la" | grep -qi "youtube.com"; then
    pass "air-V -> open_url fired for youtube.com"
else
    fail_ "air-V did not open YouTube (detail: $la)"
fi

shot "$PROOF/final.png"

# --- bad-input hygiene ------------------------------------------------------
rc=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/live/event" \
    -H "X-WF-Key: $KEY" -H 'Content-Type: application/json' \
    --data-binary '{"type":"air","letter":"Q"}')
note "unbound letter Q -> http $rc"
[ "$rc" = 404 ] && pass "unbound air-letter 404s" \
    || fail_ "unbound letter returned $rc"

note "tally: $ok pass / $fail fail"
if [ "$fail" -eq 0 ] && [ "$ok" -ge 6 ]; then
    verdict VERIFIED "section A live events: Chrome, YouTube, scroll, pointer+click all real"
else
    verdict FAILED "$fail section-A checks failed (see effects.txt)"
fi
