
## 2026-10-28 — Teach v3 merge cycle (devin-pro/app 7f321dc → devin/live-merge 84cafb8)

Merged PRO's new head `7f321dc` (Teach v3 quick-action grid b2f38ec +
macOS/Windows ports). 12 conflicts resolved: theirs for the Teach v3
files, HEAD kept for `web/js/actions.js` (.act-app tile-grid picker is
newer than PRO's select-row picker — verified in shots) and `web/sw.js`
(superset: signflow cache + legacy sweep). **`server/wfd.py` needed a
real union merge** — PRO's version predates the live hardening; kept the
auto-merged base (CORS Host-match, /pair-info IP-literal check,
/act/run + /act/pointer, /send mode+expect, abort cleanup, 429
Retry-After) and grafted PRO's pure additions (_mac_* window helpers,
_darwin() branches, node_name(), /send-file).

Gate: 335 unittest OK (skipped=3, xfail=1). Landing `astro build` clean.

Proofs on :96 realrun stack (env `-i` daemon, real xfwm4, fake-device
Chrome 390x844@2x — detections substituted only, labelled):
- tools/site/flow-teach.mjs (Teach v3 e2e): **20/20 PASS** — quick-grid
  pick → name → 3 real takes via daemon endpointer → banked → **fired
  immediately in Go Live, daemon matched, text in SinkEdit**. Old
  name→record→bind path also completes. No hscroll at 360/768/1280.
- tools/pro/shots-390.mjs: **7/7 PASS** (22-tile teach grid, .act-app
  picker, zero raw-text leaks in every shot).
- proofs/ui-v2/site-teach + shots-390 rewritten; PROOF.md updated.

oss/clean re-synced to 84cafb8 (kept curated set incl. tools/*, dropped
tools/site, tests/, cloud/, capture scripts; sign-wrap identity fixed in
PRO's new package.json/wrap launchers/JUDGE_PATH clone line) → pushed
`9be3228` to private. devin/live-merge pushed `84cafb8`.

## 2026-10-28 — Go Live v3 + air gestures (merged head, both ports identical)

MAX: merge the current head into devin/live-merge. Two ports of the
same merged Go Live landed in parallel (60e41ab here, 9c4c19c by the
parallel agent) — byte-identical outside COORD.md; merged, both logs
kept. Ports the merged Go Live (live.js/live.css — simplified overlays
per SIMPLIFY-1433) + the gesture layer that was dead code here
(hand.js 481 + handlines.js 595, verbatim) + the wfd section-A
handlers for /live/event: air-letter (AIR-C->Chrome, AIR-V->YouTube,
commands.tsv AIR-* overrides, through route_gloss so tier-2 gates
identically), scroll/pointer/click -> xdotool. flow-air.mjs synced
from the dr-test fixes (pinch enters browse/palm exits, API-taught
sign, unique per-run text, whole-file sink check, bank cleared at
start) + the 6 gesture clips.

Real-run :103: flow-air 9/9, act-tiles 27/27, flow-teach 21/21,
shots-390 7/7, 282 tests. Env caveat that keeps biting: a live
proof Chrome's tracker steals TEACH.session — kill remote-debugging
Chromes between drivers.

## 2026-10-28 — Every-screen simplify pass (devin-pro/app 01816b4)

MAX: merge 01816b4. The rest of FOUNDER-SIMPLIFY-1433 — ported PEAK's
shell to pro's surfaces: boot splash (boot.js/css), five-tone lotus
(flower.js + f3/f4 layers), morphing waveform->send-arrow act pill,
upload progress + cancel (App.api.upload XHR), calm drawer (single
Connect row when unpaired, no footer), laptop hero page, dev-gated
settings row, flat library rows (tap to open), quiet toast (visible
only on errors), deep-link bounce for laptop-only routes. Kept pro's
@ window targeting (re-applied into merge's compose.js; .tchip/win-*
styles re-added). v3 act picker now shows real installed-app icons.
Rebrand kept: "Sign-Flow". Skipped merge-only sheets (panes/packs —
retired/placeholders upstream).

Gates on :103: shots-390 7/7, flow-teach 21/21, act-tiles 27/27,
282 unit tests.

## 2026-10-28 — SPEC-1229 B1 pack + native-send proofs (head below)

MAX: merge the current head. "I am Govinda" is now the third prefilled
pack (was `intro`: gloss I-AM-GOVINDA, types "I am Govinda") — pack
tiles in the Teach grid read Hello / Namaste(built-in) / I am Govinda.
demo-ready.mjs updated for the PEAK shell + hardened (creates its own
photo/clip fixtures, whole-bank wipe so replay clips can't be matched
by stale signs, unique per-run sentinels, focus the sink AFTER the
live sheet opens since sheet-open steals X focus to the browser,
wf-uploads file assertion). shots-390 gained the sidebar-open leg
(1026 §3 proof).

Real-run :103: demo-ready 13/13 (pair -> @ window pick -> text/image
/file sends real end to end incl. pasted path + real wf-uploads file,
teach wizard -> Go Live fires bound type.text), shots-390 9/9,
flow-teach 21/21, 282 unit tests.

## 2026-10-27 — /act + @ picking on macOS/Windows (devin-pro/app b560574)

Cross-platform action layer, pushed to private `devin-pro/app` at `b560574`
(also mirrored on origin). Same /act contract, platform-detected:

- **server/act_platform.py** — `CrossPlatformProvider`, selected by
  `wfd.py` when `sys.platform` isn't Linux (Linux still gets
  `LinuxX11Provider`). macOS: osascript keystrokes/typing, `open -a`,
  `/Applications`+`~/Applications` discovery for the @ sheet,
  frontmost-app focus report. Windows: PowerShell SendKeys/typing,
  `explorer shell:AppsFolder`, `Get-StartApps` discovery, foreground-title
  focus. Scroll/workspace/tile/move_monitor refuse cleanly
  ("not supported on this platform") — no silent no-ops. Guards, tier
  floors, first-use confirm gate all unchanged.
- **system_ctl.py** — macOS volume/mute (osascript), screenshots
  (screencapture), lock (ctrl-cmd-q); Windows screenshot (PowerShell
  .NET) + lock (rundll32). `_PROBES` extended so `available:` stays
  honest per host.
- **UI leak cuts** — live.js miss toast no longer appends raw backend
  `detail`; picker descs show "not available here" without tool names.
  No new UI strings.

Tests: `test_act_platform.py` 25 cases green; full suite 217 pass.
Linux regression: `act-tiles-live.mjs` **27/27** on a fresh realrun
stack (`:103`, own daemon + sink + term — no agent terminals) — real
typing/copy/paste/undo, WM state flips, XF86 keys, screenshot file,
thunar launch, tier-2 close-window confirm. Proofs at
`proofs/ui-v2/act-tiles/` (gitignored on this branch — artifacts in
/tmp/wf-realrun-wispr-wrap-pro + tree).

macOS/Windows paths are unit-tested against mocked backends only —
not real-run on real hardware (none on this box). Linux is unchanged
and fully proven.

## 2026-10-27 — fresh 390x844 proof shots (devin-pro/app 3196072)

`tools/pro/shots-390.mjs` — composer, teach record, action grid, @ app
picker, Go Live control; each view leak-scanned (visibility-aware —
the ?dev seam fields are 1px-clipped when body.dev is off and stay
off in product runs). 6/6 PASS; shots in proofs/ui-v2/shots-390/
(gitignored branch — artifacts on disk). @ picker lists real apps
(Bulk Rename, Chrome, LibreOffice…); Go Live Control renders clean,
no engine/d=/ms/URL text. No product copy changes needed — everything
user-visible was already compliant after the b560574 toast/desc cuts.


## 2026-10-28 — teach exemplar-save "regression" diagnosed: streamer contamination (devin)

While waiting on PRO's `devin-pro/app` teach fix, reproduced the reported
symptom on live-merge @ 7092bd2 (daemon :8875, env `-i` stack, display :96):
`/teach/begin` ok → 176 kp frames POSTed, all 200 → `/teach/end`
`exemplars: 0, takes: 0`, zero segment events in `/live/kp` replies.

Diagnosis: **environmental, not a code regression on this tree**.
- `wfd.live_kp` in-process on the same tree + clip: teach captures fine
  (`got:1, done:True`).
- Fresh daemon (same code, port 8892, same key file): exemplar saves
  immediately.
- The failing :96 daemon had leftover proof-harness Chrome pages
  (`/tmp/pro-chrome-9233/9234`, opened by shots-390 dry runs) still
  pumping `/live/kp` frames. After killing them, the same push sequence
  on the same daemon saved the exemplar.

The real robustness hole this exposes (worth PRO's fix covering):
during `TEACH["active"]` the 409 guard only refuses frames carrying a
DIFFERENT `session` once an owner binds — **frames with NO `session`
field always feed the endpointer**, so a foreign sid-less streamer
interleaves zeros into the capture and segments silently never form.
A stale page/tab anywhere on the LAN (or a second open app window)
poisons every teach on that daemon until it stops streaming.

Cleanup done: proof-Chrome instances killed, test glosses removed,
bank = HELLO only. shots-390 `@ picker` check updated for the `.act-app`
tile grid (6/6 on the :96 stack) — commit 4effb7e.

## 2026-10-28 — teach e2e green on live-merge @ 952a822 + oss/clean re-sync (devin)

`devin-pro/app` polled: still `20ff3ea` (already an ancestor — merge = no-op).
Gate: **270 unittest OK** (skipped=3, expected failures=1) on 952a822.

**e2e — tools/pro/demo-ready.mjs on the :96 stack (env `-i` daemon :8875,
private Xvfb + real xfwm4 EWMH, Chrome fake-device 390x844): 11/12 PASS**
(`proofs/ui-v2/demo-ready/RESULT.txt`):
- pair → composer; @ window pick → `AT-TERM-OK` lands in the picked tty only
- text/image/video sends land in the focused SinkEdit editor
- **Teach**: DR0105 named → 3 real takes through the daemon endpointer →
  @-picker Type text `hi from sign` → Finish → sign banked (3 exemplars)
- **Go Live**: palm-hold → perform DR0105 → `match accepted` dist **0.024**
  (score 0.977) → `hi from sign` typed into the focused editor; live
  readout names the sign
- lone FAIL `typed text lands in the focused editor` = timing flake
  (xfwm4 first-focus settle; `dr-proof-text` present in sink after poll,
  direct `/send` re-verified in ~1.5 s — not a defect)

**proof shots — tools/pro/shots-390.mjs → proofs/ui-v2/shots-390: 6/6 PASS**
composer / teach-record / teach-grid / app-picker / go-live all clean of
raw text, URLs, ms, dtw, X-WF (fake-camera pac-man is the labelled
substitution seam). PROOF.md rewritten for this run.

**oss/clean**: synced to 952a822, scrubbed (agent names, /workspace+dr-test
paths, devin-pro branch refs, stale wispr-wrap URLs→sign-wrap, pycache,
landing capture tooling dropped), secrets scan clean — pushed
`4fd6996..9e7e882`. Stack notes: :96 now runs xfwm4 (real WM) — the fake
_NET_ACTIVE_WINDOW props are replaced by real EWMH; SinkEdit `pro-sink`
(6291474) + `wispr-wrap-term` cat tty (8388611) provide the sink/term
targets, env file `/tmp/wf-rr-proofs/env`.

## 2026-10-28 — merged devin-cloud/windows + devin-cloud/macos (HEAD 712d3f6)

Merge (no rebase): windows 247ea3b clean; macos ac8e4d3 conflicts
resolved — union platform branches in wfd/system_ctl, kept the
provider-based inject path + act_platform clip backends, folded in
darwin sips converter + non-ASCII paste fallback; 712d3f6 = resolution
fixups (darwin-first focus check, _to_png test signature). macOS also
brings @ window targeting into compose.js (web/ changed → re-shot).

Gates: full suite 282 green; Linux realrun act-tiles-live 27/27 on
:103 merged build; fresh shots-390 6/6 with the new compose UI
(composer/teach/grid/@-picker/go-live all clean, @ lists real apps).

Caveat: macOS/Windows remain unit-tested (mocked backends) — no real
hardware here. Cloud agents own those tracks; Linux unchanged.

MAX: merge 712d3f6 into devin/live-merge.

## 2026-10-28 — Teach v3 quick-action grid (devin-pro/app b2f38ec)

MAX: merge b2f38ec into devin/live-merge. Teach v3 ported from the
merged tree wholesale (quick.js/teach.js/teach.css, compat ids kept) —
the 'what should this sign do?' step is a one-tap .qa-tile grid driven
by GET /act: 22 tiles on Linux, each kind:builtin tool id, all proven
through /act. PACK_TILES gained 'intro' (this daemon's intro-pack id).
tools/site/flow-teach.mjs ported: 21/21 real-run on :103 — grid >=15,
pack tiles from GET /packs (hello/intro here, i-am-govinda mapped too),
3 takes -> held-out train view -> done -> immediate live fire typed
into the real SinkEdit, old name->record->bind path still green.
Harness fixes along the way: whole-file sink check with per-run text
(the Tcl sink's trailing newline puts landings 1 byte before the
measured offset — systematic), best-last_match in act-tiles polling
(trailing segment can overwrite last_match before last_action lands),
copy assert accepts any sink substring. act-tiles-live 27/27 re-green
on the v3 wizard (eval-set compat name, #tw-pick2, Ready done text).
shots-390 reshot: 22 tiles, all views clean. 282 unit tests green.

