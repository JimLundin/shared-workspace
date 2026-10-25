import assert from 'node:assert/strict';
import { cp, mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { chromium } from 'playwright';
import { startStaticServer } from './static-server.mjs';
import { generateWorker } from '../scripts/service-worker.mjs';

async function openOptions(page) { await page.getByRole('button',{name:/Current workspace:/}).click(); await page.getByRole('button',{name:/App options/}).click(); }

const root = new URL('../', import.meta.url).pathname;
const temporary = await mkdtemp(join(tmpdir(), 'workspace-update-'));
const dist = join(temporary, 'dist');
await cp(join(root, 'dist'), dist, { recursive: true });
await writeFile(join(dist, 'assets/previous-build.js'), '/* previous build */');
await generateWorker(dist);
const server = await startStaticServer(dist);
const browser = await chromium.launchPersistentContext(join(temporary, 'profile'), { headless: true });
const report = { passed: false, assertions: [], scope: 'Real waiting service worker update on headers-free static server; real BrowserHost editing.', limits: ['Chromium only.', 'No public update or physical iPhone verification.'] };
try {
  const context = browser;
  // Withhold one real close acknowledgement, after the host has closed its store.
  // This tests UI recovery at its worker boundary without a fake host/store.
  await context.addInitScript(() => {
    const RealWorker = window.Worker;
    window.Worker = class extends RealWorker {
      closeId;
      constructor(...args) {
        super(...args);
        this.addEventListener('message', event => {
          if (this.closeId !== event.data?.id) return;
          this.closeId = undefined; event.stopImmediatePropagation();
          queueMicrotask(() => this.dispatchEvent(new MessageEvent('message', { data: { id: event.data.id, ok: false, error: 'Close acknowledgement unavailable for recovery test.' } })));
        });
      }
      postMessage(message, ...args) {
        if (message?.method === 'set_connection' && window.failNextConnectionSave) { this.closeId=message.id;window.failNextConnectionSave=false; }
        if (message?.method === 'close' && window.failNextWorkspaceClose) { this.closeId = message.id; window.failNextWorkspaceClose = false; }
        return super.postMessage(message, ...args);
      }
    };
  });
  const page = await context.newPage();
  await page.goto(server.url);
  await page.getByLabel('Workspace name', { exact: true }).waitFor({ timeout: 90000 });
  await page.getByLabel('Workspace name', { exact: true }).fill('Update check');
  await page.getByRole('button', { name: 'Create workspace', exact: true }).click();
  await page.getByRole('button', { name: 'Add to workspace', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: /^Note / }).click();
  await page.getByLabel('Title', { exact: true }).fill('Unsubmitted idea');
  await page.getByLabel('Content', { exact: true }).fill('Keep this while installing an update.');
  await rm(join(dist, 'assets/previous-build.js'));
  await writeFile(join(dist, 'assets/current-build.js'), '/* current build */');
  await generateWorker(dist);
  await page.evaluate(async () => { await (await navigator.serviceWorker.getRegistration()).update(); });
  await page.locator('[data-update]').waitFor({ state:'attached', timeout: 60000 });
  assert.equal(await page.getByLabel('Content', { exact: true }).inputValue(), 'Keep this while installing an update.');
  report.assertions.push('New build waits and offers update without reloading or replacing unsaved input.');
  await openOptions(page);
  await page.getByRole('button', { name: 'Update available', exact: true }).click();
  await page.getByRole('button', { name: 'Reload with update', exact: true }).click();
  assert.ok(await page.getByRole('dialog',{name:'Update application'}).textContent().then(text=>text.includes('unfinished edit')));
  assert.equal(await page.locator('dialog:modal').count(),1);
  await page.getByRole('button', { name: 'Later', exact: true }).click();
  assert.equal(await page.getByLabel('Content', { exact: true }).inputValue(), 'Keep this while installing an update.');
  report.assertions.push('Deferring the update retains the open editor and text.');
  await page.getByRole('button', { name: 'Save note', exact: true }).click();
  await page.locator('.note-body').waitFor();
  // Browser options outlive the app's five-second observation refresh.
  await openOptions(page);
  await page.getByRole('button',{name:'Close app options',exact:true}).focus();
  await page.waitForTimeout(5500);
  assert.equal(await page.getByRole('button',{name:'Close app options',exact:true}).evaluate(el=>el===document.activeElement),true);
  await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('button',{name:/Current workspace:/}).evaluate(el=>el===document.activeElement),true);
  // An unfinished workspace name is input too, even after dismissing its dialog.
  await page.getByRole('button',{name:/Current workspace:/}).click();
  await page.getByRole('button',{name:'Create workspace',exact:true}).click();
  await page.getByLabel('Workspace name',{exact:true}).fill('Unsubmitted workspace');
  await page.getByRole('button',{name:'Close dialog'}).click();
  await openOptions(page);
  await page.getByRole('button',{name:'Update available',exact:true}).click();
  await page.getByRole('button',{name:'Reload with update',exact:true}).click();
  assert.ok(await page.getByRole('dialog',{name:'Update application'}).textContent().then(text=>text.includes('unfinished edit')));
  await page.getByRole('button',{name:'Later',exact:true}).click();
  await page.getByRole('button',{name:/Current workspace:/}).click();
  await page.getByRole('button',{name:'Create workspace',exact:true}).click();
  assert.equal(await page.getByLabel('Workspace name',{exact:true}).inputValue(),'Unsubmitted workspace');
  await page.getByLabel('Workspace name',{exact:true}).fill('');
  await page.getByRole('button',{name:'Close dialog'}).click();
  report.assertions.push('Options survive polling and restore focus; update preserves unfinished workspace creation.');
  // Private key input and an unconfirmed real worker write must also block update.
  await page.getByRole('button',{name:/Current workspace:/}).click();await page.getByRole('button',{name:'OpenAI connection',exact:true}).click();
  const syntheticKey='SYNTHETIC_ONLY_UPDATE_RECOVERY_KEY';await page.getByLabel('OpenAI API key',{exact:true}).fill(syntheticKey);await page.getByRole('button',{name:'Close dialog'}).click();
  await openOptions(page);await page.getByRole('button',{name:'Update available',exact:true}).click();await page.getByRole('button',{name:'Reload with update',exact:true}).click();assert.ok((await page.getByRole('dialog',{name:'Update application'}).textContent()).includes('unfinished edit'));await page.getByRole('button',{name:'Later',exact:true}).click();
  await page.getByRole('button',{name:/Current workspace:/}).click();await page.getByRole('button',{name:'OpenAI connection',exact:true}).click();assert.equal(await page.getByLabel('OpenAI API key',{exact:true}).inputValue(),syntheticKey);
  await page.evaluate(()=>{window.failNextConnectionSave=true});await page.getByRole('button',{name:'Save connection',exact:true}).click();await page.getByRole('button',{name:'Retry connection change',exact:true}).waitFor();await page.getByRole('button',{name:'Close dialog'}).click();
  await openOptions(page);await page.getByRole('button',{name:'Update available',exact:true}).click();await page.getByRole('button',{name:'Reload with update',exact:true}).click();assert.ok((await page.getByRole('dialog',{name:'Update application'}).textContent()).includes('unfinished edit'));await page.getByRole('button',{name:'Later',exact:true}).click();
  await page.getByRole('button',{name:/Current workspace:/}).click();await page.getByRole('button',{name:'OpenAI connection',exact:true}).click();await page.getByRole('button',{name:'Retry connection change',exact:true}).click();await page.getByRole('button',{name:'Continue to voice'}).waitFor();await page.getByRole('button',{name:'Close dialog'}).click();
  report.assertions.push('Update preserves private key input and blocks an unconfirmed real connection write until its exact retry completes.');
  // A second old client remains open during activation, exercising old chunks.
  const oldClient = await context.newPage(); await oldClient.goto(server.url);
  await openOptions(page);
  await page.getByRole('button', { name: 'Update available', exact: true }).click();
  await Promise.all([
    page.waitForEvent('domcontentloaded', { timeout: 60000 }),
    page.getByRole('button', { name: 'Reload with update', exact: true }).click(),
  ]);
  await page.locator('.note-body').waitFor({ timeout: 60000 });
  assert.equal(await page.locator('.note-body').textContent(), 'Keep this while installing an update.');
  assert.equal(await page.evaluate(() => crossOriginIsolated), true);
  report.assertions.push('Explicit update reload reopens the committed note and isolated storage.');
  const previous = await oldClient.evaluate(async url => {
    const response = await fetch(url); return { status: response.status, body: await response.text() };
  }, server.url + 'assets/previous-build.js');
  assert.equal(previous.status, 200); assert.equal(previous.body, '/* previous build */');
  report.assertions.push('Previous build asset remains available to an older client after removal from server.');
  await writeFile(join(dist, 'assets/following-build.js'), '/* following build */');
  await generateWorker(dist);
  await page.evaluate(async () => { await (await navigator.serviceWorker.getRegistration()).update(); });
  await page.locator('[data-update]').waitFor({ state:'attached', timeout: 60000 });
  await page.evaluate(() => { window.failNextWorkspaceClose = true; });
  await openOptions(page);
  await page.getByRole('button', { name: 'Update available', exact: true }).click();
  await page.getByRole('button', { name: 'Reload with update', exact: true }).click();
  await page.getByRole('button', { name: 'Reload and recover', exact: true }).waitFor();
  assert.equal(await page.locator('#app').evaluate(element => element.inert), true);
  await page.goBack();
  assert.equal(await page.getByRole('button',{name:'Reload and recover',exact:true}).isVisible(),true);
  assert.equal(await page.locator('dialog:modal').count(),1);
  await Promise.all([
    page.waitForEvent('domcontentloaded', { timeout: 60000 }),
    page.getByRole('button', { name: 'Reload and recover', exact: true }).click(),
  ]);
  await page.locator('.note-body').waitFor({ timeout: 60000 });
  assert.equal(await page.locator('.note-body').textContent(), 'Keep this while installing an update.');
  report.assertions.push('A missing close acknowledgement offers explicit reload recovery and reopens committed work.');
  report.passed = true;
} catch (error) { report.failure = 'Update acceptance failed.'; await mkdir(join(root, 'artifacts'), { recursive: true }); await writeFile(join(root, 'artifacts/update-diagnostics.txt'), String(error.stack)); process.exitCode = 1; }
finally {
  await browser.close(); await server.close(); await rm(temporary, { recursive: true, force: true });
  await mkdir(join(root, 'evidence'), { recursive: true });
  await writeFile(join(root, 'evidence/update.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report));
}
