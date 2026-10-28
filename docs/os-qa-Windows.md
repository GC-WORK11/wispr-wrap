# Windows manual QA checklist

The Windows backend is implemented and unit-tested with the OS calls
mocked, but has not run on real Windows hardware yet — work this list on
a real Windows 10/11 machine and mark each row PASS/FAIL with the actual
result, not the expected one. Anything that fails honestly (a refused
action naming the missing backend) is still a real result — log it.

Setup:

```powershell
git clone <repo>
cd sign-flow
npm install -g .        # or: npm link
wrap doctor             # all "MISSING" rows must be justified
```

If `wrap` is not on PATH after npm install, use `node bin\wrap.js <cmd>`
from the repo root instead — same commands.

## 1. Install + start

- [ ] `wrap doctor` — python row ok, powershell row ok, no MISSING row
      unexplained.
- [ ] `wrap start` — daemon stays up after the terminal that launched it
      is closed (DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP).
- [ ] `wrap start` prints a QR, the `http://<lan-ip>:<port>/?k=…` link,
      the `app/manual` address + 8-char pair code.
- [ ] `curl http://localhost:8741/health` (or the bound port) answers 200.
- [ ] `wrap status` reports UP, pid, LAN URL.
- [ ] `wrap logs` shows `[wfd]` lines, no traceback.
- [ ] `wrap stop` kills the daemon (`wrap status` → DOWN).

## 2. Pairing

- [ ] Phone browser opens `http://<lan-ip>:<port>/?k=<key>` — composer
      loads, pair status shows connected.
- [ ] If the phone can't reach it: the firewall hint printed by
      `wrap start` names `netsh advfirewall …` and opening the port
      fixes it.

## 3. @ app picking (/windows)

- [ ] In the composer, tap `@` — the focus/close tool select lists
      installed apps AND open windows (`proc · title` rows).
- [ ] Pick an open window → its `hwnd` hex id lands in the input → the
      bind fires `focus_app` → the window comes to front on the laptop
      (foreground-lock lifted by the alt-tap).
- [ ] Minimized windows are restored before focusing.
- [ ] Picked a window by name (not hwnd) → WScript.Shell AppActivate
      focuses it.

## 4. Text injection

- [ ] `wrap send "hello windows"` — text lands at the laptop cursor via
      SendKeys.
- [ ] Multi-line send — `\n` becomes `{ENTER}` in the target app.
- [ ] Text with `+^%~()[]{}` arrives literal (SendKeys escaping).
- [ ] Type-guard: with no focused window reported, the action refuses
      rather than typing blind.

## 5. Clipboard paths

- [ ] `/send` with paste mode — clipboard set, `ctrl+v` chord injected,
      previous clipboard restored after ~1 s.
- [ ] In Windows Terminal the chord is `ctrl+shift+v` (terminal class
      detection).
- [ ] `/send-image` a JPG — lands as a real image on the clipboard
      (`SetImage`, not a file path); paste into Paint works.
- [ ] `/send-image` a PNG — same (no re-encode needed).
- [ ] `/send-file` a file — clipboard holds the file (FileDrop list);
      paste into Explorer copies it; paste into a file input attaches it.
- [ ] File still lands in `~\wf-uploads\` (check `%USERPROFILE%\wf-uploads`).

## 6. Quick-action tiles + Live sign actions

- [ ] `sys.volume-up/down` — volume moves (2%-per-press loop).
- [ ] `sys.mute` — mutes/unmutes.
- [ ] `media.playpause`/`next`/`prev` — a running player responds
      (VK_MEDIA keys).
- [ ] `sys.screenshot` — PNG lands in `~/Pictures` (region mode is
      honestly refused on Windows).
- [ ] `sys.lock` — workstation locks (rundll32).
- [ ] `sys.dnd` — toast banners toggle off/on in Settings.
- [ ] `net.wifi off`/`on` — Wi-Fi adapter disables/enables (needs admin;
      refusal should say so).
- [ ] `net.bluetooth off`/`on` — Bluetooth radio toggles (needs admin).
- [ ] `sys.brightness-up/down` — laptop display brightness moves
      (WmiMonitorBrightness; desktops should refuse honestly).
- [ ] `clipboard.history` index 0 — returns the live clipboard; index ≥1
      refuses honestly.
- [ ] `desktop.show` — toggles the desktop.
- [ ] `win.alt-tab` / `win.minimize` / `win.close` — work via SendKeys.
- [ ] A taught sign bound to a `sys.*` action fires it from Live mode.

## 7. Honest-failure spot checks

- [ ] `sys.screenshot-region` says "not supported on Windows".
- [ ] `clipboard.targets` lists real formats when the clipboard holds
      text/files, or refuses when empty.
- [ ] Every failure's detail names what it needs — no silent no-ops.

## Known gaps (expected FAIL/refuse)

- Region screenshots, clipboard history > 0, tiling/workspace/monitor
  moves, app icons — refused by design on Windows.
- `powershell` must be on PATH; `pwsh` also works.
- `wrap send` uses the key file at `%USERPROFILE%\.config\wf\key`.
