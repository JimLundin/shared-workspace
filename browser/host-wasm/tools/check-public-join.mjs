import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { chromium, webkit } from '../../checks/node_modules/playwright/index.mjs';

const engines = { chromium, webkit };
const args = process.argv.slice(2);
const option = (key, fallback) => args.includes(key) ? args[args.indexOf(key) + 1] : fallback;
const target = option('--url', 'https://jimlundin.github.io/shared-workspace/');
const inviterEngine = option('--inviter', 'chromium');
const joinerEngine = option('--joiner', 'webkit');
const evidence = resolve(option('--evidence', '/tmp/public-join-diagnosis'));
const contexts = [];
const secrets = new Set();
const observations = [];
const routes = [];
let stage = 'launch';
let failure;
let joinStatus = [];
let joined = false;
let profiles;
let profilesRemoved = false;
let contextsClosed = false;
const transfers = { initial_note: false, joiner_edit: false, inviter_edit: false };
function scrub(value) {
  let result = String(value);
  for (const secret of secrets) result = result.replaceAll(secret, '[invitation redacted]').replaceAll(encodeURIComponent(secret), '[invitation redacted]');
  return result.replace(/workspace-browser-v1\.[A-Za-z0-9_-]+|\bw2\.[A-Za-z0-9_-]+/g, '[invitation redacted]')
    .replace(/(?:https?:\/\/|blob:)[^\s"'<>]+/g, '[URL redacted]')
    .replace(/\b[a-fA-F0-9]{32,}\b/g, '[identifier redacted]')
    .replace(/[A-Za-z0-9_=-]{64,}/g, '[opaque value redacted]').slice(0, 1200);
}
async function start(name, engine, mobile) {
  // WebKit's ephemeral contexts expose OPFS but reject getDirectory(). Each
  // participant needs its own fresh persistent profile, removed after closure.
  const context = await engines[engine].launchPersistentContext(join(profiles, name), {
    headless: true, viewport: { width: mobile ? 390 : 1280, height: 900 },
  });
  contexts.push(context);
  context.setDefaultTimeout(20_000);
  const page = context.pages()[0] ?? await context.newPage();
  page.on('websocket', (socket) => {
    const route = { device: name, origin: new URL(socket.url()).origin, created: true, sent_frames: 0, received_frames: 0, failed: false, closed: false };
    routes.push(route);
    socket.on('socketerror', () => { route.failed = true; });
    socket.on('framesent', () => { route.sent_frames++; });
    socket.on('framereceived', () => { route.received_frames++; });
    socket.on('close', () => { route.closed = true; });
  });
  observations.push({ device: name, engine, version: context.browser().version(), profile: 'fresh-persistent' });
  return { page, context };
}
async function ready(page, url, device) {
  const started = Date.now();
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45_000 });
  await page.waitForFunction(() => {
    const nav = document.querySelector('nav[aria-label="Workspaces"]');
    return Boolean((nav && nav.getBoundingClientRect().width && nav.getBoundingClientRect().height)
      || document.querySelector('.boot [role="alert"]'));
  }, undefined, { timeout: 80_000 });
  const alert = page.locator('.boot [role="alert"]');
  if (await alert.count()) throw new Error(`Startup failed: ${scrub(await alert.innerText())}`);
  const isolated = await page.evaluate(() => crossOriginIsolated);
  assert(isolated, 'The ready application must be cross-origin isolated.');
  observations.push({ stage, device, startup_ms: Date.now() - started, isolated, nav_ready: true });
}
async function readInvitation(page) {
  const input = page.getByLabel('Pairing ticket', { exact: true });
  await input.waitFor({ state: 'attached', timeout: 45_000 });
  const initiallyHidden = !await input.isVisible();
  if (initiallyHidden) {
    await page.locator('details[data-disclosure="outgoing"] > summary').click();
    await input.waitFor({ state: 'visible' });
  }
  observations.push({ stage, ticket_initially_hidden: initiallyHidden, disclosure_opened: initiallyHidden });
  const ticket = await input.inputValue();
  secrets.add(ticket);
  assert(ticket.length > 0, 'The invitation must not be empty.');
  return ticket;
}
async function navigate(page, title) {
  await page.locator('nav[aria-label="Workspaces"]').getByRole('button', { name: title, exact: true }).click();
}
async function note(page, content) {
  await page.waitForFunction(expected => [...document.querySelectorAll('.note-body')].some(element => element.textContent === expected), content, { timeout: 45_000 });
}
async function edit(page, content) {
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  await page.getByLabel('Content', { exact: true }).fill(content);
  await page.getByRole('button', { name: 'Save note', exact: true }).click();
  await page.locator('.editor').waitFor({ state: 'detached' });
  await note(page, content);
}
try {
  assert(engines[inviterEngine] && engines[joinerEngine], 'Choose chromium or webkit for each participant.');
  const destination = new URL(target);
  assert(!destination.hash && !destination.search && !destination.username && !destination.password, 'The target must be a base app URL without credentials, query, or fragment.');
  profiles = await mkdtemp(join(tmpdir(), 'workspace-public-smoke-'));
  const inviter = await start('inviter', inviterEngine, false);
  stage = 'inviter-startup';
  await ready(inviter.page, target, 'inviter');
  stage = 'create-workspace';
  const title = `Join diagnosis ${Date.now()}`;
  await inviter.page.getByLabel('Workspace name', { exact: true }).fill(title);
  await inviter.page.getByRole('button', { name: 'Create workspace', exact: true }).click();
  await inviter.page.getByRole('heading', { name: title, exact: true }).waitFor();
  stage = 'create-note';
  const initialContent = 'Public smoke: initial note retained.';
  const joinerContent = 'Public smoke: revision from the joining device.';
  const inviterContent = 'Public smoke: revision returned by the inviting device.';
  await inviter.page.getByRole('button', { name: 'Add a note', exact: true }).click();
  await inviter.page.getByLabel('Title', { exact: true }).fill('Public smoke note');
  await inviter.page.getByLabel('Content', { exact: true }).fill(initialContent);
  await inviter.page.getByRole('button', { name: 'Save note', exact: true }).click();
  await note(inviter.page, initialContent);
  stage = 'create-invitation';
  await inviter.page.getByRole('button', { name: 'Share this workspace' }).click();
  await inviter.page.getByRole('button', { name: 'Create invitation', exact: true }).click();
  const ticket = await readInvitation(inviter.page);
  observations.push({ stage, ticket_characters: ticket.length, inviter_visible: await inviter.page.evaluate(() => document.visibilityState) });
  const isEntryUrl = /^https?:\/\//.test(ticket);
  const link = isEntryUrl ? new URL(ticket) : new URL(target);
  if (!isEntryUrl) link.hash = new URLSearchParams({ invite: ticket });
  const encoded = new URLSearchParams(link.hash.slice(1)).get('invite');
  if (encoded) secrets.add(encoded);
  secrets.add(link.href);
  assert(link.origin === destination.origin && link.pathname === destination.pathname, 'Invitation entry must use this application origin and path.');
  assert(encoded, 'The entry link must contain one invitation fragment.');
  const joiner = await start('joiner', joinerEngine, true);
  stage = 'joiner-startup';
  await ready(joiner.page, link.href, 'joiner');
  assert(new URL(joiner.page.url()).hash === '', 'The invitation fragment must be removed after startup.');
  await joiner.page.getByLabel('Invitation to join', { exact: true }).waitFor({ state: 'attached' });
  const loadedInvitation = await joiner.page.getByLabel('Invitation to join', { exact: true }).inputValue();
  assert(loadedInvitation === ticket || loadedInvitation === encoded, 'Displayed invitation must retain the same token');
  observations.push({ stage, ticket_preserved: true, inviter_visible: await inviter.page.evaluate(() => document.visibilityState) });
  stage = 'join-selected-workspaces';
  const started = Date.now();
  assert(await inviter.page.evaluate(() => document.visibilityState === 'visible'), 'The inviting page must stay visible during Join.');
  await joiner.page.getByRole('button', { name: 'Join selected workspaces', exact: true }).click();
  try {
    await joiner.page.waitForFunction(expected => [...document.querySelectorAll('nav[aria-label="Workspaces"] button')].some(button => button.textContent === expected)
      || Boolean(document.querySelector('.join-panel [role="alert"]')), title, { timeout: 45_000 });
    joined = await joiner.page.locator('nav[aria-label="Workspaces"]').getByRole('button', { name: title, exact: true }).count() === 1;
    if (!joined) throw new Error(`Join failed: ${scrub(await joiner.page.locator('.join-panel [role="alert"]').innerText())}`);
  } finally {
    joinStatus = (await joiner.page.locator('.status-message').allTextContents()).map(scrub);
    observations.push({ stage, elapsed_ms: Date.now() - started, joined, inviter_visible: await inviter.page.evaluate(() => document.visibilityState) });
  }
  assert(joined, 'Join must import the selected workspace before timing out');
  stage = 'verify-imported-note';
  await navigate(joiner.page, title);
  await note(joiner.page, initialContent);
  transfers.initial_note = true;
  stage = 'joiner-edit-transfer';
  await edit(joiner.page, joinerContent);
  await navigate(inviter.page, title);
  await note(inviter.page, joinerContent);
  transfers.joiner_edit = true;
  stage = 'inviter-edit-transfer';
  await edit(inviter.page, inviterContent);
  await note(joiner.page, inviterContent);
  transfers.inviter_edit = true;
  observations.push({ stage: 'complete', joined, ...transfers });
} catch (error) {
  failure = { stage, message: scrub(error instanceof Error ? error.message : 'The public UI check failed.') };
} finally {
  const closed = await Promise.allSettled(contexts.map(async context => {
    try { await context.close(); }
    catch { await context.browser()?.close(); }
  }));
  contextsClosed = closed.every(result => result.status === 'fulfilled');
  try { if (profiles) await rm(profiles, { recursive: true, force: true }); profilesRemoved = true; }
  catch { profilesRemoved = false; }
  if (!contextsClosed || !profilesRemoved) failure ??= { stage: 'cleanup', message: 'Temporary browser cleanup did not complete.' };
  const report = {
    passed: !failure, target: (() => { try { const url = new URL(target); return url.origin + url.pathname; } catch { return '[invalid target]'; } })(), timestamp: new Date().toISOString(), observations,
    joined, transfers, join_status: joinStatus, relay_routes: routes,
    cleanup: { contexts_closed: contextsClosed, profiles_removed: profilesRemoved },
    scope: 'Served production UI, separate browser engines and fresh temporary persistent profiles, real Iroh pairing and bidirectional note edits; Linux WebKit is not physical iPhone Safari. No screenshots, traces, profiles, databases, seeds, or invitation values are exported.',
    ...(failure ? { failure } : {}),
  };
  await mkdir(evidence, { recursive: true });
  await writeFile(resolve(evidence, 'report.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
  if (failure) process.exitCode = 1;
}
