
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
