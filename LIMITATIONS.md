# LIMITATIONS

Read this before judging. Everything here is a real, current limit — not a hedge.
`bin/wrap doctor` catches the environment ones before they bite.

## platform

- **Linux/X11 only.** On a Wayland session `xdotool`/`xclip` cannot inject into
  apps — previously a *silent* failure. `wrap doctor` now detects the session via
  `XDG_SESSION_TYPE` and warns (added 2026-10-25). Fix today: log into an X11
  session ("GNOME on Xorg" or equivalent). A Wayland/portal backend does not
  exist yet.
- **macOS / Windows: not supported.** The daemon is portable stdlib Python, but
  the injection layer is a port of `xdotool`, not a rebuild — a documented
  future lane, not a promise.
- **Headless/SSH:** the daemon serves and pairs fine headless; nothing can be
  *typed* without a display. Doctor flags it.

## live sign lane

- **Camera needs a secure context.** Phone browsers refuse `getUserMedia` on
  `http://<lan-ip>`. Live mode over LAN will not open the camera — use USB
  (`wrap bridge` → `http://localhost:8741`, which browsers trust) or
  `wrap tunnel` (https). Text/media sending is unaffected.
- **Phone-camera ergonomics.** A held phone sees one hand — one-handed vocab
  only, or put the phone on a stand. Two-handed signing is a laptop-webcam
  path that does not ship in this build; that's why the phone path exists.
- **Phone keypoints are hands-only.** ~21 points at ~15–30 fps, no body channel.
  The endpointer and bank thresholds were tuned on whole-body keypoints —
  measured numbers exist for a synthetic-motion sensor (`BENCH.md` §1–2);
  accuracy on a human hand on a real camera is **not yet measured**.
- **Per-user taught-bank recall/precision: measured on synthetic motion only.**
  `BENCH.md` §1 reports it (best operating point ~95% answer at 15–22.5%
  wrong at the default threshold — **NOT MET** vs the target bar). No
  human-camera measurement exists yet; treat real-world recall as open.
- **Open-set false accepts: measured on synthetic motion, NOT MET.** At the
  shipped default threshold, simulated desk motion fires ~1 routed action
  per 3.4 s (`BENCH.md` §2). "Everyday motion doesn't fire actions" is a
  measured gate (false accepts/hour), not a launch slogan.
- **One user per bank.** `~/.config/wf/signbank.json` is per-user, per-machine.
  No multi-user switching, no sync, no cloud backup — export = copy the file.

## sign-to-text lane

- The CTC sign-to-text engine works but needs **~30 min of GPU enrollment per
  user** — it is an advanced lane, not part of the self-serve path.
- **No research weights ship in this repo.** Research-trained models and
  dataset weights stay private (license restrictions); the public build
  ships code + the DTW matcher + recorded sign examples only. Nothing
  dataset-derived is committed.

## transport & security

- **LAN is authenticated cleartext HTTP.** The 40-hex pairing key travels once in
  the `?k=` URL — it can sit in phone browser history. `wrap tunnel` encrypts;
  delete `~/.config/wf/key` + `wrap restart` rotates the key.
- **`wrap tunnel` traverses Cloudflare's edge** for reachability only. No
  inference, storage, or key minting happens there — but the URL does leave your
  LAN. USB mode is zero-network.
- **`/pair` is rate-limited, not airtight** (1 try/sec/IP): it blunts 8-char code
  guessing; it isn't proof against a determined LAN attacker. Rotate the key if
  you pair on a hostile network.

## known bugs in `server/wfd.py` (triaged — daemon lane owns the fixes)

- request body is read unbounded — `Content-Length` is trusted
- rapid sends can interleave `xdotool` keystrokes (fire-and-forget `Popen`)
- `X-File-Name` is never URL-decoded
- clipboard restore mangles non-text clipboards
- fixed `/tmp/wf-img*` paths race on concurrent image sends
- `wfclip.py` advertises the `MULTIPLE` target then refuses it

## licenses

- **MediaPipe** (Live camera path): the hand-landmark WASM/model is vendored, not
  CDN-loaded — Apache-2.0 per Google's published model cards at the time of
  writing; re-verify the specific model file's license before shipping weights
  in this repo.
- `server/qrcodegen.py` is vendored (Nayuki, MIT).
- This repo is MIT. Components imported from sign-flow keep their own license;
  see `LICENSE`.
