// selftest.mjs — run PRO's teach-wizard regression (tests/selftest.html against
// tests/stub_wfd.py) inside MY headless Chrome on MY ports, without the shared
// /tmp paths or pkill patterns of tests/run-teach-ui.sh (which would kill a
// sibling agent's run). Exit 0 = every step green.
// usage: node --experimental-websocket tools/peak/selftest.mjs [outdir] [stub-port] [cdp-port]
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { launchChrome, sleep } from './cdp.mjs';

const out = process.argv[2] || '/tmp/peak-shots/selftest';
const stubPort = Number(process.argv[3] || 8953);
const cdpPort = Number(process.argv[4] || 8954);
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../..');
const resultFile = '/tmp/peak-selftest-result.json';
fs.rmSync(resultFile, { force: true });

const stub = spawn('python3', ['tests/stub_wfd.py', '--port', String(stubPort)], {
  cwd: root, stdio: 'ignore', env: { ...process.env, WF_STUB_RESULT: resultFile },
});
let page;
try {
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(`http://127.0.0.1:${stubPort}/health`)).ok) break; } catch (e) { /* wait */ }
    await sleep(100);
  }
  const profile = '/tmp/peak-chrome-selftest';
  fs.rmSync(profile, { recursive: true, force: true });
  page = await launchChrome({ port: cdpPort, profile, origin: `http://127.0.0.1:${stubPort}` });
  await page.viewport(900, 900);
  await page.goto(`http://127.0.0.1:${stubPort}/selftest`, { settle: 500 });
  let res = null;
  for (let i = 0; i < 400; i++) {
    if (fs.existsSync(resultFile)) { res = JSON.parse(fs.readFileSync(resultFile, 'utf8')); break; }
    await sleep(150);
  }
  fs.mkdirSync(out, { recursive: true });
  await page.shot(path.join(out, 'selftest-final.png'));
  if (!res) { console.log('FAIL: no result posted'); process.exitCode = 1; }
  else {
    for (const s of res.steps || []) console.log(' ', s);
    console.log('RESULT:', res.pass ? 'PASS' : 'FAIL', (res.errs || []).slice(0, 5).join(' | '));
    process.exitCode = res.pass ? 0 : 1;
  }
} finally {
  if (page) await page.close();
  stub.kill();
}
