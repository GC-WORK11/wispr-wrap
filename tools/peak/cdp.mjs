// cdp.mjs — a tiny, dependency-free Chrome DevTools Protocol driver for the
// real-browser proofs in proofs/peak/. Node >= 20.10 with --experimental-websocket
// (or Node >= 22). Real input events (Input.dispatch*), real screenshots.
//
// Safety: it only ever launches ITS OWN Chrome on a port the caller owns,
// with its own profile dir, and refuses to attach to a page whose URL does
// not start with the origin it was told to drive.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export { sleep };

async function getJson(url, tries = 80) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url);
      if (r.ok) return await r.json();
    } catch (e) { /* not up yet */ }
    await sleep(100);
  }
  throw new Error('timeout waiting for ' + url);
}

export async function launchChrome({ port, profile, origin, extra = [] }) {
  // never reuse a port somebody else already owns
  try {
    await fetch(`http://127.0.0.1:${port}/json/version`);
    throw new Error(`CDP port ${port} is already in use — pick a free one`);
  } catch (e) {
    if (String(e.message).includes('already in use')) throw e;
  }
  fs.mkdirSync(profile, { recursive: true });
  const args = [
    '--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--hide-scrollbars',
    '--no-first-run', '--no-default-browser-check', '--disable-extensions',
    '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
    '--autoplay-policy=no-user-gesture-required',
    '--force-color-profile=srgb', '--font-render-hinting=none',
    ...extra, 'about:blank',
  ];
  const proc = spawn('google-chrome', args, { stdio: 'ignore', detached: false });
  await getJson(`http://127.0.0.1:${port}/json/version`);
  const targets = await getJson(`http://127.0.0.1:${port}/json/list`);
  const t = targets.find((x) => x.type === 'page');
  const page = new Page(t.webSocketDebuggerUrl, origin);
  await page.ready;
  page.proc = proc;
  page.profile = profile;
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
    this.consoleLog = [];
    this.errors = [];
    this.ws = new WebSocket(wsUrl);
    this.ready = new Promise((res, rej) => {
      this.ws.onopen = async () => {
        await this.send('Page.enable');
        await this.send('Runtime.enable');
        await this.send('Network.enable');
        await this.send('Log.enable');
        res();
      };
      this.ws.onerror = (e) => rej(new Error('ws error'));
    });
    this.ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) {
        const { res, rej } = this.pending.get(m.id);
        this.pending.delete(m.id);
        if (m.error) rej(new Error(m.error.message + ' (' + JSON.stringify(m.error.data || '') + ')'));
        else res(m.result);
      } else if (m.method) {
        if (m.method === 'Runtime.consoleAPICalled') {
          const txt = m.params.args.map((a) => a.value ?? a.description ?? '').join(' ');
          this.consoleLog.push(`[${m.params.type}] ${txt}`);
        }
        if (m.method === 'Runtime.exceptionThrown') {
          const d = m.params.exceptionDetails;
          this.errors.push((d.exception && d.exception.description) || d.text);
        }
        const hs = this.handlers.get(m.method);
        if (hs) hs.forEach((h) => h(m.params));
      }
    };
  }

  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.has(id)) { this.pending.delete(id); rej(new Error('cdp timeout ' + method)); }
      }, 30000);
    });
  }

  on(method, fn) {
    if (!this.handlers.has(method)) this.handlers.set(method, []);
    this.handlers.get(method).push(fn);
  }

  async eval(expr, { awaitPromise = true } = {}) {
    const r = await this.send('Runtime.evaluate', {
      expression: expr, returnByValue: true, awaitPromise, userGesture: true,
    });
    if (r.exceptionDetails) {
      throw new Error('eval: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    }
    return r.result.value;
  }

  async viewport(w, h, { dpr = 1, mobile = false } = {}) {
    await this.send('Emulation.setDeviceMetricsOverride', {
      width: w, height: h, deviceScaleFactor: dpr, mobile,
    });
    await this.send('Emulation.setTouchEmulationEnabled', { enabled: mobile });
    this.mobile = mobile;
  }

  async goto(url, { settle = 600 } = {}) {
    if (this.origin && !url.startsWith(this.origin) && url !== 'about:blank') {
      throw new Error(`refusing to drive ${url} (not ${this.origin})`);
    }
    const loaded = new Promise((res) => {
      const h = () => res();
      this.on('Page.loadEventFired', h);
      setTimeout(res, 15000);
    });
    await this.send('Page.navigate', { url });
    await loaded;
    await sleep(settle);
  }

  async waitFor(expr, timeout = 8000, what = expr) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      try { if (await this.eval(expr)) return true; } catch (e) { /* page busy */ }
      await sleep(80);
    }
    throw new Error('waitFor timed out: ' + what);
  }

  async rect(sel) {
    const r = await this.eval(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null;
      e.scrollIntoView({block:'nearest',inline:'nearest'}); const b = e.getBoundingClientRect();
      return {x:b.x,y:b.y,w:b.width,h:b.height}; })()`);
    if (!r) throw new Error('no element ' + sel);
    return r;
  }

  // real pointer events at the element's centre (mouse on desktop, touch on mobile)
  async click(sel, { dx = 0, dy = 0 } = {}) {
    const r = await this.rect(sel);
    const x = Math.round(r.x + r.w / 2 + dx), y = Math.round(r.y + r.h / 2 + dy);
    if (this.mobile) {
      await this.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
      await sleep(40);
      await this.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    } else {
      await this.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
      await this.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
      await sleep(40);
      await this.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
    }
    await sleep(120);
  }

  async hover(sel) {
    const r = await this.rect(sel);
    await this.send('Input.dispatchMouseEvent', {
      type: 'mouseMoved', x: Math.round(r.x + r.w / 2), y: Math.round(r.y + r.h / 2),
    });
    await sleep(160);
  }

  async focus(sel) {
    await this.eval(`document.querySelector(${JSON.stringify(sel)}).focus()`);
  }

  // type like a person: per-char key events so keydown/input handlers all fire
  async type(text, { delay = 18 } = {}) {
    for (const ch of text) {
      await this.send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, key: ch, unmodifiedText: ch });
      await this.send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
      if (delay) await sleep(delay);
    }
  }

  async key(key, { code = key, vk = 0, modifiers = 0 } = {}) {
    await this.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key, code, windowsVirtualKeyCode: vk, modifiers });
    await this.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode: vk, modifiers });
    await sleep(60);
  }
  enter() { return this.key('Enter', { code: 'Enter', vk: 13 }); }
  tab() { return this.key('Tab', { code: 'Tab', vk: 9 }); }
  esc() { return this.key('Escape', { code: 'Escape', vk: 27 }); }

  async setFiles(sel, files) {
    const { root } = await this.send('DOM.getDocument');
    const { nodeId } = await this.send('DOM.querySelector', { nodeId: root.nodeId, selector: sel });
    if (!nodeId) throw new Error('no element ' + sel);
    await this.send('DOM.setFileInputFiles', { nodeId, files });
  }

  async shot(file, { full = false, clip = null } = {}) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const p = { format: 'png', fromSurface: true };
    if (clip) p.clip = { ...clip, scale: 1 };
    if (full) p.captureBeyondViewport = true;
    const { data } = await this.send('Page.captureScreenshot', p);
    fs.writeFileSync(file, Buffer.from(data, 'base64'));
    return file;
  }
}
