# Offline story — what loads, when, and from where

Status: **vendored + service worker shipped** (Oct 25 2026).
Vendor fetch state: **FETCHED and hash-verified** — `tools/fetch-vendor.sh --check` is green.
Nothing is fetched from the internet at runtime, ever.

## The two meanings of "offline" here

1. **No internet** — always true by design. Everything the composer needs is
   served by `wfd.py` from this repo: pages, `vendor/mediapipe/*` (34 MB), the
   model. This works on every transport (LAN, USB `adb reverse`, tunnel) with
   zero extra machinery.
2. **Instant warm load / survive reloads** — needs `web/sw.js`. The daemon sends
   no cache headers, so without a service worker the phone revalidates ~34 MB of
   wasm/model on every visit. With it, the second load is a cache hit and the
   MediaPipe blobs never cross the wire again.

## What the service worker does (`web/sw.js`)

- **install:** precaches the shell (`/`, `index.html`, `pair.html`,
  `manifest.webmanifest`, `icon.svg`, app icons, and every `js/*.js` +
  `css/*.css` module — tolerant of not-yet-landed files) plus all of
  `vendor/mediapipe/`.
- **`vendor/**` + shell: cache-first**, refreshed in the background
  (stale-while-revalidate) — warm loads are near-instant.
- **`/health`: network-first, never stored.** A cached `ok` would claim the
  daemon is alive when it isn't; the offline answer is an honest `503`.
- **API/auth endpoints (`/pair*`, `/send*`, `/live/*`, `/teach/*`, `/bank`,
  `/act*`): network-only.** Pairing keys, keypoint frames, and bank contents are
  never written into a cache.
- `VERSION` constant at the top of `sw.js` — **bump it on every shell/vendor
  change**; it rotates the cache name so phones pick up new code.

## The secure-context catch (read this before demoing on a phone)

Service workers **and `getUserMedia`** both require a *secure context*. A plain
`http://192.168.x.x:8741` LAN URL is **not** one — Chrome and Safari will refuse
`navigator.serviceWorker.register` and the camera alike. Live mode over LAN-http
is blocked by this, full stop — not a bug we can code around.

Workarounds, in order of preference:

| path | how | context |
|---|---|---|
| **USB / `adb reverse`** | `adb reverse tcp:8741 tcp:8741`, open `http://localhost:8741` | localhost **is** a secure context — SW + camera work |
| **cloudflared tunnel** | `wrap tunnel`, open the `https://*.trycloudflare.com` URL | https — SW + camera work (inference still stays local) |
| Chrome dev flag | `--unsafely-treat-insecure-origin-as-secure=http://PHONE-IP:8741` | dev boxes only; does not exist on Android Chrome |

The service worker's scope is the whole origin (`/`), since `wfd.py` serves
`sw.js` from the web root — no `Service-Worker-Allowed` header needed.

## Registering the SW (for the composer owner — W2a)

`index.html` needs this once, anywhere before `</body>`:

```html
<script>
if ("serviceWorker" in navigator)
  addEventListener("load", () =>
    navigator.serviceWorker.register("/sw.js").catch(() => {}));
</script>
```

The `.catch` keeps insecure-context pages (LAN-http) working exactly as today —
the composer functions, just without warm-load caching.

## Vendored MediaPipe

`web/vendor/mediapipe/` holds `@mediapipe/tasks-vision@1.1.1`
(`vision_bundle.mjs` + SIMD and no-SIMD wasm builds) and the float16
`hand_landmarker.task` model. Sources and sha256 pins: `vendor/mediapipe/MANIFEST`.
Provenance/license: `vendor/mediapipe/NOTICE.md`. Re-fetch/verify:
`tools/fetch-vendor.sh`. The loader usage W3 needs:

```js
import { FilesetResolver, HandLandmarker } from "/vendor/mediapipe/vision_bundle.mjs";
const vision = await FilesetResolver.forVisionTasks("/vendor/mediapipe/wasm");
const hands = await HandLandmarker.createFromOptions(vision, {
  baseOptions: { modelAssetPath: "/vendor/mediapipe/models/hand_landmarker.task" },
  numHands: 2, runningMode: "VIDEO"
});
```
