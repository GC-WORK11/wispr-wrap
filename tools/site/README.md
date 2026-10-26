# tools/site: the SITE lane (Teach, @ picker, loading screen, logo)

Everything here runs against a **real** daemon, a **real** Chrome and a **real** X11 display. The only substituted
pieces are the hand detections (synthetic keypoint clips, no video, no people) and the phone camera (Chrome's fake
device). Each flow ends with a `RESULT.txt` (PASS/FAIL per check) and 390x844@2x screenshots in `proofs/ui-v2/site-*`.

```bash
bash tools/site/run-all.sh                 # boots what is missing, runs every flow, prints a table
ONLY="tiles quick" bash tools/site/run-all.sh
```

| flow | what it proves |
|---|---|
| `flow-tiles.mjs` | each of the 20 one-tap tiles end to end: tap, Record, 3 takes, Ready, perform the sign, then the **real effect** on the laptop (window state, a real Chrome's tabs/scroll/clipboard/undo, a file on disk, a real editor). Audio tiles are reported `NEVER-ON-BOX` with what the laptop answered. |
| `flow-quick.mjs` | the Teach start screen (tiles only, one button), Type text, Open app, immediate use of a new sign, no horizontal scroll at 360/768/1280 |
| `flow-teach.mjs` | the full Teach path incl. the training screen, plus the old name, record, bind, done path that PRO's selftest and the demo driver still use |
| `flow-packs.mjs` | Hello / Thank you as tiles, taught from the grid and recognised; Namaste (palms together) pauses listening |
| `flow-picker.mjs` | the @ picker: real app icons, no headings, apps-only mode, Write your own |
| `flow-boot.mjs` | loading screen (real readiness, no layout shift) and the four sound cues (no toggle exists) |
| `flow-offline.mjs` | the laptop is stopped for real: the grid is replaced by a laptop icon and one Connect button |
| `logo/checks-logo.py` | the logo: one blob path + two glints + one gradient, crisp at 16/64/512, all PNG sizes, favicon.ico |

## Rules these flows enforce (FOUNDER-RULE-1652)
Screens carry tile names and button names, never sentences. `lib.mjs` has `visibleText(page, selector)` (every string
a person can actually see) and `isLabel(s)` (at most four words, no sentence punctuation). Assistive-tech text lives in
`.sr-only` elements (`role="status"` / `aria-label`) and is deliberately not visible.

## For agents (stable hooks)
- Tiles: `.qa-tile[data-id="<tool id>"]` (`win.alt-tab`, `win.maximize`, `win.minimize`, `win.close`, `sys.volume-up`, ...,
  `app.open`, `type.text`, `pack.hello`, `pack.thank-you`), `aria-pressed` marks the selection.
- Teach steps: `.tw-step[data-step="name|record|bind|done|train"]`; ids `tw-name-next` (Record), `tw-done-live` (Try it now),
  `qa-text-in` (Type text field), `qa-connect` (Connect). Take progress: `#tw-takebar .tk.got`.
- The grid is driven by `GET /act` tool rows `{id,label,icon,tier}`; icons come from the laptop's own theme at
  `/act/icons/<icon>-symbolic`.

## Logo
`python3 tools/site/logo/make-blob.py` regenerates `web/icon.svg` and `mark-inline.txt`;
`node --experimental-websocket tools/site/logo/render-logo.mjs` renders the PNG set; `python3 tools/site/logo/checks-logo.py`
builds `favicon.ico`, the contact sheet and the checks.

## Environment
`SITE_ENV` (realrun env file, default `/tmp/wf-realrun-wispr-wrap-site/env`; the overlay daemon uses `/tmp/wf-site-overlay/env`),
`SITE_CDP` (Chrome debugging port), `SITE_FAKE_CAM` (neutral fake-camera clip), `SITE_RECORD=1` (also write an mp4),
`TILES=win.maximize,key.enter` (subset of `flow-tiles`), `OVERLAY_REF` (git ref whose `server/` the overlay daemon runs).
The pairing key is read from the env file and never printed or written to a proof.
