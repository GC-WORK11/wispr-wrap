import { launchChrome, sleep } from '../../peak/cdp.mjs';
import fs from 'node:fs';
const svg = fs.readFileSync('/workspace/wispr-wrap-site/tools/site/logo/mark-inline.txt','utf8').trim();
const size = 96;
const html = `<!doctype html><html><head><style>*{margin:0;padding:0}html,body{width:${size}px;height:${size}px;background:#FAFAF9;color:#C2185B;display:flex;align-items:center;justify-content:center}svg{width:72px;height:72px}</style></head><body>${svg}</body></html>`;
const page = await launchChrome({ port: 9018, profile: '/tmp/logo-chrome-9018b', origin: 'about:blank' });
try {
  await page.ready;
  await page.send('Emulation.setDeviceMetricsOverride',{width:size,height:size,deviceScaleFactor:1,mobile:false});
  await page.send('Page.navigate',{url:'about:blank'}); await sleep(120);
  await page.send('Runtime.evaluate',{expression:`document.open();document.write(${JSON.stringify(html)});document.close();`});
  await sleep(250);
  const {data}=await page.send('Page.captureScreenshot',{format:'png',clip:{x:0,y:0,width:size,height:size,scale:1},fromSurface:true});
  fs.writeFileSync('/workspace/wispr-wrap-site/proofs/ui-v2/site-logo/inline-24.png',Buffer.from(data,'base64'));
  console.log('INLINE OK');
} finally { await page.close(); }
