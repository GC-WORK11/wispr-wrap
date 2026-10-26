/* sw.js — offline shell for the Wispr Wrap composer.
 *
 * Bump VERSION on every shell/vendor change: it busts the old cache on
 * activate, so phones get fresh code without a manual clear.
 *
 * Strategy:
 *   install   precache SHELL + VENDOR (tolerant — missing files don't fail install)
 *   vendor/** cache-first  (big wasm/model blobs: fetch once, instant forever)
 *   shell     cache-first, refreshed in background (stale-while-revalidate)
 *   /health   network-first, never stored — a cached "ok" would lie about a
 *             dead daemon, so the offline fallback is an honest 503
 *   API/auth  network-only, never cached (keypoint/bank data must not persist)
 *   everything else same-origin: stale-while-revalidate
 */
const VERSION = "wf-shell-v20-peak";
const CACHE = `wispr-wrap-${VERSION}`;

const SHELL = [
  "/", "/index.html", "/pair.html", "/manifest.webmanifest", "/icon.svg",
  "/icon-192.png", "/icon-512.png", "/icon-maskable-512.png",
  "/apple-touch-icon.png",
  // merged module app — every lane lives under js/ + css/
  "/css/app.css", "/css/compose.css", "/css/live.css", "/css/teach.css",
  "/css/actions.css", "/css/macro.css", "/css/library.css", "/css/panes.css",
  "/js/app.js", "/js/flower.js", "/js/compose.js", "/js/pose.js", "/js/skeleton.js",
  "/js/live.js", "/js/actions.js", "/js/macro.js", "/js/teach.js",
  "/js/library.js", "/js/panes.js",
  "/js/laptop.js", "/js/settings.js", "/js/drawer.js", "/css/drawer.css",
  "/js/sound.js",
  // BOOT stage: loading screen + packs shell files (sfx.js unused —
  // sound.js is the loaded sound module)
  "/css/boot.css", "/css/packs.css", "/js/boot.js", "/js/packs.js"
];
const VENDOR = [
  "/vendor/mediapipe/vision_bundle.mjs",
  "/vendor/mediapipe/wasm/vision_wasm_internal.js",
  "/vendor/mediapipe/wasm/vision_wasm_internal.wasm",
  "/vendor/mediapipe/wasm/vision_wasm_nosimd_internal.js",
  "/vendor/mediapipe/wasm/vision_wasm_nosimd_internal.wasm",
  "/vendor/mediapipe/models/hand_landmarker.task"
];

/* endpoints that must never be served from cache */
const API_PATHS = /^\/(health|pair-info|pair|send|send-image|send-file|live|teach|bank|act)(\/|$)/;

function precache(cache, urls) {
  /* add() fails the whole install on one 404 — use per-url fetch+put so
     not-yet-landed files (teach.html, live.js) don't break offline prep */
  return Promise.allSettled(urls.map(async (u) => {
    const r = await fetch(u, { cache: "no-store" });
    if (r.ok) await cache.put(u, r);
  }));
}

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => precache(c, SHELL.concat(VENDOR)))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k.startsWith("wispr-wrap-") && k !== CACHE)
            .map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (e) => {
  if (e.data && e.data.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin) return;

  /* network-only for auth'd/live endpoints — never persist pairing keys,
     keypoint frames, or bank contents */
  if (API_PATHS.test(url.pathname) && url.pathname !== "/health") {
    e.respondWith(fetch(e.request));
    return;
  }

  /* /health: network-first per spec, but never cache the answer — offline
     must read as offline, not as a stale "ok" */
  if (url.pathname === "/health") {
    e.respondWith(
      fetch(e.request).catch(() => new Response("offline", { status: 503 }))
    );
    return;
  }

  /* vendor + shell: cache-first (these are exactly the precached set) */
  e.respondWith(
    caches.match(e.request).then((hit) => {
      const refresh = fetch(e.request).then((r) => {
        if (r.ok) {
          const clone = r.clone();
          caches.open(CACHE).then((c) => c.put(e.request, clone));
        }
        return r;
      }).catch(() => hit); // offline and not yet cached → undefined → error below
      return hit || refresh.then((r) => r || Response.error());
    })
  );
});
