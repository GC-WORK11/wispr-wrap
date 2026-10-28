# macOS manual QA checklist

Run on a real Mac with the daemon installed (`npm install`, `wrap start`).
Prerequisite: **Accessibility permission** — the terminal (or whatever hosts
`wfd.py`) must be allowed under System Settings → Privacy & Security →
Accessibility. `wrap doctor` probes this and warns when it's missing.

## Start / pair

- [ ] `wrap doctor` — all required rows `ok`; `session` shows `macOS (Aqua)`,
      `accessibility` shows the frontmost app name.
- [ ] `wrap start` — daemon comes up, QR + LAN link + pair code print;
      `wfd.pid`/`wfd.log` written. Closing the terminal leaves the daemon up.
- [ ] `wrap status` — `daemon: UP`, rss reported via `ps`, `lan:` URL.
- [ ] `wrap stop` / `wrap restart` — clean stop and re-start.

## Window picking (@ menu)

- [ ] Composer: type `@` — menu lists real open windows
      (`GET /windows` → `[{id:"<pid>:<index>", app, title}]`).
- [ ] Pick a window → chip shows it; sends land in that app.
- [ ] Change the chip to a different app → next send focuses that app
      (AXRaise + frontmost via System Events).
- [ ] Clear the chip → sends go to whatever is focused.

## Injection

- [ ] `wrap send "hello"` or composer send — ASCII text types into the
      focused app via System Events keystroke.
- [ ] Send text containing non-ASCII (e.g. `é`, `✓`, emoji) — lands intact
      (routed through pbcopy + cmd+v; keystroke alone garbles these).
- [ ] Send into a terminal app — cmd+v paste lands (no ctrl+shift+v on macOS).

## Image / file

- [ ] `/send-image` (PNG) — image pastes as pixels (NSPasteboard has NSImage +
      NSURL). JPEG/WebP also works (normalized via `sips`).
- [ ] `/send-file` — file saved to `~/wf-uploads/<ts>_<name>` and the file
      URL pastes (Finder pastes the file, editors paste an attachment).

## Quick actions / Live

- [ ] `sys.screenshot` — `screencapture` writes the shot.
- [ ] `sys.lock` — ctrl-cmd-q locks the screen.
- [ ] `sys.volume` / `sys.mute` — osascript volume settings respond.
- [ ] `media.*` — NX media keys (play/next/prev) reach the active player;
      Music/Spotify are the named fallback.
- [ ] `net.wifi` — `networksetup -setairportpower en0 on|off` toggles Wi-Fi.
- [ ] `net.bluetooth` — works only with `blueutil` (brew); without it the
      action refuses honestly.
- [ ] `clipboard.history` — index 0 returns the live clipboard (pbpaste);
      index > 0 refuses honestly (no manager on stock macOS).
- [ ] `sys.dnd`, `brightness-*` — refuse honestly (no stock macOS mechanism).

## Regressions to watch

- Clipboard restores after a paste (the daemon saves/replaces/restores —
      copy something, send text, paste by hand, confirm the old clip is back).
- Second `wrap start` while running reports "already running".
- Port fallback: occupy 8741, `wrap start` binds the next port and the pair
      card matches the bound port.
