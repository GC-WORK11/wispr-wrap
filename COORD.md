
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
