# BENCH.md — merged-product numbers, measured on the SHIPPED stack

Everything below is a **measured** number on the real product modules —
`server/endpointer.py` + `server/signbank.py` driven through the exact
`{"t_ms","points":[{x,y,score} x42]}` wire protocol `wfd.py` serves at
`/live/kp`, routed through a mock `commands.tsv` (journal-only spawn).
Harness: `tools/live-eval/run_eval_sb.py` (this commit). Proofs:
`tools/live-eval/proofs/sb_{teach,false_accept,latency,load}.json`
(gitignored local evidence — regenerate with the commands at the bottom;
deterministic, `--seed 7`, ~17s total).

**Sensor = synthetic motion.** Keypoints come from `tools/live-eval/synth.py`
(seeded MediaPipe-shape renderer, 640x480 px, kp jitter sigma 1.2 px/frame,
independent per frame). This is a *synthetic-motion* result: it exercises
the real segmentation/match/route code paths end to end but says nothing
about a human hand on a real camera. Single synthetic signer — "per-user"
is the protocol shape, not a human-subjects claim. See LIMITATIONS.md.

**Box:** Linux 6.12.94+ x86_64 VM, Intel Xeon, 8 cores, 15 GB RAM,
CPython 3.13.5. Wall-clock numbers are this box's, not a laptop's.

**Two endpointer regimes** (same methodology as the old harness's floor
calibration — measure the still-motion floor of THIS sensor, sit the
hysteresis above it):

- `shipped` — `Endpointer()` defaults (enter 1.5 / exit 0.7
  palm-widths/s, quiet 0.35 s, max-seg 3.0 s). On this sensor still-hand
  EMA sits at p50 **5.4–5.9** vs exit_v 0.7 — the segmenter is
  permanently "active"; a sign only "ends" at the 3.0 s force-close.
  Run in its real regime: one continuous stream, blob segments
  attributed to the sign windows they overlap.
- `kp` — calibrated for this sensor: `enter_v=14.8`
  (still-EMA p99 9.85 x1.5), `exit_v=9.4` (p95 7.84 x1.2), pre_roll 8.

---

## 1 — Per-user taught-sign recall (headline)

Protocol: teach N motion-distinct signs x 3 takes through the real teach
path (each emitted segment -> `add_exemplar`, capped at takes, mirroring
`_on_segment`'s `got` counter), then replay held-out performances
(disjoint seeds) and score the FIRST segment per sign window.
Thresholds: shipped default `0.6`, per-bank `calibrate_threshold`
(labeled cal set = cal-seed positives + cal-seed idle negatives,
`max_bad_accepts=0`), and the shipped `consensus=True` accept rule.

### kp-calibrated endpointing

| N | variant | answer rate | precision (acc when answered) | recall (correct/all) | **wrong-sign rate** | no-segment |
|---|---------|------------:|------------------------------:|---------------------:|--------------------:|-----------:|
| 5  | veto @0.6        | 0.900 | 1.000 | 0.900 | **0.000** | 0/20 |
| 5  | veto @cal (0.190)| 0.350 | 1.000 | 0.350 | 0.000 | 0/20 |
| 5  | consensus @cal   | 0.100 | 1.000 | 0.100 | 0.000 | 0/20 |
| 10 | veto @0.6        | 0.950 | 0.763 | 0.725 | **0.225** | 1/40 |
| 10 | veto @cal (0.190)| 0.225 | 1.000 | 0.225 | 0.000 | 1/40 |
| 10 | consensus @cal   | 0.025 | 1.000 | 0.025 | 0.000 | 1/40 |
| 20 | veto @0.6        | 0.925 | 0.838 | 0.775 | **0.150** | 5/80 |
| 20 | veto @cal (0.133)| 0.100 | 1.000 | 0.100 | 0.000 | 5/80 |
| 20 | consensus @cal   | 0.013 | 1.000 | 0.013 | 0.000 | 5/80 |

### shipped endpointing (continuous regime — its only working regime)

| N | variant | answer rate | precision | recall | wrong-sign | no-segment |
|---|---------|------------:|----------:|-------:|-----------:|-----------:|
| 5  | veto @0.6 | 0.150 | 1.000 | 0.150 | 0.000 | 10/20 |
| 10 | veto @0.6 | 0.150 | 0.667 | 0.100 | 0.050 | 19/40 |
| 20 | veto @0.6 | 0.138 | 0.455 | 0.062 | 0.075 | 36/80 |
| all| veto/cons @cal | 0.000 | — | 0.000 | 0.000 | — |

(`cal` collapses to ~0.136: the blob cal-set's min-positive distance is
tiny, so the safe threshold accepts almost nothing. Confusion under kp
veto@0.6 is concentrated where the trajectory channel is genuinely
line-like: `HOOK->SWIPE-R` x3, `ZIGZAG->SWIPE-R` x2, `V-DRAW->CIRCLE-CCW`
x2, plus `TRIANGLE->SQUARE/CIRCLE-CW`, `SWIPE-R->ZIGZAG/U-DRAW` at N=20 —
see `sb_teach.json` per_eval rows.)

**The bar: near-100% answer @ <1% wrong — NOT MET** (synthetic
motion). No measured operating point clears it: at the default 0.6 the
bank answers ~92–95% but **guesses wrong 15–22.5%**; the calibrated
threshold and consensus rule reach 0% wrong only by dropping the answer
rate to 1–35%. The wrong-answer sin is the fused bank's: the shape
channel (bbox-normalized flatten, dim-weight 0.1) is too weak to pull the
winner when the traj channel argmin is wrong — `signbank.py`'s own
PARITY NOTE predicted exactly this ("if confusion appears, port the
engineered descriptor"). Both sins now have numbers.

## 2 — False accepts on non-sign motion

T = 600 s simulated idle stream (scrolling / waving / fidget / resting
distractors, 30 fps, seed disjoint from calibration) through the live
pipeline, bank = VOCAB10 x3 takes. A *fire* = a segment accepted by the
bank and routed to a command row.

| mode | rule/threshold | segments | fires | **fires/hour** |
|------|----------------|---------:|------:|---------------:|
| kp | veto @0.6        | 271 | 178 | **1067.5** |
| kp | veto @cal (0.190)| 271 | 6   | **36.0** |
| kp | consensus @cal   | 271 | 0   | 0.0 |
| shipped | veto @0.6 (3s blob segs) | 193 | 35 | 209.9 |
| shipped | veto @cal (0.136)      | 193 | 0 | 0.0 |
| shipped | consensus @cal         | 193 | 0 | 0.0 |

Mode events (PALM-OPEN / NAMASTE holds): **0** in both regimes — on this
sensor the hold-speed gate (`hold_v=0.6`) sits under still-jitter hand
speed too, so the listen toggle is a MISS, not a false fire.

**NOT MET** for production: at the shipped default 0.6, normal desk
motion fires ~1 routed action every **3.4 s** of simulated idle
(1067/hr). The calibrated threshold gets to ~36/hr — still one stray
fire per 100 s — and consensus to 0/hr but (arm 1) at a recall cost.
Distance distribution of the fires spans 0.09–0.60: distractor arcs
genuinely land inside the taught shapes' neighborhood, not just at the
threshold edge.

## 3 — Sign-end → action latency (this box)

Per held-out segment: `detect_lag` = segment close − ground-truth sign
end (virtual ms, the endpointer's designed quiet wait), `decide` =
wall-clock `bank.match` + route + mock spawn. NOT included (stated, not
hidden): keypoints→daemon LAN POST (~3–10 ms PRD), real argv spawn
(~10–40 ms), camera→keypoint browser time.

| mode | detect p50/p95 (virtual) | match+route decide p50/p95 | sign-end→decide p50/p95 |
|------|-------------------------:|---------------------------:|------------------------:|
| kp      | 500 / 733 ms | **4.08 / 5.64 ms** (max 7.37) | 503.9 / 738.0 ms |
| shipped | 2201 / 2835 ms | 3.17 / 3.36 ms (max 3.46) | 2204 / 2838 ms |

**Decision latency MET** (bar <10 ms): the pure-Python fused-DTW match +
route on a 10-gloss x3 bank is ~4 ms p50 on a Xeon VM — the compute is
not the bottleneck. The end-to-end sign-end→action number is dominated
by the endpointer's quiet window (designed ~350 ms + measured EMA decay
→ ~500 ms p50); under shipped defaults it is ~2.2 s p50 because only the
3.0 s max-seg force-close fires. Lowering `quiet_s`/`exit_v` trades
directly against the false-accept arm — measured, not hidden.

## 4 — App shell load time (real `wfd.py` static serve)

Spawned the shipped daemon, fetched the full shell asset list a phone
pulls (every `src`/`href` in index.html + the sw.js precache set):
**28 assets, 34.93 MB** (26.3 MB wasm + 7.8 MB hand_landmarker.task).
Loopback — server-path only; phone-on-LAN deltas stated below.

| metric | value |
|--------|------:|
| daemon start → /health | 165.6 ms |
| cold fetch total (28 assets, first ever) | 107.8 ms |
| warm per-asset p50 (small files) | ~0.5 ms |
| cold vendor wasm .wasm files | 30.6 / 26.1 ms |
| full-shell sequential p50/p95 | **55.5 / 57.9 ms** |

`service_worker` present, `Cache-Control` headers: **none** — "warm"
above is a real repeat TCP+HTTP fetch (wfd is HTTP/1.0, closes each
connection), not a cache hit. On the phone the service worker makes the
34.9 MB vendor payload a one-time cost (cache-first precache);
repeat loads pull only the ~0.2 MB shell. What loopback does NOT
include: LAN RTT (~2–20 ms wifi) and radio throughput — at ~50 Mbps the
first-ever 34.9 MB pull is ~5.6 s, at ~300 Mbps ~0.9 s; per-asset RTT
adds ~ms each over loopback. Also found: `index.html` references
`icon-180.png` which does not exist in `web/` — a real (cosmetic) 404
on the touch icon.

---

## 0 — Learned encoder vs DTW baseline (held-out signer benchmark)

Trained on Kaggle GPU (`signflow-fewshot-bench`); 300-gloss pool,
signer-disjoint splits, n=600 closed-set draws per k. Full details:
`signflow-devin/kaggle-jobs/sf-fewshot-bench/RESULTS.md`.

| shots | encoder top-1 | DTW-1NN top-1 | ratio |
|------:|--------------:|--------------:|------:|
| k=1 | .637 | .230 | 2.8x |
| k=2 | .743 | .277 | 2.7x |
| k=3 | .770 | .310 | 2.5x |
| k=4 | **.788** | .337 | 2.3x |

Reproduced across seeds (2026 seed run: k4 .780 vs .337; 150-gloss v3:
k4 .795 vs .351). Capacity is data-bound: d192 400ep .788 ≈ d128 200ep
.795. Open-set (episodic, taught+negatives): tau_margin FAR **0** at
k4 with recall .688 — honest abstain, not free acceptance.
Same-signer control shows DTW's gains are signer memorization
(same .487 vs diff .326 @k4); the encoder generalizes (.765/.790).

The kp42 (browser wire-format) variant (`signflow-fewshot-mp`,
held-out signers 7–10): k=1 enc **.923–.960** vs DTW .813–.941;
k=3 enc .959–.992. Exported `enc42_kp42.onnx` (+int8, ~1.08 MB).

### CTC sign-to-text lane (`signflow-ctc-v2`, 29.2M params)

Warm-start resume chain (v8: steps 6937→10063, +.02 WER gain):
held-out main_s1.5 **.734**, open_s1.5 **.739** WER; val .45.
fp32 ONNX exported (117 MB); int8 written (30.6 MB) but ORT
ConvInteger runtime verify is a known limitation, not a training
failure. Detail: `signflow-devin/kaggle-jobs/sf-ctc-v2/RESULTS.md`.

These are OFFLINE-EVAL numbers on real public datasets — not yet wired
into the live hot path (DTW stays the shipping matcher until the
encoder lands on-device).

## The honest summary vs the bars

| bar | result |
|-----|--------|
| ~100% answer @ <1% wrong | **NOT MET** (synthetic). Best: 95% answer / 22.5% wrong @0.6, or 0% wrong at ≤35% answer @cal. |
| decision <10 ms | **MET** — match+route p50 4.1 / p95 5.6 ms |
| sign-end→action (designed) | **NOT MET** at UX scale — detect lag p50 500 ms kp / 2201 ms shipped (quiet-hold is the design; compute is 4 ms) |
| false-accepts | **NOT MET** at 0.6 (1067/hr); 36/hr @cal; 0/hr only under consensus which trades recall to ~2.5% |
| shell load | ~55 ms p50 loopback (varies a few ms run to run); one-time 34.9 MB vendor pull on LAN ≈ 1–6 s depending on wifi; SW-cached thereafter |

## Real defects surfaced (measured, not opinion)

1. `server/endpointer.py` shipped thresholds are calibrated for a sensor
   whose still EMA is <0.7 palm-widths/s; on the declared kp42 jitter
   (1.2 px) still EMA is ~5.9 — the product as configured cannot close a
   segment before the 3.0 s force-close. A deployed daemon needs the
   `calibrate_ep`-style still-floor measurement this harness runs
   (or a noise model at the source).
2. Fused bank shape channel is too weak to separate traj-similar signs
   (HOOK/ZIGZAG vs SWIPE-R); veto rule guesses, consensus starves.
   `signbank.py` PARITY NOTE names the fix (port the engineered 19-dim
   descriptor) — now backed by the confusion data in `sb_teach.json`.
3. `web/index.html` → `icon-180.png` 404 (cosmetic).

## Reproduce (deterministic, ~17 s total, --seed 7)

```bash
python3 tools/live-eval/run_eval_sb.py --arm all --seed 7        # all four arms
python3 tools/live-eval/run_eval_sb.py --arm teach --seed 7      # headline only
python3 tools/live-eval/run_eval_sb.py --arm false-accept --seed 7 --idle-seconds 600
python3 tools/live-eval/run_eval_sb.py --arm latency --seed 7
python3 tools/live-eval/run_eval_sb.py --arm load --seed 7
```

Proofs land at `tools/live-eval/proofs/sb_{teach,false_accept,latency,load}.json`
(gitignored — same policy as the older harness's proofs; the pre-existing
contract-engine proofs `teach/false_accept/latency/load.json` remain in
git history for the traj-only reference bank: N=10 kp .950 answer @ 1.000
precision — the gap vs this stack is the fused bank's shape story, not DTW).

Seed knobs: `--kp-noise 1.2 --takes 3 --heldout 4 --cal-seconds 90`.
