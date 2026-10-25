# Sign engine v2 — measured on real signers (kiro/night-text)

`server/signbank2.py` is a drop-in for `server/signbank.py` with a calibrated, open-set recognizer inside. This page is
the evidence for it and the honest list of what it does **not** show. Every number below comes from
`proofs/kiro-night/text/py-engine/` in the sign-flow repo (branch `kiro/night-text`), reproduced with
`tools/text/py_engine/engine_bench.py`. The keypoints are LSA64 (see "What the data is").

## What changed in the daemon (small, opt-out)

* `wfd.py` picks the engine per bank file: a **new or v2 bank → v2** (needs `numpy`), a bank file already in the v1
  format **keeps v1** (nothing the user taught is lost). `WF_BANK_ENGINE=auto|v1|v2` overrides; `v2` over a v1 file moves
  it aside to `signbank.v1.json`. `GET /engine` reports `python-dtw2` or `python-dtw` — what is really serving.
* `/live/kp` forwards `body` (the laptop camera's 11 upper-body joints) and `aspect`/`w`/`h` into the segment frames.
  Before this the daemon dropped them, so a body-aware engine could never see them.
* Teach feedback: a refused take carries `teach.issue` ("too short - sign a little slower"), and the take that finishes a
  sign carries `teach.conflict` when it looks like a sign already taught ("GOVINDA looks like NAMASTE"):
  `{"other","separation","level":"similar|conflict","none"}`. The UI can show it before the user moves on.
* Tests: `server/tests/test_signbank2.py` (18, incl. the vectorised DTW pinned to a plain reference DP) and
  `server/tests/test_engine_v2.py` (7, a real `wfd.py` on a throwaway HOME: teach through the endpointer → perform →
  `/live/state` match, look-alike warning, laptop-camera wire, v1 file untouched). The existing 156-test server suite
  still passes.

## What v2 is

Per-frame features on a 15 Hz grid (position, velocity, a 14-d hand-shape descriptor in which hand orientation carries the
weight), the sign cut out of the take, banded DTW, and a threshold **per sign** learned from the user's own takes
(leave-one-out plus drift-perturbed probes). A match is accepted only if it is under the sign's own threshold, at least
1.5× closer than the nearest other sign and than the user's recorded everyday movement, with enough hand in view and
enough movement (a held pose is not a sign). Two camera modes, fixed by the first sign taught:
**hands** (phone camera: palm position relative to the sign's own mean) and **body** (laptop camera: wrist relative to the
shoulders, like the Rust spotter). One decision ≈ 3–10 ms for a 12-sign bank (numpy, one pass over all templates).

## Numbers

Protocol (the product's own): one person teaches **k = 3** takes of 12 random signs through the real `endpointer.py`; the
same person then signs the remaining repetitions of those 12 signs and of 16 signs **never taught**, each gesture in its
own stream with rest around it, through the same endpointer. Dev signers 1–5 for development; **signers 7–10 held out,
configuration frozen before the run, run once** (signer 6 excluded: looked at while exploring). 40 units per run,
960 taught-sign items, 1,280 never-taught items, 156 min of stream. 95 % intervals are in the proof files.

| engine on the same streams | taught: typed RIGHT | typed WRONG | missed | never-taught signs accepted |
|---|---|---|---|---|
| v1 **before** the normalized-coordinate fix (33a7bb6), thr 0.6 | 90.3 % | **6.6 %** | 3.1 % | **73.0 %** |
| v1 now (6598f00+), thr 0.6 = the shipped default | 85.4 % | 0.7 % | 13.9 % | **7.2 %** |
| v1 now, consensus rule | 78.9 % | 0.1 % | 21.0 % | 3.7 % |
| v1 now, thr 0.4 (swept on the test data — descriptive) | 67.7 % | 0.3 % | 32.0 % | 0.8 % |
| **v2 hands** (phone camera) | 66.6 % | 0.1 % | 33.3 % | 0.8 % |
| **v2 body** (laptop camera, `body` on the wire) | **84.6 %** | **0.0 %** | 15.4 % | **0.9 %** |

Dev signers (1–5, 1,200 / 1,600 items) tell the same story: v1 before the fix 94.8 / 3.8 / 79.7 %, v1 now 85.3 / 0.9 /
13.2 %, v2 hands 73.8 / 0.3 / 1.6 %, v2 body 82.6 / 0.0 / 0.8 %. Closed-set (nearest template, no rejection) is 95–97 % for
every engine — the distance was never the problem, the accept rule was.

How to read this honestly:

1. The fix that mattered most is the one already merged (the pixel floor in the stillness gate): without it the
   engine typed the wrong sign 6.6 % of the time and accepted 73 % of signs it had never been taught.
2. **On the phone path (hands only) v2 is no better than v1 with a tighter threshold** (66.6 % / 0.8 % vs 67.7 % / 0.8 %).
   v1's shipped 0.6 trades 7.2 % false accepts for 18 more points of recall. If the phone path stays on v1, the
   threshold is the knob, and 0.4–0.5 is where real keypoints put it. v2's remaining value there is structural:
   per-sign thresholds, the teach-time look-alike warning, the everyday-movement class.
3. **The step change is body mode** (laptop camera): the same recall as v1's loose default with 8× fewer false accepts and no
   wrong sign in 960 items. It needs the shoulders in view and the `body` block to reach the bank (the daemon change above).

## What the data is, and what is NOT measured

* LSA64 (Argentine Sign Language, CC BY-NC-SA 4.0): 64 signs, 10 signers, 5 repetitions, studio video, **gloved hands**,
  keypoints from RTMW-L at 15 Hz mapped 1:1 onto the kp42 wire (same 21-joint order as MediaPipe). **Not MediaPipe output,
  not user-invented control gestures.** A random 12 of 64 includes look-alike pairs, so this is a stress test for
  "10–30 motion-distinct gestures", not a copy of it. Keypoints stay outside git; only numbers are published.
* **Same sitting only.** Teach and test are minutes apart. Camera moves, other light and other days are NOT measured on real
  data; the per-sign thresholds are built for it (drift probes) but that is a design claim, not a result.
* **False accepts are per never-taught real sign, not per hour of desk movement.** There is no real idle-movement
  recording in the data; "accepted in rests" was 0–3 per 156 min but those streams contain no scratching or typing. No
  per-hour figure is claimed.
* Jitter (dev signers 1-5 only, 6 draws, white noise on every hand joint as a % of shoulder width; webcams measure about
  1.3 % median, 2.1 % p75): at +1.3 % v1-now answers 87.6 % right but accepts 15.3 % of never-taught signs; v2 hands
  69.4 % right / 0.8 % accepted; v2 body 75.4 % / 0.2 %. At +2.5 %: v1-now 87.8 % / 18.3 %, v2 hands 58.6 % / 0.8 %,
  v2 body 67.8 % / 0.1 %. Noise makes v2 go quiet (recall falls 7-17 points) and makes v1 less selective; neither is
  fixed by this patch. Files: `proofs/kiro-night/text/py-engine/dev-jitter/`.
* Segmentation is the endpointer's: 1,440 teach takes became 1,499 segments (4 % fragments), and 2.5 % of test gestures
  produced two segments. Movement gestures only — a static pose never makes a segment.

## Reproduce

```
LSA_KP=/path/to/lsa64-kp WRAP_SERVER=/path/to/wispr-wrap/server \
python3 tools/text/py_engine/engine_bench.py --engine v2 --body --send-aspect --signers 7,8,9,10 --draws 10 --k 3 \
        --sentence 1 --pad-back 20 --blend 0 --out out/ --stem heldout-v2-body
# --engine v1 [--v1-file old/signbank.py] for the shipped engine; --engine v2 (no --body) for the phone path
```
Each report records the sha256 of the engine files it ran against.
