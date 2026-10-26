// cdp.mjs — a small, dependency-free Chrome DevTools Protocol driver for the
// Go Live proofs (Node >= 20 with --experimental-websocket, or Node >= 22).
// Real input events (Input.dispatch*), real screenshots, real network.
//
// Safety: it only launches ITS OWN Chrome on a port the caller owns (refuses a
// port that already answers), with its own profile dir, and refuses to drive a
// page whose URL is not on the origin it was given.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

export const sleep = ms => new Promise(r => setTimeout(r, ms));

async function getJson(url, tries = 100) {
  for (let i = 0; i < tries; i++) {
    try { const r = await fetch(url); if (r.ok) return await r.json(); } catch (e) { /* not up yet */ }
    await sleep(100);
  }
  throw new Error('timeout waiting for ' + url);
}

export async function launchChrome({ port, profile, origin, extra = [], env = {} }) {
  let busy = false;
  try { await fetch(`http://127.0.0.1:${port}/json/version`); busy = true; } catch (e) { /* free */ }
  if (busy) throw new Error(`CDP port ${port} is already in use; refusing to attach to someone else's browser`);
  fs.rmSync(profile, { recursive: true, force: true });
  fs.mkdirSync(profile, { recursive: true });
  const args = [
    '--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    '--no-sandbox', '--disable-dev-shm-usage', '--hide-scrollbars', '--mute-audio',
    '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-sync',
    '--disable-background-networking', '--disable-component-update',
    '--force-color-profile=srgb', '--font-render-hinting=none',
    '--autoplay-policy=no-user-gesture-required',
    ...extra, 'about:blank',
  ];
  const proc = spawn('google-chrome', args, { stdio: 'ignore', env: { ...process.env, ...env } });
  await getJson(`http://127.0.0.1:${port}/json/version`);
  // under load Chrome can answer /json/version before its first tab exists
  let t = null;
  for (let i = 0; i < 100 && !t; i++) {
    const targets = await getJson(`http://127.0.0.1:${port}/json/list`).catch(() => []);
    t = (targets || []).find(x => x.type === 'page' && x.webSocketDebuggerUrl);
    if (!t) await sleep(200);
  }
  if (!t) throw new Error('Chrome started but opened no page');
  const page = new Page(t.webSocketDebuggerUrl, origin);
  await page.ready;
  page.proc = proc;
  page.close = async () => {
    try { await page.send('Browser.close'); } catch (e) { /* gone */ }
    await sleep(300);
    try { proc.kill('SIGKILL'); } catch (e) { /* gone */ }
  };
  return page;
}

export class Page {
  constructor(wsUrl, origin) {
    this.origin = origin;
    this.id = 0;
    this.pending = new Map();
    this.handlers = new Map();
    this.errors = [];
    this.logs = [];
    this.ws = new WebSocket(wsUrl);
    this.ready = new Promise((res, rej) => {
      this.ws.onopen = async () => {
        await this.send('Page.enable');
        await this.send('Runtime.enable');
        await this.send('Network.enable');
        await this.send('Log.enable');
        res();
      };
      this.ws.onerror = () => rej(new Error('ws error'));
    });
    this.ws.onmessage = ev => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) {
        const { res, rej } = this.pending.get(m.id);
        this.pending.delete(m.id);
        if (m.error) rej(new Error(m.error.message + ' (' + (m.error.data || '') + ')')); else res(m.result);
        return;
      }
      if (m.method === 'Runtime.exceptionThrown') {
        const d = m.params.exceptionDetails || {};
        this.errors.push((d.exception && d.exception.description) || d.text || 'exception');
      }
      if (m.method === 'Runtime.consoleAPICalled') {
        const txt = (m.params.args || []).map(a => a.value !== undefined ? String(a.value) : (a.description || '')).join(' ');
        this.logs.push(m.params.type + ': ' + txt);
        if (m.params.type === 'error') this.errors.push('console.error: ' + txt);
      }
      for (const fn of this.handlers.get(m.method) || []) { try { fn(m.params); } catch (e) { /* handler bug */ } }
    };
  }
  on(method, fn) { if (!this.handlers.has(method)) this.handlers.set(method, []); this.handlers.get(method).push(fn); }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async url() { return this.eval('location.href'); }
  async goto(url, { settle = 1500 } = {}) {
    if (!url.startsWith(this.origin)) throw new Error('refusing to drive a foreign origin: ' + url);
    const loaded = new Promise(r => this.on('Page.loadEventFired', r));
    await this.send('Page.navigate', { url });
    await Promise.race([loaded, sleep(15000)]);
    await sleep(settle);
  }
  async eval(expr) {
    const r = await this.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true, userGesture: true });
    if (r.exceptionDetails) throw new Error('eval failed: ' + ((r.exceptionDetails.exception && r.exceptionDetails.exception.description) || r.exceptionDetails.text));
    return r.result.value;
  }
  async waitFor(expr, timeout = 10000, what = expr) {
    const t0 = Date.now();
    for (;;) {
      let v = false;
      try { v = await this.eval(expr); } catch (e) { /* page mid-navigation */ }
      if (v) return v;
      if (Date.now() - t0 > timeout) throw new Error('timeout: ' + what);
      await sleep(80);
    }
  }
  async viewport(w, h, { mobile = true, dpr = 2 } = {}) {
    await this.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: dpr, mobile });
    await this.send('Emulation.setTouchEmulationEnabled', { enabled: mobile, maxTouchPoints: mobile ? 5 : 1 });
  }
  async center(sel) {
    const r = await this.eval(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null;
      const b = e.getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2, w: b.width, h: b.height }; })()`);
    if (!r || !r.w) throw new Error('not visible: ' + sel);
    return r;
  }
  async tap(sel) {
    const { x, y } = await this.center(sel);
    await this.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    await sleep(40);
    await this.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await sleep(60);
  }
  async click(sel) {
    const { x, y } = await this.center(sel);
    await this.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
    await this.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
    await this.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
    await sleep(60);
  }
  async key(key, code = key, keyCode = 0) {
    await this.send('Input.dispatchKeyEvent', { type: 'keyDown', key, code, windowsVirtualKeyCode: keyCode });
    await this.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode: keyCode });
  }
  async type(text) { for (const ch of text) await this.send('Input.insertText', { text: ch }); }
  async shot(file) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const r = await this.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    fs.writeFileSync(file, Buffer.from(r.data, 'base64'));
    return file;
  }
}
