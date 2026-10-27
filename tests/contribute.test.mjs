// node --test tests/contribute.test.mjs
// Proves the training-samples queue is landmarks only and that it never
// touches the network unless sharing is on AND an endpoint is configured.
import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { createContribute, toSample, MAX_SAMPLES, MAX_FRAMES, POINTS, KEY_ID, KEY_Q } from '../web/js/contribute.js';

function memStore() {
  const m = new Map();
  return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k), _m: m };
}
function frames(n) {
  return Array.from({ length: n }, (_, i) => ({
    t_ms: 1000 + i * 33,
    points: Array.from({ length: 42 }, (_, k) => ({ x: 0.1 + k / 100, y: 0.2, z: -0.01, score: k < 21 ? 0.9 : 0 })),
  }));
}
function spyFetch() {
  const calls = [];
  const f = (...a) => { calls.push(a); return Promise.resolve({ ok: true }); };
  f.calls = calls;
  return f;
}

// also trap the global fetch so a stray call anywhere fails the test
const realFetch = globalThis.fetch;
let globalCalls = 0;
globalThis.fetch = () => { globalCalls++; return Promise.reject(new Error('network used')); };
test.after(() => { globalThis.fetch = realFetch; });

test('off by default: nothing queued, no network', async () => {
  const store = memStore(), f = spyFetch();
  const c = createContribute({ store, fetchImpl: f, crypto: webcrypto, endpoint: () => '' });
  assert.equal(c.enabled(), false);
  assert.equal(c.add('HELLO', frames(30)), false);
  assert.equal(c.size(), 0);
  assert.equal(store.getItem(KEY_ID), null);
  assert.deepEqual(await c.flush(), { sent: 0, skipped: 'disabled' });
  assert.equal(f.calls.length, 0);
});

test('on, no endpoint: queues landmarks only, no network', async () => {
  const store = memStore(), f = spyFetch();
  const c = createContribute({ store, fetchImpl: f, crypto: webcrypto, endpoint: () => '' });
  c.setEnabled(true);
  assert.match(store.getItem(KEY_ID), /^[0-9a-f]{32}$/);
  assert.equal(c.add('hello', frames(200)), true);
  const q = c.queue();
  assert.equal(q.length, 1);
  assert.deepEqual(Object.keys(q[0]).sort(), ['frames', 'gloss', 'v']);
  assert.equal(q[0].gloss, 'HELLO');
  assert.ok(q[0].frames.length <= MAX_FRAMES);
  for (const fr of q[0].frames) {
    assert.deepEqual(Object.keys(fr).sort(), ['p', 't']);
    assert.equal(fr.p.length, POINTS);
    for (const pt of fr.p) { assert.equal(pt.length, 3); pt.forEach(v => assert.equal(typeof v, 'number')); }
  }
  assert.deepEqual(q[0].frames[0].p[30], [0, 0, 0]);   // absent hand stays zero
  assert.deepEqual(await c.flush(), { sent: 0, skipped: 'disabled' });
  assert.equal(f.calls.length, 0);
});

test('queue is capped', () => {
  const store = memStore();
  const c = createContribute({ store, crypto: webcrypto });
  c.setEnabled(true);
  for (let i = 0; i < MAX_SAMPLES + 5; i++) c.add('S' + i, frames(10));
  assert.equal(c.size(), MAX_SAMPLES);
  assert.equal(c.queue()[0].gloss, 'S5');
});

test('turning off deletes the queue and the id', () => {
  const store = memStore();
  const c = createContribute({ store, crypto: webcrypto });
  c.setEnabled(true); c.add('A', frames(10));
  c.setEnabled(false);
  assert.equal(store.getItem(KEY_Q), null);
  assert.equal(store.getItem(KEY_ID), null);
});

test('non-https endpoint is refused', async () => {
  const store = memStore(), f = spyFetch();
  const c = createContribute({ store, fetchImpl: f, crypto: webcrypto, endpoint: () => 'http://example.invalid' });
  c.setEnabled(true); c.add('A', frames(10));
  assert.equal((await c.flush()).sent, 0);
  assert.equal(f.calls.length, 0);
});

test('toSample drops scores and keeps relative time', () => {
  const s = toSample(frames(3));
  assert.equal(s[0].t, 0);
  assert.equal(s[1].t, 33);
  assert.ok(!JSON.stringify(s).includes('score'));
});

test('no global fetch was used', () => { assert.equal(globalCalls, 0); });
