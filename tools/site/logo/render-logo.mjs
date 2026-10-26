// render-logo.mjs — render web/icon.svg geometry to all PNG sizes via headless
// Chrome (CDP port 9018), then build proof renders + contact sheet inputs.
// Run: node --experimental-websocket tools/site/logo/render-logo.mjs
import { launchChrome, sleep } from '../../peak/cdp.mjs';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = '/workspace/wispr-wrap-site';
const WEB = path.join(ROOT, 'web');
const PROOF = path.join(ROOT, 'proofs/ui-v2/site-logo');
const LOGO = path.join(ROOT, 'tools/site/logo');
fs.mkdirSync(PROOF, { recursive: true });
fs.mkdirSync(LOGO, { recursive: true });

const svg = fs.readFileSync(path.join(WEB, 'icon.svg'), 'utf8').trim();

// Build an HTML page that paints the mark at a given CSS pixel size, with a
// chosen tile style. We screenshot the exact box so pixels == requested size.
function pageHTML({ size, tile, pad, radius }) {
  // tile: 'none' (transparent), 'fafaf9' (opaque flat), 'rounded' (rounded tile)
  const inner = size * (1 - 2 * pad);
  let tileCss = 'background:transparent;';
  if (tile === 'fafaf9') tileCss = 'background:#FAFAF9;';
  if (tile === 'rounded') tileCss = `background:#FAFAF9;border-radius:${radius}px;`;
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    *{margin:0;padding:0;box-sizing:border-box}
    html,body{width:${size}px;height:${size}px;background:transparent}
    #tile{width:${size}px;height:${size}px;${tileCss}
      display:flex;align-items:center;justify-content:center;overflow:hidden}
    #mark{width:${inner}px;height:${inner}px;display:block}
    #mark svg{width:100%;height:100%;display:block}
  </style></head><body><div id="tile"><div id="mark">${svg}</div></div></body></html>`;
}

async function renderPNG(page, out, opts) {
  const html = pageHTML(opts);
  const dataUrl = 'data:text/html;base64,' + Buffer.from(html).toString('base64');
  await page.send('Emulation.setDeviceMetricsOverride', {
    width: opts.size, height: opts.size, deviceScaleFactor: 1, mobile: false,
  });
  // navigate via about:blank-friendly data url (origin check allows about:blank only,
  // so drive through document.write instead)
  await page.send('Page.navigate', { url: 'about:blank' });
  await sleep(120);
  await page.send('Runtime.evaluate', {
    expression: `document.open();document.write(${JSON.stringify(html)});document.close();`,
  });
  await sleep(250);
  // transparent backdrop for mask/favicon renders; opaque otherwise
  if (opts.tile === 'none') {
    await page.send('Emulation.setDefaultBackgroundColorOverride', {
      color: { r: 0, g: 0, b: 0, a: 0 },
    });
  } else {
    await page.send('Emulation.setDefaultBackgroundColorOverride', {
      color: { r: 250, g: 250, b: 249, a: 255 },
    });
  }
  const clip = { x: 0, y: 0, width: opts.size, height: opts.size, scale: 1 };
  const { data } = await page.send('Page.captureScreenshot', {
    format: 'png', clip, fromSurface: true,
  });
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, Buffer.from(data, 'base64'));
  return out;
}

(async () => {
  const page = await launchChrome({
    port: 9018,
    profile: '/tmp/logo-chrome-9018',
    origin: 'about:blank',
  });
  try {
    await page.ready;
    // ---- shipped PNGs (from SVG geometry) ----
    // icon-512 / icon-192: mark on #FAFAF9 rounded tile, ~16% padding
    await renderPNG(page, path.join(WEB, 'icon-512.png'),
      { size: 512, tile: 'rounded', pad: 0.16, radius: 114 });
    await renderPNG(page, path.join(WEB, 'icon-192.png'),
      { size: 192, tile: 'rounded', pad: 0.16, radius: 43 });
    // maskable: full-bleed #FAFAF9 tile, mark inside 80% safe zone -> pad 0.20
    await renderPNG(page, path.join(WEB, 'icon-maskable-512.png'),
      { size: 512, tile: 'fafaf9', pad: 0.20, radius: 0 });
    // apple-touch 180 opaque, modest padding
    await renderPNG(page, path.join(WEB, 'apple-touch-icon.png'),
      { size: 180, tile: 'fafaf9', pad: 0.14, radius: 0 });

    // ---- favicon source renders (transparent mark, for ICO build) ----
    for (const s of [16, 32, 48]) {
      await renderPNG(page, path.join(LOGO, `fav-${s}.png`),
        { size: s, tile: 'none', pad: 0.04, radius: 0 });
    }

    // ---- proof renders on #FAFAF9 at 16/64/512, DPR 1 & 2 ----
    for (const s of [16, 64, 512]) {
      for (const dpr of [1, 2]) {
        const px = s * dpr;
        await renderPNG(page, path.join(PROOF, `mark-${s}-dpr${dpr}.png`),
          { size: px, tile: 'fafaf9', pad: 0.10, radius: 0 });
      }
    }
    // transparent proof renders for bbox checks
    for (const s of [64, 512]) {
      await renderPNG(page, path.join(PROOF, `mark-${s}-alpha.png`),
        { size: s, tile: 'none', pad: 0, radius: 0 });
    }
    console.log('RENDER OK');
  } finally {
    await page.close();
  }
})().catch((e) => { console.error('RENDER FAIL', e); process.exit(1); });
