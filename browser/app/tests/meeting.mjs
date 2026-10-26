import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { chromium, webkit } from 'playwright';
import { controlledMeetingProvider, desiredOutcome, syntheticCredential, transcriptQuote } from './controlled-meeting-provider.mjs';
import { startStaticServer } from './static-server.mjs';
import { addMaterial, closePanel, openSettings, pageAction } from './ui-controls.mjs';

const directory = fileURLToPath(new URL('../', import.meta.url));
const { values } = parseArgs({ options: {
  dist: { type: 'string', default: join(directory, 'dist') },
  evidence: { type: 'string', default: join(directory, 'evidence') },
  engine: { type: 'string', default: 'both' },
} });
assert(['chromium', 'webkit', 'both'].includes(values.engine), 'Unsupported browser engine.');
const engines = values.engine === 'both' ? ['chromium', 'webkit'] : [values.engine];
const dist = resolve(values.dist), evidence = resolve(values.evidence);
const report = {
  passed: false, started_at: new Date().toISOString(), node: process.version,
  scope: 'Served production renderer, worker, local Turso journal and signed Work commands in isolated persistent browser profiles.',
  limits: [
    'Synthetic credential and generated provider response; no authenticated OpenAI request.',
    'Chromium desktop and WebKit compact viewport; no physical iPhone, microphone or Home Screen validation.',
    'Follow-up is editable unsent text; no email or application action is executed.',
    'The local TLS fixture forwards no provider traffic. Certificate exceptions apply only to these temporary profiles.',
    'Other proxy destinations are rejected; this gate does not establish peer delivery.',
  ],
  engines: [],
};

function scrub(value, limit = 1500) {
  return String(value).replaceAll(syntheticCredential, '[synthetic credential]')
    .replace(/(?:workspace-browser-v1|w2)\.[A-Za-z0-9_-]+/g, '[invitation redacted]')
    .replace(/(?:https?:\/\/)[^\s"'<>]+/g, '[URL redacted]').slice(0, limit);
}
function failure(error, stage) {
  return { stage, name: scrub(error?.name ?? 'Error', 80), message: scrub(error?.message ?? error) };
}
async function assetSnapshot(path = dist, prefix = '') {
  const result = {};
  for (const entry of (await readdir(path, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    const name = prefix + entry.name;
    if (entry.isDirectory()) Object.assign(result, await assetSnapshot(join(path, entry.name), name + '/'));
    else {
      assert(entry.isFile(), 'Built assets must be regular files.');
      result[name] = createHash('sha256').update(await readFile(join(path, entry.name))).digest('hex');
    }
  }
  return result;
}
async function visibleText(page, selector, text) {
  await page.waitForFunction(({ selector, text }) => [...document.querySelectorAll(selector)]
    .some(element => element.getClientRects().length && element.textContent.includes(text)), { selector, text }, { timeout: 30000 });
}
async function openWork(page) {
  if (await page.locator('.meeting-work').isVisible()) return;
  const all = page.locator('.capture-dock').getByRole('button', { name: 'All material', exact: true });
  if (await all.isVisible()) await all.click();
  else await pageAction(page, 'All material');
  await page.getByRole('button', { name: 'Open Review continuity', exact: true }).click();
  await page.locator('.meeting-work').waitFor();
}

async function checkEngine(engineName) {
  const result = { engine: engineName, passed: false, assertions: [], browser_lifetimes: [], cleanup: {} };
  report.engines.push(result);
  const contexts = new Set();
  let profile, server, provider, context, page, stage = 'startup';
  function check(name, value = true) {
    assert(value, name);
    result.assertions.push(name);
    console.log(JSON.stringify({ engine: engineName, passed: name }));
  }
  async function start(offline) {
    const next = await { chromium, webkit }[engineName].launchPersistentContext(profile, {
      headless: true, proxy: { server: provider.server, bypass: '127.0.0.1,localhost,[::1]' },
      ignoreHTTPSErrors: true, viewport: { width: engineName === 'webkit' ? 390 : 1280, height: 900 },
    });
    contexts.add(next);
    next.setDefaultTimeout(30000);
    if (offline) await next.setOffline(true);
    result.browser_lifetimes.push({ version: next.browser().version(), offline });
    return next;
  }
  async function ready() {
    await page.goto(server.url, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: /^Current workspace:/, includeHidden: true }).waitFor({ state: 'attached', timeout: 60000 });
    check('Served app starts with the real cross-origin-isolated host.', await page.evaluate(() => crossOriginIsolated));
  }
  try {
    profile = await mkdtemp(join(tmpdir(), 'workspace-built-meeting-'));
    server = await startStaticServer(dist);
    provider = await controlledMeetingProvider(new URL(server.url).origin);
    result.provider = provider.stats;
    context = await start(false);
    page = context.pages()[0] ?? await context.newPage();
    await ready();

    stage = 'create-workspace';
    await page.getByLabel('Workspace name', { exact: true }).fill('Rendered meeting proof');
    await page.getByRole('button', { name: 'Create workspace', exact: true }).click();
    await page.getByRole('button', { name: 'Current workspace: Rendered meeting proof', exact: true, includeHidden: true }).waitFor({ state: 'attached' });

    stage = 'configure';
    await openSettings(page);
    await page.getByRole('dialog', { name: 'Settings', exact: true }).getByRole('button', { name: /^OpenAI/ }).click();
    await page.getByLabel('OpenAI API key', { exact: true }).fill(syntheticCredential);
    await page.getByRole('button', { name: 'Save connection', exact: true }).click();
    await visibleText(page, '.connection-state', 'A connection is saved for this workspace.');
    await closePanel(page);
    check('Workspace connection is saved through the actual private settings and worker.');

    stage = 'import';
    await addMaterial(page);
    await page.getByRole('dialog', { name: 'Add to workspace', exact: true }).getByRole('button', { name: /^Meeting work / }).click();
    await page.getByLabel('Work title', { exact: true }).fill('Review continuity');
    await page.getByLabel('Desired outcome', { exact: true }).fill(desiredOutcome);
    await page.locator('#transcript-file').setInputFiles([
      { name: 'recording-1.txt', mimeType: 'text/plain', buffer: Buffer.from(transcriptQuote) },
      { name: 'recording-2.txt', mimeType: 'text/plain', buffer: Buffer.from('Keep the launch small.') },
      { name: 'recording-3.txt', mimeType: 'text/plain', buffer: Buffer.from('The meeting ended after reviewing next steps.') },
    ]);
    await visibleText(page, '.dialog-status', '3 transcript parts imported');
    check('Three imported transcripts become selected real saved sources.', await page.locator('[data-meeting-source]:checked').count() === 3);

    stage = 'create';
    await page.getByRole('button', { name: 'Create work', exact: true }).click();
    await page.locator('.meeting-work').waitFor();
    assert.equal(await page.locator('.work-sources button').count(), 3, 'Work retains all three pinned transcripts.');
    check('Work creation does not start the provider.', provider.stats.requests === 0);

    stage = 'prepare';
    await page.getByRole('button', { name: 'Prepare meeting material', exact: true }).click();
    await visibleText(page, '.meeting-work', 'Material ready for review');
    check('Rendered preparation reaches the worker and publishes one cited result.', provider.stats.requests === 1
      && provider.stats.accepted_requests === 1 && provider.stats.rejected_requests === 0
      && provider.stats.captured_source_count === 3 && provider.stats.synthetic_credential);
    const action = await page.locator('.meeting-action').innerText();
    check('Generated owner and deadline appear with transcript evidence.', action.includes('Mira') && action.includes('next Friday'));

    stage = 'pinned-source';
    await page.locator('.work-citation').first().getByRole('button').click();
    await visibleText(page, '.pinned-source .note-body', transcriptQuote);
    await visibleText(page, '.pinned-source', 'The exact revision cited by this work.');
    await closePanel(page);
    check('A generated citation opens its exact saved transcript revision.');

    stage = 'correct';
    await page.getByRole('button', { name: 'Correct owners or deadlines', exact: true }).click();
    await page.getByLabel('Owner', { exact: true }).fill('Jim');
    await page.getByLabel('Deadline', { exact: true }).fill('Next Tuesday');
    await page.getByRole('button', { name: 'Save corrections', exact: true }).click();
    await visibleText(page, '.meeting-action', 'Jim · user correction');
    await visibleText(page, '.meeting-action', 'Next Tuesday · user correction');
    check('Corrections commit as user evidence through the actual Work command.');

    stage = 'follow-up';
    await visibleText(page, '.meeting-work', 'Follow-up · unsent text');
    await page.getByRole('button', { name: 'Open and edit follow-up', exact: true }).click();
    await visibleText(page, '.note-body', 'For review: ' + transcriptQuote);
    await pageAction(page, 'Edit');
    await page.getByLabel('Content', { exact: true }).fill('Edited follow-up retained across reopen.');
    await page.getByRole('button', { name: 'Save note', exact: true }).click();
    await visibleText(page, '.note-body', 'Edited follow-up retained across reopen.');
    check('The unsent follow-up is an actual editable saved document.');

    stage = 'offline-process-reopen';
    await context.close();
    contexts.delete(context);
    context = await start(true);
    page = context.pages()[0] ?? await context.newPage();
    await ready();
    await openWork(page);
    await visibleText(page, '.meeting-action', 'Jim · user correction');
    await visibleText(page, '.meeting-action', 'Next Tuesday · user correction');
    assert.equal(await page.locator('.work-sources button').count(), 3);
    await page.getByRole('button', { name: 'Open and edit follow-up', exact: true }).click();
    await visibleText(page, '.note-body', 'Edited follow-up retained across reopen.');
    assert.equal(provider.stats.requests, 1, 'Reopening saved work does not call the provider again.');
    assert.equal(provider.stats.forwarded, 0, 'The fixture forwards no provider traffic.');
    check('A new offline browser lifetime retains the work, corrections and edited follow-up.');
    result.passed = true;
  } catch (error) {
    result.failure = failure(error, stage);
    if (page && !page.isClosed()) {
      const ui = await page.evaluate(() => ({
        main: document.querySelector('.main')?.textContent.slice(0, 2500),
        dialog: document.querySelector('dialog[open]')?.textContent.slice(0, 1500),
      })).catch(() => null);
      if (ui) result.failure_ui = Object.fromEntries(Object.entries(ui).map(([key, value]) => [key, scrub(value ?? '', 2500)]));
    }
    console.error(JSON.stringify({ engine: engineName, failure: result.failure }));
  } finally {
    async function clean(name, operation) {
      try { await operation(); result.cleanup[name] = true; }
      catch (error) {
        result.cleanup[name] = false;
        result.passed = false;
        (result.cleanup_errors ??= []).push(failure(error, name));
      }
    }
    await clean('contexts_closed', async () => {
      for (const item of contexts) { await item.close(); contexts.delete(item); }
    });
    await clean('provider_closed', async () => { await provider?.close(); });
    await clean('server_closed', async () => { await server?.close(); });
    await clean('profile_removed', async () => { if (profile) await rm(profile, { recursive: true, force: true }); });
  }
  return result;
}

await mkdir(evidence, { recursive: true });
try {
  report.asset_hashes = await assetSnapshot();
  assert(report.asset_hashes['index.html'] && report.asset_hashes['sw.js'], 'Build the complete app before running meeting acceptance.');
  report.harness_hashes = {};
  for (const name of ['meeting.mjs', 'controlled-meeting-provider.mjs', 'static-server.mjs', 'ui-controls.mjs']) {
    report.harness_hashes[name] = createHash('sha256').update(await readFile(join(directory, 'tests', name))).digest('hex');
  }
  for (const engine of engines) await checkEngine(engine);
  report.final_asset_hashes = await assetSnapshot();
  assert.deepEqual(report.final_asset_hashes, report.asset_hashes, 'Every emitted asset must remain unchanged through acceptance.');
  report.assets_unchanged = true;
  report.passed = report.engines.length === engines.length && report.engines.every(result => result.passed);
} catch (error) {
  report.failure = failure(error, 'asset-verification');
  console.error(JSON.stringify(report.failure));
} finally {
  report.finished_at = new Date().toISOString();
  await writeFile(join(evidence, 'meeting.json'), JSON.stringify(report, null, 2) + '\n');
}
if (!report.passed) process.exitCode = 1;
