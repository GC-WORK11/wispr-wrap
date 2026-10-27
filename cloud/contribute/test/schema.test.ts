import test from "node:test";
import assert from "node:assert/strict";
import { validate, POINTS, MAX_FRAMES } from "../src/schema.js";

const pt = (): [number, number, number] => [0.5, 0.4, -0.02];
const frame = (t: number) => ({ t, p: Array.from({ length: POINTS }, pt) });
const good = () => ({ v: 1, install: "0123456789abcdef0123456789abcdef", gloss: "HELLO", frames: [frame(0), frame(33)] });

test("accepts a landmarks-only sample", () => assert.equal(validate(good()).ok, true));
test("rejects extra top-level fields", () => assert.equal(validate({ ...good(), video: "x" }).ok, false));
test("rejects extra frame fields", () => {
  const b = good(); (b.frames[0] as Record<string, unknown>).img = "x";
  assert.equal(validate(b).ok, false);
});
test("rejects wrong point count", () => {
  const b = good(); b.frames[0].p.pop();
  assert.equal(validate(b).ok, false);
});
test("rejects too many frames", () => {
  const b = good(); b.frames = Array.from({ length: MAX_FRAMES + 1 }, (_, i) => frame(i));
  assert.equal(validate(b).ok, false);
});
test("rejects non-numeric coordinates", () => {
  const b = good(); (b.frames[1].p[0] as unknown[])[0] = "1";
  assert.equal(validate(b).ok, false);
});
test("rejects a bad install id", () => assert.equal(validate({ ...good(), install: "user@example.com" }).ok, false));
