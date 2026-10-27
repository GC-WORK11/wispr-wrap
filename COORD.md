
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
