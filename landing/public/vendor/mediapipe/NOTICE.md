# Vendored: MediaPipe Tasks Vision (hand landmarker)

These files are vendored so the composer can run hand-pose inference fully
offline — no CDN, no runtime fetch (see `web/OFFLINE.md` and the merged-product
PRD §3). Do not edit them in place; update via `tools/fetch-vendor.sh`.

| file | upstream | license |
|---|---|---|
| `vision_bundle.mjs`, `wasm/*` | `@mediapipe/tasks-vision@1.1.1` npm package (jsdelivr mirror) | Apache-2.0, © Google |
| `models/hand_landmarker.task` | `storage.googleapis.com/mediapipe-models/.../float16/1/` | MediaPipe model license (Google) |

Exact URLs + sha256 pins live in `MANIFEST` (same directory). Fetch and verify:

```sh
tools/fetch-vendor.sh          # download missing/changed files, verify hashes
tools/fetch-vendor.sh --check  # verify only, no network
```

Layout the loader expects (`FilesetResolver.forVisionTasks(root)` + model path):

```
vendor/mediapipe/
  vision_bundle.mjs              # import { FilesetResolver, HandLandmarker } from this
  wasm/                          # pass this dir to forVisionTasks()
    vision_wasm_internal.{js,wasm}        # SIMD build (loaded on capable devices)
    vision_wasm_nosimd_internal.{js,wasm} # fallback for older phones
  models/hand_landmarker.task    # ~7.5MB float16 hand landmarker, 21pt/hand
```
