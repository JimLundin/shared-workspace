import assert from 'node:assert/strict';
import { cp, mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { chromium } from 'playwright';
import { startStaticServer } from './static-server.mjs';
import { generateWorker } from '../scripts/service-worker.mjs';
import { addMaterial, openSettings, openWorkspacePicker } from './ui-controls.mjs';

async function openOptions(page) { await openSettings(page); await page.getByRole('button',{name:'Install and storage',exact:true}).click(); }

const root = new URL('../', import.meta.url).pathname;
const temporary = await mkdtemp(join(tmpdir(), 'workspace-update-'));
const dist = join(temporary, 'dist');
await cp(join(root, 'dist'), dist, { recursive: true });
await writeFile(join(dist, 'assets/previous-build.js'), '/* previous build */');
await generateWorker(dist);
const server = await startStaticServer(dist);
const browser = await chromium.launchPersistentContext(join(temporary, 'profile'), { headless: true, permissions: ['microphone'], args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] });
const report = { passed: false, assertions: [], scope: 'Real service-worker discovery through online events and visible timers, explicit update and real BrowserHost editing on a headers-free server.', limits: ['Chromium only.', 'Install events and display-mode observations are controlled test inputs; no actual OS installation or Safari-to-Home-Screen storage migration is claimed.', 'No public update or physical iPhone verification.', 'BFCache eligibility is not asserted: PageTransitionEvents exercise the actual application lifecycle handlers.'] };
report.started_at = new Date().toISOString();
report.browser = { engine: 'chromium', version: browser.browser().version(), synthetic_microphone: true };
let blockedProviderRequests = 0;
try {
  const context = browser;
  await context.route('https://api.openai.com/**', route => { blockedProviderRequests++; return route.abort('blockedbyclient'); });
  // Withhold one real close acknowledgement, after the host has closed its store.
  // This tests UI recovery at its worker boundary without a fake host/store.
  await context.addInitScript(() => {
    window.installProofCalls = [];
    window.addEventListener('beforeinstallprompt', event => { if (!event.installProof) { event.preventDefault(); event.stopImmediatePropagation(); } }, { capture: true });
    window.offerInstallProof = (id, fails = false) => {
      const event = new Event('beforeinstallprompt', { cancelable: true }); event.installProof = true;
      event.prompt = async () => { window.installProofCalls.push(id); if (fails) throw new Error('Synthetic install unavailable'); };
      event.userChoice = Promise.resolve({ outcome: 'dismissed' }); window.dispatchEvent(event); return event.defaultPrevented;
    };
    const matchMedia = window.matchMedia.bind(window), installDisplay = new EventTarget(); let standalone = false;
    Object.defineProperty(installDisplay, 'matches', { get: () => standalone });
    window.matchMedia = query => query === '(display-mode: standalone)' ? installDisplay : matchMedia(query);
    window.setInstallDisplayProof = value => { standalone = value; installDisplay.dispatchEvent(new Event('change')); };
    window.updateCheckCalls = 0;
    const originalUpdate = ServiceWorkerRegistration.prototype.update;
    ServiceWorkerRegistration.prototype.update = function(...args) { window.updateCheckCalls++; return Reflect.apply(originalUpdate, this, args); };
    const originalHidden = Object.getOwnPropertyDescriptor(Document.prototype, 'hidden').get;
    Object.defineProperty(document, 'hidden', { get: () => window.updateTestHidden ?? originalHidden.call(document) });
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
  await page.clock.install();
  await page.goto(server.url);
  await page.getByLabel('Workspace name', { exact: true }).waitFor({ timeout: 90000 });
  assert.equal(await page.locator('#workspace-update').count(), 0);
  assert.ok(await page.evaluate(() => window.updateCheckCalls >= 1));
  report.assertions.push('Startup checks automatically; no top-level update button appears without a ready waiting worker.');
  await openOptions(page);
  const installPanel = page.getByRole('dialog', { name: 'Install and storage', exact: true });
  assert.equal(await installPanel.getByRole('button', { name: 'Install on this device', exact: true }).count(), 0);
  assert.ok((await installPanel.textContent()).includes('Share → Add to Home Screen'));
  assert.ok((await installPanel.textContent()).includes('Keep your Safari work'));
  assert.ok((await installPanel.textContent()).includes('pair it from another connected device'));
  assert.ok((await installPanel.textContent()).includes('does not add desktop control or continuous background operation'));
  assert.deepEqual(await page.evaluate(() => [window.offerInstallProof('first'), window.offerInstallProof('second')]), [true, true]);
  assert.equal(await installPanel.getByRole('button', { name: 'Install on this device', exact: true }).count(), 1);
  assert.deepEqual(await page.evaluate(() => window.installProofCalls), []);
  await installPanel.getByRole('button', { name: 'Install on this device', exact: true }).click();
  assert.deepEqual(await page.evaluate(() => window.installProofCalls), ['second']);
  assert.equal(await installPanel.getByRole('button', { name: 'Install on this device', exact: true }).count(), 0);
  assert.equal(await page.locator('#installation-state').textContent(), 'Running in a browser tab.');
  await page.evaluate(() => window.offerInstallProof('failed', true));
  await installPanel.getByRole('button', { name: 'Install on this device', exact: true }).click();
  assert.equal(await page.locator('#installation-state').textContent(), 'The browser did not complete installation. Try its install menu.');
  await page.evaluate(() => { window.offerInstallProof('already-installed'); window.setInstallDisplayProof(true); });
  assert.equal(await installPanel.getByRole('button', { name: 'Install on this device', exact: true }).count(), 0);
  assert.ok((await page.locator('#installation-state').textContent()).startsWith('Running in an app window.'));
  await page.evaluate(() => window.setInstallDisplayProof(false));
  await installPanel.getByRole('button', { name: 'Close install and storage', exact: true }).click();
  assert.equal(await page.locator('#workspace-settings').evaluate(element => element === document.activeElement), true);
  report.assertions.push('Settings opens install/storage guidance; conditional install events are deduplicated, user-triggered, and do not claim extra device powers or move Safari work.');
  report.assertions.push('Controlled standalone detection hides redundant install actions; dismissal and failure do not falsely claim an app window, and closing returns focus to Settings.');

  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.locator('#workspace-settings').isVisible(), false);
  await openOptions(page);
  await page.getByRole('button', { name: 'Close install and storage', exact: true }).click();
  assert.equal(await page.locator('#navigation-toggle').evaluate(element => element === document.activeElement), true);
  report.assertions.push('Closing install/storage in a compact window returns focus to its visible navigation control.');
  await page.setViewportSize({ width: 1280, height: 900 });

  await page.getByLabel('Workspace name', { exact: true }).fill('Update check');
  await page.getByRole('button', { name: 'Create workspace', exact: true }).click();
  await addMaterial(page);
  await page.getByRole('dialog').getByRole('button', { name: /^Note / }).click();
  await page.getByLabel('Title', { exact: true }).fill('Unsubmitted idea');
  await page.getByLabel('Content', { exact: true }).fill('Keep this while installing an update.');
  await rm(join(dist, 'assets/previous-build.js'));
  await writeFile(join(dist, 'assets/current-build.js'), '/* current build */');
  await generateWorker(dist);
  const checksBeforePause = await page.evaluate(() => window.updateCheckCalls);
  await page.evaluate(() => { window.updateTestHidden = true; document.dispatchEvent(new Event('visibilitychange')); });
  await page.clock.fastForward(6 * 60_000);
  assert.equal(await page.evaluate(() => window.updateCheckCalls), checksBeforePause);
  assert.equal(await page.locator('#workspace-update').count(), 0);
  await context.setOffline(true);
  await page.evaluate(() => { window.updateTestHidden = false; document.dispatchEvent(new Event('visibilitychange')); });
  await page.clock.fastForward(6 * 60_000);
  assert.equal(await page.evaluate(() => window.updateCheckCalls), checksBeforePause);
  await context.setOffline(false);
  // The real online event invokes the production scheduler, not the update API.

  await page.locator('#workspace-update').waitFor({ state:'visible', timeout: 60000 });
  assert.equal(await page.getByLabel('Content', { exact: true }).inputValue(), 'Keep this while installing an update.');
  assert.ok(await page.evaluate(() => window.updateCheckCalls > 0));
  report.assertions.push('Hidden/offline periods do not poll; a real online event discovers the ready update and preserves unsaved input.');
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
  await page.getByRole('button',{name:'Close install and storage',exact:true}).focus();
  await page.waitForTimeout(5500);
  assert.equal(await page.getByRole('button',{name:'Close install and storage',exact:true}).evaluate(el=>el===document.activeElement),true);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#workspace-settings').evaluate(el=>el===document.activeElement),true);
  // An unfinished workspace name is input too, even after dismissing its dialog.
  await openWorkspacePicker(page);
  await page.getByRole('button',{name:'Create workspace',exact:true}).click();
  await page.getByLabel('Workspace name',{exact:true}).fill('Unsubmitted workspace');
  await page.getByRole('button',{name:'Close dialog'}).click();
  await page.getByRole('button',{name:'Update available',exact:true}).click();
  await page.getByRole('button',{name:'Reload with update',exact:true}).click();
  assert.ok(await page.getByRole('dialog',{name:'Update application'}).textContent().then(text=>text.includes('unfinished edit')));
  await page.getByRole('button',{name:'Later',exact:true}).click();
  await openWorkspacePicker(page);
  await page.getByRole('button',{name:'Create workspace',exact:true}).click();
  assert.equal(await page.getByLabel('Workspace name',{exact:true}).inputValue(),'Unsubmitted workspace');
  await page.getByLabel('Workspace name',{exact:true}).fill('');
  await page.getByRole('button',{name:'Close dialog'}).click();
  report.assertions.push('Options survive polling and restore focus; update preserves unfinished workspace creation.');
  // Private key input and an unconfirmed real worker write must also block update.
  await openSettings(page);await page.getByRole('button',{name:'OpenAI',exact:true}).click();
  const syntheticKey='SYNTHETIC_ONLY_UPDATE_RECOVERY_KEY';await page.getByLabel('OpenAI API key',{exact:true}).fill(syntheticKey);await page.getByRole('button',{name:'Close dialog'}).click();
  await page.getByRole('button',{name:'Update available',exact:true}).click();await page.getByRole('button',{name:'Reload with update',exact:true}).click();assert.ok((await page.getByRole('dialog',{name:'Update application'}).textContent()).includes('unfinished edit'));await page.getByRole('button',{name:'Later',exact:true}).click();
  await openSettings(page);await page.getByRole('button',{name:'OpenAI',exact:true}).click();assert.equal(await page.getByLabel('OpenAI API key',{exact:true}).inputValue(),syntheticKey);
  await page.evaluate(()=>{window.failNextConnectionSave=true});await page.getByRole('button',{name:'Save connection',exact:true}).click();await page.getByRole('button',{name:'Retry connection change',exact:true}).waitFor();await page.getByRole('button',{name:'Close dialog'}).click();
  await page.getByRole('button',{name:'Update available',exact:true}).click();await page.getByRole('button',{name:'Reload with update',exact:true}).click();assert.ok((await page.getByRole('dialog',{name:'Update application'}).textContent()).includes('unfinished edit'));await page.getByRole('button',{name:'Later',exact:true}).click();
  await openSettings(page);await page.getByRole('button',{name:'OpenAI',exact:true}).click();await page.getByRole('button',{name:'Retry connection change',exact:true}).click();await page.getByRole('button',{name:'Continue to voice'}).waitFor();await page.getByRole('button',{name:'Close dialog'}).click();
  report.assertions.push('Update preserves private key input and blocks an unconfirmed real connection write until its exact retry completes.');
  await page.getByRole('button', { name: 'Speak', exact: true }).click();
  await page.locator('.voice-stage[data-phase="listening"]').waitFor();
  const whileRecording = await page.evaluate(() => window.updateCheckCalls);
  await page.clock.fastForward(60_001);
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await page.waitForFunction(before => window.updateCheckCalls > before, whileRecording);
  assert.equal(await page.locator('.voice-stage[data-phase="listening"]').isVisible(), true);
  assert.equal(await page.getByRole('dialog', { name: 'Update application' }).count(), 0);
  await page.getByRole('button', { name: 'Cancel recording', exact: true }).click();
  await page.locator('.voice-stage[data-phase="idle"]').waitFor();
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  report.assertions.push('Automatic checking leaves a real MediaRecorder session listening until the user explicitly cancels capture.');
  // A second old client remains open during activation, exercising old chunks.
  const oldClient = await context.newPage(); await oldClient.goto(server.url); await page.bringToFront();
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
  await page.clock.fastForward(5 * 60_000 + 1);
  await page.locator('#workspace-update').waitFor({ state:'visible', timeout: 60000 });
  report.assertions.push('A visible five-minute timer discovers a later real service-worker build without manual refresh.');
  const beforeCache = await page.evaluate(() => window.updateCheckCalls);
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })));
  await page.clock.fastForward(6 * 60_000);
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  assert.equal(await page.evaluate(() => window.updateCheckCalls), beforeCache);
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
  await page.waitForFunction(before => window.updateCheckCalls > before, beforeCache);
  report.assertions.push('BFCache lifecycle suspension stops polling and restoration requests one eligible check.');
  await page.evaluate(() => { window.failNextWorkspaceClose = true; });
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
  await page.locator('#workspace-update').waitFor({ state: 'visible' });
  await addMaterial(page);
  await page.getByRole('dialog').getByRole('button', { name: /^Note / }).click();
  await page.getByLabel('Content', { exact: true }).fill('Keep this while another client activates.');
  await oldClient.evaluate(async () => { const registration = await navigator.serviceWorker.getRegistration(); registration.waiting.postMessage({ type: 'ACTIVATE_UPDATE' }); });
  await page.locator('#workspace-update').waitFor({ state: 'detached' });
  assert.equal(await page.getByLabel('Content', { exact: true }).inputValue(), 'Keep this while another client activates.');
  report.assertions.push('Another client activating clears the stale update action without reloading or losing this tab’s draft.');
  const beforeDispose = await page.evaluate(() => window.updateCheckCalls);
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: false })));
  await page.clock.fastForward(10 * 60_000);
  await page.evaluate(() => { window.dispatchEvent(new Event('online')); document.dispatchEvent(new Event('visibilitychange')); window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })); });
  assert.equal(await page.evaluate(() => window.updateCheckCalls), beforeDispose);
  report.assertions.push('An ordinary departure disposes discovery; timers and later events cannot restart it.');
  await page.evaluate(() => window.offerInstallProof('after-departure'));
  assert.equal(await page.getByRole('button', { name: 'Install on this device', exact: true }).count(), 0);
  assert.ok(!(await page.evaluate(() => window.installProofCalls)).includes('after-departure'));
  report.assertions.push('Departed-page cleanup also removes install event handling and cannot revive an install action.');
  assert.equal(blockedProviderRequests, 0, 'Cancelling synthetic microphone capture must not request provider execution.');
  report.passed = true;
} catch (error) { report.failure = 'Update acceptance failed.'; await mkdir(join(root, 'artifacts'), { recursive: true }); await writeFile(join(root, 'artifacts/update-diagnostics.txt'), String(error.stack)); process.exitCode = 1; }
finally {
  await browser.close(); await server.close(); await rm(temporary, { recursive: true, force: true });
  report.finished_at = new Date().toISOString();
  report.blocked_provider_requests = blockedProviderRequests;
  await mkdir(join(root, 'evidence'), { recursive: true });
  await writeFile(join(root, 'evidence/update.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report));
}
