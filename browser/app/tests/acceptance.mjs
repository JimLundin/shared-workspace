import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { chromium } from 'playwright';
import { startStaticServer } from './static-server.mjs';

const directory = new URL('../', import.meta.url).pathname;
const server = await startStaticServer(join(directory, 'dist'));
const profiles = await mkdtemp(join(tmpdir(), 'workspace-ui-'));
const contexts = new Set(); const assertions = []; const diagnostics = []; const secrets = new Set();
function scrub(value) { let result = String(value); for (const secret of secrets) result = result.replaceAll(secret, '[invitation redacted]'); return result.replace(/(?:workspace-browser-v1|w2)\.[A-Za-z0-9_-]+/g, '[invitation redacted]'); }
const report = { passed: false, started_at: new Date().toISOString(), assertions,
  scope: 'Built production UI with real BrowserHost, Turso OPFS and Iroh peers; headers-free static /shared-workspace/ origin.',
  limits: ['Desktop Chromium with phone viewport; no physical iPhone result.', 'Browser speech adapter tested separately; no microphone recognition claim.', 'No public deployment from this test.'] };
let stage = 'setup';
function record(value) { assertions.push(value); console.log(JSON.stringify({ passed: value })); }
function step(value) { stage = value; console.log(JSON.stringify({ stage })); }
async function eventually(work, label, timeout = 60000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) { if (await work()) return; await new Promise(resolve => setTimeout(resolve, 200)); }
  throw new Error(label);
}
async function start(name, width = 1280, offline = false) {
  const context = await chromium.launchPersistentContext(join(profiles, name), { headless: true, viewport: { width, height: 900 }, offline });
  contexts.add(context); context.setDefaultTimeout(20000);
  const page = context.pages()[0];
  page.on('pageerror', error => diagnostics.push({ stage, error: error.message.slice(0, 250) }));
  page.on('console', message => { if (message.type() === 'error') diagnostics.push({ stage, error: message.text().slice(0, 250) }); });
  return { context, page };
}
async function ready(page, path = server.url) {
  await page.goto(path);
  await page.locator('#workspace-picker').waitFor({ state:'attached', timeout: 90000 });
  assert.equal(await page.evaluate(() => crossOriginIsolated), true);
}
async function nav(page, title) {
  if(await page.locator('dialog[open]').count())await page.getByRole('button',{name:'Close dialog',exact:true}).click();
  await page.getByRole('button',{name:/Current workspace:/}).click();
  try {await page.getByRole('dialog').getByRole('button',{name:title,exact:true}).click();}
  catch(error){diagnostics.push({stage,ui:await page.evaluate(()=>({history:history.state,dialogs:Array.from(document.querySelectorAll('dialog')).map(el=>({name:el.getAttribute('aria-label'),open:el.open})),buttons:Array.from(document.querySelectorAll('button')).map(el=>({text:el.textContent,action:el.dataset.action}))}))});throw error}
}

async function edit(page, body) {
  await focus(page);
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  await page.getByLabel('Content', { exact: true }).fill(body);
  await page.getByRole('button', { name: 'Save note', exact: true }).click();
  await eventually(() => page.locator('.editor').count().then(count => count === 0), 'Save did not return a receipt.');
}
async function note(page, body) { await eventually(() => page.locator('.note-body').allTextContents().then(texts => texts.includes(body)), 'Expected committed note did not appear.'); }
async function focus(page) { const open=page.getByRole('button',{name:'Open Launch brief',exact:true});if(await open.count())await open.click(); }
async function identity(page) { return page.evaluate(() => Object.keys(localStorage).find(key => /presentation/.test(key))); }
async function stop(peer) { await peer.context.close(); contexts.delete(peer.context); }
try {
  step('static-startup');
  const desktop = await start('desktop'); await ready(desktop.page);
  assert.equal(await desktop.page.locator('.workspace-tile').count(), 0);
  assert.equal(await desktop.page.locator('.resource-card').count(), 0);
  record('Fresh browser shows genuine empty host; service worker enables isolation without server headers.');
  const desktopIdentity = await identity(desktop.page); assert.ok(desktopIdentity);
  await desktop.page.getByLabel('Workspace name', { exact: true }).fill('Launch work');
  await desktop.page.getByRole('button', { name: 'Create workspace', exact: true }).click();
  await desktop.page.getByRole('button', { name: 'Current workspace: Launch work', exact: true }).waitFor();
  await desktop.page.getByRole('button', { name: 'Add to workspace', exact: true }).click();
  await desktop.page.getByRole('dialog').getByRole('button', { name: /^Note / }).click();
  await desktop.page.getByLabel('Title', { exact: true }).fill('Launch brief');
  await desktop.page.getByLabel('Content', { exact: true }).fill('The launch starts with three pilot teams.');
  await desktop.page.getByRole('button', { name: 'Save note', exact: true }).click();
  await note(desktop.page, 'The launch starts with three pilot teams.');
  record('Workspace and note created through UI receive real durable host receipts.');
  await mkdir(join(directory, 'artifacts'), { recursive: true });
  await desktop.page.screenshot({ path: join(directory, 'artifacts/desktop.png') });
  await desktop.page.getByRole('button',{name:/Current workspace:/}).click();await desktop.page.getByRole('button',{name:'Appearance',exact:true}).click();await desktop.page.getByRole('button',{name:'Dark',exact:true}).click();
  await desktop.page.emulateMedia({colorScheme:'dark'});await desktop.page.emulateMedia({colorScheme:'light'});assert.equal(await desktop.page.locator('html').getAttribute('data-theme'),'dark');
  await desktop.page.getByRole('button',{name:'Light',exact:true}).click();await desktop.page.emulateMedia({colorScheme:'dark'});assert.equal(await desktop.page.locator('html').getAttribute('data-theme'),'light');await desktop.page.getByRole('button',{name:'Close dialog'}).click();
  record('Production startup theme observer cannot override explicit Light or Dark after an OS preference change.');
  step('pairing');
  await desktop.page.getByRole('button', { name: 'Share this workspace' }).click();
  await desktop.page.getByRole('button', { name: 'Create invitation', exact: true }).click();
  await desktop.page.getByLabel('Pairing ticket', { exact: true }).waitFor({ state: 'attached', timeout: 60000 });
  assert.equal(await desktop.page.getByLabel('Pairing ticket', { exact: true }).isVisible(), false);
  const ticket = await desktop.page.getByLabel('Pairing ticket', { exact: true }).inputValue();
  secrets.add(ticket);
  const link = new URL(server.url); link.hash = new URLSearchParams({ invite: ticket });
  const phone = await start('phone', 390); await ready(phone.page, link.href);
  assert.equal(new URL(phone.page.url()).hash, '');
  assert.equal(await phone.page.getByLabel('Invitation to join', { exact: true }).inputValue(), ticket);
  assert.equal(await phone.page.locator('.resource-card').count(), 0);
  assert.notEqual(await identity(phone.page), desktopIdentity);
  const storage = await phone.page.evaluate(() => ({ local: JSON.stringify(localStorage), session: JSON.stringify(sessionStorage) }));
  assert.ok(!storage.local.includes(ticket) && !storage.session.includes(ticket));
  record('Phone fragment survives initial isolation reload, clears from URL, remains memory-only, and does not auto-join.');
  await phone.page.evaluate(() => {
    window.observedJoinLabels = [];
    const observer = new MutationObserver(() => {
      const label = document.querySelector('.join-progress strong')?.textContent;
      if (label && !window.observedJoinLabels.includes(label)) window.observedJoinLabels.push(label);
    });
    observer.observe(document.getElementById('app'), { childList: true, subtree: true });
  });
  await phone.page.getByRole('button', { name: 'Join selected workspaces', exact: true }).click();
  await phone.page.getByRole('button', { name: 'Current workspace: Launch work', exact: true }).waitFor({ timeout: 90000 });
  assert.equal(await phone.page.locator('dialog[open]').count(),0);
  const stages = await phone.page.evaluate(() => window.observedJoinLabels);
  for (const label of ['Checking invitation', 'Connecting this device', 'Contacting the inviting device', 'Saving work on this device']) assert.ok(stages.includes(label));
  record('Observed host stages reach the production UI through the real worker before joining completes.');
  await focus(phone.page); await note(phone.page, 'The launch starts with three pilot teams.');
  record('Explicit phone Join completes an authenticated peer exchange and imports the real note.');
  step('two-way-edits');
  await edit(phone.page, 'Phone revision retained.');
  await nav(desktop.page, 'Launch work'); await note(desktop.page, 'Phone revision retained.');
  await edit(desktop.page, 'Desktop revision returned.'); await note(phone.page, 'Desktop revision returned.');
  record('Notes edited from each production UI converge across isolated real browser hosts.');
  // A command keeps its captured address even after opening another workspace.
  await phone.page.getByRole('button',{name:/Current workspace:/}).click();
  await phone.page.getByRole('button',{name:'Create workspace',exact:true}).click();
  await phone.page.getByLabel('Workspace name',{exact:true}).fill('Private');
  await phone.page.getByRole('button',{name:'Create workspace',exact:true}).click();
  await phone.page.getByRole('dialog').waitFor({state:'hidden'});
  await phone.page.getByRole('button',{name:'Current workspace: Private',exact:true}).waitFor();
  await nav(phone.page,'Launch work');
  await phone.page.getByRole('button', { name: 'Type instead', exact: true }).click();
  await phone.page.getByLabel('Your words', { exact: true }).fill('Remember a separate task');
  await phone.page.getByRole('button',{name:'Close dialog'}).click();
  await nav(phone.page,'Private');
  await phone.page.getByRole('button',{name:'Review capture',exact:true}).click();
  assert.equal(await phone.page.locator('#run-command').isDisabled(), true);
  await phone.page.getByRole('button', { name: 'Return to Launch work' }).click();
  assert.equal(await phone.page.locator('#run-command').isDisabled(), false);
  await phone.page.getByRole('button', { name: 'New capture' }).click();
  await phone.page.getByRole('button',{name:'Close dialog'}).click();
  const width = await phone.page.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth }));
  assert.ok(width.content <= width.viewport);
  await phone.page.screenshot({ path: join(directory, 'artifacts/phone.png') });
  record('Phone material fits viewport, picker is on demand, and capture keeps its original workspace across navigation.');
  step('stale-editor');
  await phone.page.getByRole('button', { name: 'Edit', exact: true }).click();
  await phone.page.getByLabel('Content', { exact: true }).fill('Unsaved phone edit.');
  await edit(desktop.page, 'Concurrent desktop edit.');
  await phone.page.getByRole('heading', { name: 'Edit note', exact: true }).click();
  await eventually(() => phone.page.locator('.comparison').count().then(count => count > 0), 'New version did not produce comparison.');
  assert.equal(await phone.page.getByLabel('Content', { exact: true }).inputValue(), 'Unsaved phone edit.');
  await phone.page.getByRole('button', { name: 'Save my edit as a new revision', exact: true }).click();
  await note(desktop.page, 'Unsaved phone edit.');
  record('Peer update preserves unsaved editor; explicit reconciliation saves a new revision.');
  step('offline-restart');
  await stop(desktop); await stop(phone);
  const reopened = await start('desktop', 1280, true); await ready(reopened.page);
  assert.equal(await identity(reopened.page), desktopIdentity);
  await note(reopened.page, 'Unsaved phone edit.');
  await edit(reopened.page, 'Edited while offline.');
  record('Offline browser-process restart opens cached application and retained identity/work; offline save succeeds.');
  await reopened.context.setOffline(false);
  const phoneAgain = await start('phone', 390); await ready(phoneAgain.page); await note(phoneAgain.page, 'Edited while offline.');
  record('Foreground reconnection exchanges the offline edit with the paired browser.');
  const cacheKeys = await reopened.page.evaluate(async () => {
    const result = []; for (const name of await caches.keys()) for (const request of await (await caches.open(name)).keys()) result.push(request.url); return result;
  });
  assert.ok(cacheKeys.length > 5); assert.ok(cacheKeys.every(url => !url.includes('#') && !url.includes('?') && !url.includes(ticket)));
  record('Service-worker caches contain application assets only; no fragment, pairing ticket, or workspace records.');
  const assets = await readdir(join(directory, 'dist/assets')); const js = await Promise.all(assets.filter(file => file.endsWith('.js')).map(file => readFile(join(directory, 'dist/assets', file), 'utf8')));
  assert.ok(!js.some(source => /plugin:workspace-voice|__TAURI_INTERNALS__|plugin:barcode-scanner/.test(source)));
  record('Production browser bundle contains no native bridge or native speech/scanner calls.');
  report.asset_hash = createHash('sha256').update(await readFile(join(directory, 'dist/sw.js'))).digest('hex');
  report.passed = true;
} catch (error) {
  report.failure = { stage, message: 'An acceptance expectation failed. Redacted diagnostics are in ignored local artifacts.' };
  diagnostics.push({ stage, error: error.stack });
  process.exitCode = 1;
} finally {
  await Promise.all([...contexts].map(context => context.close())); await server.close();
  report.finished_at = new Date().toISOString();
  await mkdir(join(directory, 'evidence'), { recursive: true }); await mkdir(join(directory, 'artifacts'), { recursive: true });
  await writeFile(join(directory, 'evidence/production-ui.json'), JSON.stringify(report, null, 2) + '\n');
  await writeFile(join(directory, 'artifacts/diagnostics.json'), scrub(JSON.stringify(diagnostics, null, 2)) + '\n');
  await rm(profiles, { recursive: true, force: true });
  console.log(JSON.stringify({ passed: report.passed, stage, assertions: assertions.length }));
}
