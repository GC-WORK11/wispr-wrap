# PROOFS — the realrun gate (founder rule 17:08)

A feature counts ONLY when it works end to end on a real run, a real OS
effect is observed, and the proof is recorded + reproducible. Green unit
tests are NOT done. This file is the index; the artifacts
(`proofs/realrun/<feature>/`) are local-only by `.gitignore` — they may
contain screen captures and must never be committed to this public repo.

## The rig

```
phone-composer stand-in            REAL product path                  real OS effect
┌────────────────────┐   HTTP    ┌──────────────────────────────┐
│ kpstream.py        │──────────▶│ server/wfd.py on :8874       │    ┌────────────────────────┐
│  synthetic kp42    │ /live/kp  │  scratch HOME, DISPLAY :97   │    │ Xvfb :97 + xfwm4 (4    │
│  clips, 15 Hz      │           │  -> endpointer -> DTW bank   │──▶ │  workspaces) + real    │
│                    │           │  -> commands.tsv argv spawn  │    │ apps: wish SinkEdit,   │
│ curl /teach,/bank, │           └──────────────────────────────┘    │ xfce4-terminal,        │
│ /send,/act         │            (the shipped code, unmodified)     │ xmessage               │
└────────────────────┘                                              └────────────────────────┘
```

Everything fires through the REAL path — the daemon's HTTP endpoints or
the argv a real binding spawns (wmctrl/xdotool/xclip/ffmpeg/…). No
verifier calls an implementation function to produce an effect. The
pairing key lives only in `/tmp/wf-realrun/env` (mode 600) and is
redacted from every artifact by `sanitize_dir`.

### Boot / repro

```bash
tools/realrun/setup.sh        # boot stack (or run_all.sh does it)
tools/realrun/run_all.sh      # all verifiers + table; --teardown to kill
tools/realrun/verify_<feature>.sh   # any single feature, standalone
```

Per feature: `before.txt after.txt diff.txt shot.png run.log RESULT`.

## Capability matrix (this box — probed live)

| subsystem                | status | detail |
|--------------------------|--------|--------|
| X server                 | ✅ Xvfb :97 1280x800, XTEST + XI2 2.0 | real display, real input injection |
| window manager           | ✅ xfwm4, 4 EWMH workspaces | real focus/tile/workspace semantics |
| inject tools             | ✅ xdotool wmctrl xprop xwininfo xclip ffmpeg xmodmap | |
| GUI apps                 | ✅ wish(Tk text editor) xfce4-terminal xmessage thunar google-chrome libreoffice | |
| input capture            | ⚠️ `xinput` binary ABSENT | realrun supplies an XI2-protocol shim (same event classes, same text wire) — see `xinput_shim.py` header |
| audio                    | ❌ no pulseaudio/pactl/amixer | volume/mute = NEVER-ON-BOX |
| backlight                | ❌ /sys/class/backlight empty, no brightnessctl | NEVER-ON-BOX |
| wifi / bluetooth         | ❌ no nmcli/bluetoothctl/rfkill | NEVER-ON-BOX (honest flag) |
| media keys (MPRIS)       | ❌ no playerctl; no org.mpris.* player on bus | NEVER-ON-BOX |
| screen lock              | ⚠️ loginctl+xdg-screensaver exist, no locker session | untestable honestly |
| DND / notifications      | ❌ no gsettings, no Notifications daemon | NEVER-ON-BOX |
| clipboard history        | ⚠️ xclip only (index 0) | no manager (copyq/clipster absent) |
| screenshot tools         | ❌ import/scrot/maim/xwd/gnome-screenshot all absent | verified via bound ffmpeg argv instead |

## Feature table (run_all.sh refreshes the verdict column)

| feature | OS effect observed | proof dir | repro | verdict |
|---------|--------------------|-----------|-------|---------|
| pair_send | typed text lands in the focused real editor (sink file + WM_NAME diff) | proofs/realrun/pair_send | `tools/realrun/verify_pair_send.sh` | VERIFIED |
| live_sign | sign -> marker file spawned; palm gate holds (no listen = no commit) | proofs/realrun/live_sign | `tools/realrun/verify_live_sign.sh` | VERIFIED |
| open_app | sign -> new real app window (wmctrl -l diff, WM_CLASS) | proofs/realrun/open_app | `tools/realrun/verify_open_app.sh` | VERIFIED |
| focus_app | sign -> _NET_ACTIVE_WINDOW changes (WM_NAME diff) | proofs/realrun/focus_app | `tools/realrun/verify_focus_app.sh` | VERIFIED |
| close_window | sign -> window gone from client list | proofs/realrun/close_window | `tools/realrun/verify_close_window.sh` | FAILED |
| tile | sign -> window geometry/maximized-state diff (xwininfo/xprop) | proofs/realrun/tile | `tools/realrun/verify_tile.sh` | FAILED |
| workspace | sign -> desktop switch + window moved (_NET_WM_DESKTOP) | proofs/realrun/workspace | `tools/realrun/verify_workspace.sh` | VERIFIED |
| volume | no audio backend on box | proofs/realrun/volume | `tools/realrun/verify_volume.sh` | NEVER-ON-BOX |
| screenshot | sign -> real PNG of the display (header+size) via bound argv | proofs/realrun/screenshot | `tools/realrun/verify_screenshot.sh` | VERIFIED |
| macro | sign -> multi-effect chain (open window, type, close, step log) | proofs/realrun/macro | `tools/realrun/verify_macro.sh` | VERIFIED |
| context | same sign -> different marker per focused app | proofs/realrun/context | `tools/realrun/verify_context.sh` | VERIFIED |
| observe | reported-without-effect mode (route probe) | proofs/realrun/observe | `tools/realrun/verify_observe.sh` | PENDING |
| record | real XI2 input -> macro.json (type/keys/click steps, mode 600) | proofs/realrun/record | `tools/realrun/verify_record.sh` | FAILED |
| confirm | tier-2 sign parks; /act/confirm fires; cancel buries | proofs/realrun/confirm | `tools/realrun/verify_confirm.sh` | FAILED |
| auth | unpaired/wrong-key/proxied all refused on live surface | proofs/realrun/auth | `tools/realrun/verify_auth.sh` | VERIFIED |

Legend: VERIFIED = real run + real effect + proof. PENDING = route not
in the daemon yet (N1 mid-flight). NEVER-ON-BOX = box lacks the
subsystem (probe recorded). FAILED = broken for real — kept loud.
