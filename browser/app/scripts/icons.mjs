import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';
const svg = await readFile(new URL('../public/icon.svg', import.meta.url), 'utf8');
const browser = await chromium.launch();
try {
  for (const [name, size] of [['icon-192', 192], ['icon-512', 512], ['apple-touch-icon', 180]]) {
    const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
    await page.setContent(`<style>html,body{margin:0;width:100%;height:100%}svg{width:100%;height:100%;display:block}</style>${svg}`);
    await page.screenshot({ path: new URL(`../public/${name}.png`, import.meta.url).pathname }); await page.close();
  }
} finally { await browser.close(); }
