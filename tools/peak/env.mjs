// env.mjs — read the realrun state file (mode 600, /tmp only) for the stack's
// port + pairing key. The key is returned to the caller and NEVER printed or
// written to any proof artifact.
import fs from 'node:fs';

export function loadStack(file = process.env.PEAK_ENV || '/tmp/wf-realrun-wispr-wrap-peak/env') {
  const o = {};
  for (const l of fs.readFileSync(file, 'utf8').split('\n')) {
    const i = l.indexOf('=');
    if (i > 0) o[l.slice(0, i)] = l.slice(i + 1);
  }
  o.base = `http://127.0.0.1:${o.PORT}`;
  return o;
}

// redact anything key-shaped from a string before it goes to a log/proof
export function scrub(s, stack) {
  let out = String(s);
  for (const k of [stack.KEY, stack.PAIR_CODE]) if (k) out = out.split(k).join('<redacted>');
  return out;
}
