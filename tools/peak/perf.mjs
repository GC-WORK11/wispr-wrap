// perf.mjs — first-load and idle cost of the home screen, measured in a real
// headless Chrome against the real daemon (fresh profile, no service worker
// cache). Numbers come from CDP, not estimates.
// usage: node --experimental-websocket tools/peak/perf.mjs [cdp-port]
import fs from 'node:fs';
import { launchChrome, sleep } from './cdp.mjs';
import { loadStack } from './env.mjs';

const cdpPort = Number(process.argv[2] || 8951);
const S = loadStack();
const profile = '/tmp/peak-chrome-perf';
fs.rmSync(profile, { recursive: true, force: true });
const page = await launchChrome({ port: cdpPort, profile, origin: S.base });
await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*'] });
const out = {};
try {
  await page.send('Performance.enable');
  await page.send('Network.setCacheDisabled', { cacheDisabled: true });
  await page.viewport(390, 844, { mobile: true, dpr: 2 });
  const bytes = { total: 0, byType: {} };
  const reqs = new Map();
  page.on('Network.responseReceived', p => reqs.set(p.requestId, p.type));
  page.on('Network.loadingFinished', p => {
    const t = reqs.get(p.requestId) || 'Other';
    bytes.total += p.encodedDataLength; bytes.byType[t] = (bytes.byType[t] || 0) + p.encodedDataLength;
  });
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 2500 });
  out.firstLoad = {
    transferred_kB: +(bytes.total / 1024).toFixed(1),
    by_type_kB: Object.fromEntries(Object.entries(bytes.byType).map(([k, v]) => [k, +(v / 1024).toFixed(1)])),
  };
  out.timing = await page.eval(`(() => {
    const n = performance.getEntriesByType('navigation')[0] || {};
    const fcp = (performance.getEntriesByName('first-contentful-paint')[0] || {}).startTime;
    const lcp = (performance.getEntriesByType('largest-contentful-paint').pop() || {}).startTime;
    return { dom_content_loaded_ms: Math.round(n.domContentLoadedEventEnd), load_ms: Math.round(n.loadEventEnd), first_contentful_paint_ms: Math.round(fcp || 0), largest_contentful_paint_ms: Math.round(lcp || 0) };
  })()`);
  // layout shift
  out.cls = await page.eval(`new Promise(res => { let v = 0; new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) v += e.value; }).observe({ type: 'layout-shift', buffered: true }); setTimeout(() => res(+v.toFixed(4)), 300); })`);
  // idle cost once the lotus is open
  const m0 = Object.fromEntries((await page.send('Performance.getMetrics')).metrics.map(m => [m.name, m.value]));
  await sleep(6000);
  const m1 = Object.fromEntries((await page.send('Performance.getMetrics')).metrics.map(m => [m.name, m.value]));
  const d = k => +((m1[k] - m0[k]) / 6 * 1000).toFixed(1);
  out.idle_per_second_ms = { main_thread_task: d('TaskDuration'), script: d('ScriptDuration'), layout: d('LayoutDuration'), style_recalc: d('RecalcStyleDuration') };
  out.js_heap_MB = +(m1.JSHeapUsedSize / 1048576).toFixed(1);
  out.dom_nodes = m1.Nodes;
  // while typing (charged lotus, 25 fps)
  await page.focus('#box');
  const t0 = Object.fromEntries((await page.send('Performance.getMetrics')).metrics.map(m => [m.name, m.value]));
  for (let i = 0; i < 20; i++) { await page.type('ab'); await sleep(250); }
  const t1 = Object.fromEntries((await page.send('Performance.getMetrics')).metrics.map(m => [m.name, m.value]));
  out.typing_per_second_ms = { main_thread_task: +((t1.TaskDuration - t0.TaskDuration) / 5 * 1000).toFixed(1), script: +((t1.ScriptDuration - t0.ScriptDuration) / 5 * 1000).toFixed(1) };
  out.page_errors = page.errors.length;
  console.log(JSON.stringify(out, null, 1));
} finally { await page.close(); }
fs.mkdirSync('proofs/ui-v2', { recursive: true });
fs.writeFileSync('proofs/ui-v2/perf.json', JSON.stringify({ when: new Date().toISOString(), viewport: '390x844@2x (headless Chrome, software raster, fresh profile)', ...out }, null, 1));
