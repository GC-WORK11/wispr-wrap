# The 5-minute judge path

**clone → run → scan → teach a sign → sign it → an app opens.** No sign-in, no GPU,
no model download beyond one vendored WASM. Total budget: under 5 minutes.

Every step is stamped:

- **VERIFIED** — works on `main` today, re-checked 2026-10-25
- **PENDING** — written for the target state of the merged build; the endpoint or
  UI it names is mid-merge and not landed yet. `tools/judge-smoke.sh` reports these
  as `pend` until they exist.

If you only have a laptop and no phone: `tools/judge-smoke.sh` runs the whole
daemon side headlessly in ~3 seconds.

---

## step 0 — before you start (~60 s)

You need: a **Linux laptop on an X11 session** (check `echo $XDG_SESSION_TYPE` —
Wayland cannot be injected into, see [../LIMITATIONS.md](../LIMITATIONS.md)),
`git`, `python3` ≥ 3.7, and either `node`+`npm` or nothing (`bin/wrap` runs
standalone — npm is a convenience, not a requirement). A phone with any modern
browser. Same Wi-Fi, **or** a USB cable.

After cloning, `bin/wrap doctor` tells you what's missing before it bites:

```
wrap doctor — dependency check
  [ok] python3       the daemon itself
  [ok] curl          CLI talks to the daemon over HTTP
  [ok] xdotool       types/pastes at the cursor
  ...
  [ok] session       x11 (DISPLAY=:0)
  [ok] python 3.13   daemon needs 3.7+
  [ok] key perms     /home/you/.config/wf/key mode 600
```

VERIFIED 2026-10-25 — doctor output above is real output format from this branch.

## step 1 — clone + run (~60 s)

```bash
git clone https://github.com/GC-WORK11/wispr-wrap && cd wispr-wrap
npm install && npm run wrap        # zero-dep install, instant
# no node? equivalent: ./bin/wrap start
```

Expected: an ASCII QR plus lines like

```
  scan with your phone camera — composer opens paired
  or open on phone:  http://192.168.1.4:8741/?k=…
  app/manual:        address 192.168.1.4:8741   code AB12CD34
```

VERIFIED 2026-10-25. Failure modes:

| symptom | cause | fix |
|---|---|---|
| `daemon failed — see wfd.log` | port already bound | `WF_PORT=9000 npm run wrap` |
| phone scans but page never loads | ufw/firewalld blocking :8741 | `wrap start` offers to open it once (sudo); headless: `sudo ufw allow 8741/tcp` |
| `npm: command not found` | no node | `./bin/wrap start` — identical path |

## step 2 — pair the phone (~20 s)

Scan the QR with the phone camera → the composer opens **already paired** (key rides
in the `?k=` link). No QR scanner? Open `http://<lan-ip>:8741` on the phone and type
the 8-char code shown at startup.

Expected: a dark page with a bottom pill — `[ + ]  [ text field ]  [ ↑ ]`.

VERIFIED 2026-10-25. Failure modes:

| symptom | cause | fix |
|---|---|---|
| page loads, pairing rejected | phone hit a different network/VPN | use the printed `other interface:` link, or USB below |
| can't reach the laptop at all | LAN isolation (café/corp wifi) | `wrap bridge` over USB, or `wrap tunnel` for an https QR |

## step 3 — sanity send (~20 s)

Click a text field on the laptop (any editor, a terminal, Gmail). On the phone, type
or dictate → **↑**. Expected: text types at the laptop cursor.

VERIFIED 2026-10-25 — this is the path in the recorded demos. If nothing appears:
focus moved (xdotool types where focus *is*, not where it was), or the session is
Wayland — doctor step 0 flags both.

## step 4 — teach your first sign (~90 s) — PENDING

> The camera path needs a **secure context**: browsers refuse `getUserMedia` on
> `http://<lan-ip>`. For Live mode use **USB** (`wrap bridge`, then open
> `http://localhost:8741` on the phone — localhost counts as secure) or
> **`wrap tunnel`** (https QR). Plain LAN works for text/media only.
> PENDING — this constraint lands with the Live UI; verified against the
> getUserMedia spec, not yet against our page.

In the composer tap **Live** → allow camera → **teach a sign**:

1. name it — e.g. `OPEN-BROWSER`
2. perform it 3 times for the camera (3-2-1 countdown per take)
3. preview the captured trajectories → bind action → *open app → `google-chrome`*

Expected: the sign appears in your library with 3 exemplars; the daemon writes
`~/.config/wf/signbank.json` (mode 600, yours, deletable). ~90 seconds per sign.
Video never leaves the phone — the browser sends 21-point hand keypoints, ~1 KB
per frame.

PENDING 2026-10-25 — `/teach/*`, `/bank`, and the composer teach wizard are
mid-merge. Check `curl -H "X-WF-Key: $(cat ~/.config/wf/key)" localhost:8741/bank`;
404 = not landed yet.

## step 5 — sign → the app opens (~30 s) — PENDING

Palm open to the camera → listening starts. Sign `OPEN-BROWSER` → Chrome opens on
the laptop. Palms together (namaste) → it stops listening.

Expected: the composer's reading strip shows the live top-guess and match distance;
the action fires once per sign; risky actions (risk tier ≥ 2) hold for a
laptop-side confirm instead of firing blind. A sign that doesn't clear the
rejection threshold **abstains — it never guesses**.

PENDING 2026-10-25. Failure modes when it lands:

| symptom | cause | fix |
|---|---|---|
| sign never fires | motion too different from the takes | re-teach, or add a 4th–5th exemplar (≤5 kept) |
| fires on random movement | threshold too loose for your vocab | report it — false-accept rate is a measured gate in BENCH.md, not a vibe |
| app doesn't open but match shows | bound argv missing on the laptop | rebind to an installed binary |

## step 6 — optional extras

- `wrap install` — build + install the Android shell over adb (needs `adb`, JDK).
- `wrap send "hello"` — CLI sanity check without the phone.
- `wrap stop` — kills daemon + tunnel, removes the adb reverse.

## the honest scoreboard

| step | status @ 2026-10-25 | proof |
|---|---|---|
| clone → daemon → QR | VERIFIED | this doc re-run on `2493c53`; smoke covers it |
| pair (QR / code / usb) | VERIFIED | `tools/judge-smoke.sh` exercises `/pair` + `/pair-info` |
| text / image / file → cursor | VERIFIED | shipped and in daily use |
| teach wizard + bank write | PENDING | endpoints mid-merge; `judge-smoke` watches for them |
| sign → action | PENDING | needs `/live/kp` + endpointer + matcher |
| per-user taught-sign recall | UNMEASURED | will land in `BENCH.md` (eval lane) — no claim until then |
| sign-end → action latency | UNMEASURED | ~50–90 ms expected on LAN; `BENCH.md` gets the real p50/p95 |

If any PENDING row is still 404 when you read this, the merge is in flight —
`git log --oneline` shows what's landed.

---

## naming

The product ships as **Sign-Flow**; the daemon binary and CLI stay `wrap`.
