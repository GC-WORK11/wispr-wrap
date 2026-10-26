# tools/demo — the recorded Sign-Flow demo (replayed keypoints)

This lane produces the honest, reproducible screen-capture demo: the **real
shipped live-sign pipeline** running on a private X display, driven by
**replayed keypoint clips** instead of a camera. Everything except the
camera/MediaPipe input is real and unmodified.

```
clips/*.json ──► replay service :8877 ──(GET /pull)──► composer page (chrome)
                     ▲                                     │ POST /live/kp
                     │                                     ▼
                X-Replay-Route                      REAL wfd.py :8874
                :8874 (demo)                      endpointer → DTW signbank
                                                        │ commands.tsv router
                                                        ▼
                                   real argv: touch markers · xdotool type
                                   tier-2: parked pending → /act/confirm
```

## What is real vs replayed

| part | status |
|---|---|
| 42-point hand keypoints arriving over HTTP | **replayed** from `tools/demo/clips/*.json` (the only substitution) |
| browser composer page + its kp pump (`web/js/pose.js` `Pose.replay()` → `/live/kp`) | real, unmodified — the same code path camera frames take |
| `server/wfd.py` daemon, `server/endpointer.py` segmentation, `server/signbank.py` python-DTW | real, unmodified |
| `/teach/begin`…`/teach/end` 3-take enrolment, `/bank` read/write of `commands.tsv` | real |
| action routing: tier-0 immediate argv, tier-2 pending + `POST /act/confirm` | real |
| OS effects: marker files, text typed into the real SinkEdit Tk window on `:97` | real X11 side effects |
| camera / MediaPipe / adb | **absent on this box** — that is exactly why the replay seam exists |

`web/js/pose.js` already ships the seam: when `http://127.0.0.1:8877/health`
answers, the page replaces its camera source with frames pulled from the
replay service and POSTs them to the daemon as `/live/kp` — byte-for-byte the
same request shape MediaPipe frames produce. The demo never bypasses the
page, the auth, or the daemon.

## Layout

```
tools/demo/
  boot.sh              idempotent env bring-up → /tmp/wf-demo/env (mode 600)
  run_all.sh           orchestrated scenes + per-scene recordings → proofs/demo/
  record.sh            ffmpeg x11grab of :97 → mp4 (SIGINT = valid moov)
  lib.sh               shared helpers (authed calls, replay queue, waits, rec)
  make_clips.py        clip generator + offline endpointer/signbank self-check
  replay_split.py      :8877 drop-in: per-Origin queues (shared-box isolation)
  feed.py              direct daemon clip feeder (debug path — bypasses chrome)
  type_into_sink.sh    argv target for the TYPE-HELLO binding (xdotool → sink)
  evidence_terminal.sh ticker pane shown beside chrome in the recordings
  clips/*.json         {"frames":[{"points":[42 keypoints]}]}
```

## Run it

```bash
# from the repo root — one command does everything:
bash tools/demo/run_all.sh
```

That single command:
1. (re)boots the realrun stack on Xvfb `:97` + the real daemon on `:8874`
   (`tools/realrun/setup.sh`), ensures `replay_split.py` answers `:8877`,
   and launches google-chrome windowed 1280x800 at the composer URL.
2. navigates the real page to `#/live`, proves the page is pulling replayed
   frames (replay `/stats` served counter grows).
3. runs seven recorded scenes — see below — teeing every curl + daemon
   reply into `proofs/demo/run.log`.
4. writes `proofs/demo/RESULT` (`VERIFIED n pass / m fail`) and exits
   non-zero on any failure.

Re-runnable: markers are cleared, glosses are re-taught through the real
`/teach` endpoints, recordings are overwritten.

```bash
bash tools/demo/run_all.sh --no-boot   # reuse an already-booted env
bash tools/demo/boot.sh                # boot only (leaves chrome open)
bash tools/demo/boot.sh --teardown     # kill demo chrome + demo replay
bash tools/demo/record.sh myname 12    # ad-hoc 12s capture of :97
python3 tools/demo/make_clips.py       # regenerate + re-verify clips
python3 tools/demo/feed.py clips/palm-hold.json   # debug: bypass chrome
```

## Scenes (each records proofs/demo/<scene>.mp4)

| scene | shows | asserts |
|---|---|---|
| `01-listen` | palm-hold clip → `PALM-OPEN` → `listening=true`; namaste → `listening=false` | `GET /live/state` |
| `02-gate` | same sign performed while not listening | no marker, no commit — the gate gates |
| `03-teach` | `POST /teach/begin` + 3-take clips for OPEN-NOTES, TYPE-HELLO, LOCK; `PUT /bank` binds argv | `/bank` shows 3 exemplars + bound argv |
| `04-perform` | palm → OPEN-NOTES → `/usr/bin/touch` marker; TYPE-HELLO → xdotool types into real SinkEdit | marker file + `sink.txt` grows |
| `05-reject` | untrained squiggle | `last_match.accepted=false`, `reason=threshold` |
| `06-confirm` | LOCK (tier 2) parks in `pending`; marker absent until `POST /act/confirm` | pending row, then marker after confirm |
| `07-namaste` | stop listening + final `/bank` `/act` dump | `listening=false` |

## Signs taught by the demo

| gloss | tier | argv (run verbatim, no shell) | observable effect |
|---|---|---|---|
| `OPEN-NOTES` | 0 | `/usr/bin/touch /tmp/wf-realrun/markers/open-notes` | marker file |
| `TYPE-HELLO` | 0 | `bash tools/demo/type_into_sink.sh hello-from-the-demo-sign` | text lands in the SinkEdit window → `/tmp/wf-realrun/sink.txt` |
| `LOCK` | 2 | `/usr/bin/touch /tmp/wf-realrun/markers/lock-confirmed` | **only** after `POST /act/confirm {"id":…}` |

The motion paths are custom per-lane shapes (dome arc, two-hump wave,
square trace) so they do not collide with signs other demo lanes may have
taught into a shared bank.

## Shared-box isolation (why `replay_split.py` exists)

The composer hardcodes `127.0.0.1:8877`. When several composer pages share
one box, a singleton `/pull` queue cross-leaks clips between lanes.
`replay_split.py` is a drop-in superset of `tools/demo-rec/replay_server.py`:
pushes may carry `X-Replay-Route: <tag>` (this lane uses the daemon port,
`8874`); each puller drains the queue named by its request `Origin` port —
Chrome sends `Origin: http://127.0.0.1:8874` on the page's cross-origin
`/pull` fetches. Untagged pushes and unmodified consumers keep working on
the `default` queue, byte-for-byte upstream behaviour.

## Environment / secrets

- Runtime state lives in `/tmp/wf-demo/` (`env` mode 600) and
  `/tmp/wf-realrun/` (created by `tools/realrun/setup.sh`).
- The pairing key is read from the realrun env by `lib.sh`'s `call()` and
  is passed to chrome on its first URL (`?k=`); the page immediately moves
  it to localStorage via `history.replaceState`. It is **never** written to
  `proofs/` — `run.log` is checked to be key-free before it is copied to
  `proofs/demo/run.log`.
- Recordings land in `proofs/demo/*.mp4` (libx264 crf22 yuv420p, 15 fps
  x11grab of `:97`), plus `run.log` (every curl + response) and `RESULT`.

## Expected output

```
[demo …] ===================== RESULT: 11 pass / 0 fail =====================
VERIFIED	11 pass / 0 fail — replayed keypoints through chrome -> real wfd.py :8874 -> real OS effects
```

with `proofs/demo/` holding `01-listen.mp4` … `07-namaste.mp4`, `run.log`,
and `RESULT`. Verified end-to-end on this box: 11/11 checks pass, all clips
delivered through the real chrome page — no direct daemon feeding anywhere
in `run_all.sh` (the only simulated element is the camera's keypoints).

The `05-reject` assertion accepts any honest rejection — usually
`reason=threshold` (nearest sign beat the clip under DTW but lost on
distance) or `reason=thin-query` when a frame dropped mid-clip left the
segment too short to score. Both are `last_match.accepted=false`.

## Verified on the merged build (2026-10-26, merge tip @ d78d2f9)

`bash tools/demo/run_all.sh` → `VERIFIED 11 pass / 0 fail`. Chapter
integrity checked per file: `ffprobe` reports valid h264 1280x800 with
real durations (1.6s–34.6s), and start/mid frames extracted with ffmpeg
show full pixel range (no blank frames) and the polished app + evidence
terminal on screen — no developer-mode screens.

| chapter | mp4 | proves |
|---|---|---|
| 01-listen | 01-listen.mp4 | palm-hold → `PALM-OPEN` → `listening=true`; namaste → `false` (`/live/state`) |
| 02-gate | 02-gate.mp4 | same sign while not listening → no marker, no commit |
| 03-teach | 03-teach.mp4 | real `/teach/begin`+3-take clips ×3 glosses + `PUT /bank` argv binds; `/bank` shows 3 exemplars each |
| 04-perform | 04-perform.mp4 | OPEN-NOTES → `/usr/bin/touch` marker; TYPE-HELLO → xdotool → real SinkEdit text (`sink.txt`) |
| 05-reject | 05-reject.mp4 | untrained squiggle → `accepted=false` (open-set rejection) |
| 06-confirm | 06-confirm.mp4 | LOCK (tier 2) parks `pending`; marker only after `POST /act/confirm` |
| 07-namaste | 07-namaste.mp4 | stop listening + final `/bank` `/act` dump |
