
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
