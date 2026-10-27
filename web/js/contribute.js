// contribute.js — opt-in training samples (beta).
//
// When the user turns on "Share training samples (beta)", each Teach take
// is reduced to hand landmarks (x, y, z per point) and kept in a capped
// queue on this phone. Nothing else is stored: no video, no images, no
// audio, no device or account identifiers. The only id is a random value
// made on this phone when sharing is turned on, and it is deleted when
// sharing is turned off.
//
// Upload is not live in this release. flush() needs an endpoint, which the
// laptop reports from SIGNFLOW_CONTRIBUTE_URL (empty by default). With no
// endpoint, or with sharing off, it returns without touching the network.
// No code in the app calls flush() yet.
//
// The module takes its storage and fetch from the caller so it can be
// tested outside a browser (tests/contribute.test.mjs).

export const KEY_ON = 'wfshare';
export const KEY_ID = 'wfshareid';
export const KEY_Q = 'wfshareq';
export const MAX_SAMPLES = 16;    // queue cap; oldest is dropped first
export const MAX_FRAMES = 64;     // frames kept per take (evenly spaced)
export const POINTS = 42;         // 21 per hand, left then right
export const SCHEMA = 1;

const GLOSS_RE = /^[A-Z0-9_.\-]{1,48}$/;

function round(v) { const n = +v; return Number.isFinite(n) ? Math.round(n * 1000) / 1000 : 0; }

function randomId(crypto) {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return Array.from(b, x => x.toString(16).padStart(2, '0')).join('');
}

// One take -> landmarks only. Input frames are {t_ms, points:[{x,y,z,score}]}.
// Output: [{t, p:[[x,y,z] x42]}]; an absent point becomes [0,0,0].
export function toSample(frames, minScore = 0.28) {
  if (!Array.isArray(frames) || !frames.length) return [];
  const step = Math.max(1, frames.length / MAX_FRAMES);
  const out = [];
  const t0 = +frames[0].t_ms || 0;
  for (let i = 0; i < frames.length && out.length < MAX_FRAMES; i += step) {
    const f = frames[Math.floor(i)];
    const pts = Array.isArray(f && f.points) ? f.points : [];
    const p = new Array(POINTS);
    for (let k = 0; k < POINTS; k++) {
      const q = pts[k];
      p[k] = q && q.score > minScore ? [round(q.x), round(q.y), round(q.z)] : [0, 0, 0];
    }
    out.push({ t: Math.max(0, Math.round((+f.t_ms || 0) - t0)), p });
  }
  return out;
}

export function createContribute({ store, fetchImpl = null, crypto = globalThis.crypto, endpoint = () => '' } = {}) {
  const read = () => { try { return JSON.parse(store.getItem(KEY_Q) || '[]'); } catch (e) { return []; } };
  const write = q => {
    // localStorage is small; on quota errors drop the oldest and retry
    while (true) {
      try { store.setItem(KEY_Q, JSON.stringify(q)); return true; }
      catch (e) { if (!q.length) return false; q.shift(); }
    }
  };

  const api = {
    enabled: () => store.getItem(KEY_ON) === '1',

    setEnabled(on) {
      if (on) {
        store.setItem(KEY_ON, '1');
        if (!store.getItem(KEY_ID)) store.setItem(KEY_ID, randomId(crypto));
      } else {
        store.setItem(KEY_ON, '0');
        api.clear();
      }
    },

    // queue one take; returns true if stored
    add(gloss, frames) {
      if (!api.enabled()) return false;
      const g = String(gloss || '').toUpperCase();
      if (!GLOSS_RE.test(g)) return false;
      const lm = toSample(frames);
      if (lm.length < 2) return false;
      const q = read();
      q.push({ v: SCHEMA, gloss: g, frames: lm });
      while (q.length > MAX_SAMPLES) q.shift();
      return write(q);
    },

    queue: read,
    size: () => read().length,

    // deletes the queue and the random id
    clear() { store.removeItem(KEY_Q); store.removeItem(KEY_ID); },

    // Not called anywhere in this release. Returns {sent, skipped}.
    async flush() {
      const url = String(endpoint() || '').trim();
      if (!api.enabled() || !url || !/^https:\/\//.test(url) || typeof fetchImpl !== 'function')
        return { sent: 0, skipped: 'disabled' };
      const q = read();
      let sent = 0;
      while (q.length) {
        const s = q[0];
        const r = await fetchImpl(url.replace(/\/+$/, '') + '/v1/samples', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ v: s.v, install: store.getItem(KEY_ID), gloss: s.gloss, frames: s.frames }),
        }).catch(() => null);
        if (!r || !r.ok) break;
        q.shift(); sent++; write(q);
      }
      return { sent, skipped: null };
    },
  };
  return api;
}

// Browser instance. The endpoint comes from the laptop's /status
// (contribute_url), which is empty unless SIGNFLOW_CONTRIBUTE_URL is set.
let _endpoint = '';
export function setEndpoint(url) { _endpoint = typeof url === 'string' ? url : ''; }

const _store = (() => {
  try { return globalThis.localStorage || null; } catch (e) { return null; }
})();

export const Contribute = _store
  ? createContribute({ store: _store, fetchImpl: globalThis.fetch && globalThis.fetch.bind(globalThis), endpoint: () => _endpoint })
  : null;
