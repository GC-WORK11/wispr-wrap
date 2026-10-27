import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pack } from '../web/js/kp-pack.js';

const hand = (x0) => Array.from({ length: 21 }, (_, k) => ({ x: x0 + k * 0.01, y: 0.5, z: 0, visibility: 0 }));

test('detected hand points score 1 even though tasks-vision reports visibility 0', () => {
  const pts = pack([hand(0.2)], [[{ categoryName: 'Right', score: 0.98 }]]);
  assert.equal(pts.length, 42);
  for (let k = 21; k < 42; k++) assert.equal(pts[k].score, 1);
  for (let k = 0; k < 21; k++) assert.deepEqual(pts[k], { x: 0, y: 0, z: 0, score: 0 });
});

test('presence is used when the landmark carries one', () => {
  const lm = hand(0.3).map(p => ({ ...p, presence: 0.8 }));
  const pts = pack([lm], [[{ categoryName: 'Left' }]]);
  assert.equal(pts[0].score, 0.8);
  assert.equal(pts[21].score, 0);
});

test('no hands gives 42 zero points', () => {
  const pts = pack([], []);
  assert.ok(pts.every(p => p.score === 0 && p.x === 0));
});
