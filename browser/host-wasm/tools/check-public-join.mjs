import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { chromium, webkit } from '../../checks/node_modules/playwright/index.mjs';
import { startStaticServer } from '../../app/tests/static-server.mjs';

const engines = { chromium, webkit };
const args = process.argv.slice(2);
const option = (key, fallback) => args.includes(key) ? args[args.indexOf(key) + 1] : fallback;
let target = option('--url', 'https://jimlundin.github.io/shared-workspace/');
const dist = option('--dist', null);
const inviterEngine = option('--inviter', 'chromium');
const joinerEngine = option('--joiner', 'webkit');
const evidence = resolve(option('--evidence', '/tmp/public-join-diagnosis'));
const contexts = [];
const secrets = new Set();
const observations = [];
const routes = [];
const assertions = [];
let server;
let servedAssets;
let stage = 'launch';
let failure;
let joinStatus = [];
let joined = false;
let profiles;
let profilesRemoved = false;
let contextsClosed = false;
const transfers = { initial_note: false, joiner_edit: false, inviter_edit: false };
function record(name) { assertions.push(name); console.log(JSON.stringify({ passed: name })); }
async function hashes(directory, prefix = '') {
  const result = {};
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const name = prefix + entry.name, path = join(directory, entry.name);
    if (entry.isDirectory()) Object.assign(result, await hashes(path, name + '/'));
    else if (entry.isFile()) result[name] = createHash('sha256').update(await readFile(path)).digest('hex');
  }
  return Object.fromEntries(Object.entries(result).sort(([a], [b]) => a.localeCompare(b)));
}
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
  return { page, context, name, engine, mobile };
}
async function ready(page, url, device) {
  const started = Date.now();
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45_000 });
  await page.waitForFunction(() => {
    const current = document.querySelector('button[aria-label^="Current workspace:"]');
    return Boolean((current && current.getBoundingClientRect().width && current.getBoundingClientRect().height)
      || document.querySelector('.boot [role="alert"]'));
  }, undefined, { timeout: 80_000 });
  const alert = page.locator('.boot [role="alert"]');
  if (await alert.count()) throw new Error(`Startup failed: ${scrub(await alert.innerText())}`);
  const isolated = await page.evaluate(() => crossOriginIsolated);
  assert(isolated, 'The ready application must be cross-origin isolated.');
  observations.push({ stage, device, startup_ms: Date.now() - started, isolated, current_workspace_ready: true });
}
async function readInvitation(page) {
  const input = page.getByLabel('Pairing ticket', { exact: true });
  await input.waitFor({ state: 'attached', timeout: 45_000 });
  const initiallyHidden = !await input.isVisible();
  assert(initiallyHidden, 'Invitation text must remain behind its disclosure by default.');
  if (initiallyHidden) {
    await page.getByText('Show invitation for copy and paste', { exact: true }).click();
    await input.waitFor({ state: 'visible' });
  }
  observations.push({ stage, ticket_initially_hidden: initiallyHidden, disclosure_opened: initiallyHidden });
  const ticket = await input.inputValue();
  secrets.add(ticket);
  assert(ticket.length > 0, 'The invitation must not be empty.');
  return ticket;
}
async function navigate(page, title) {
  await page.getByRole('button', { name: /^Current workspace:/ }).click();
  const picker = page.getByRole('dialog', { name: 'Switch workspace', exact: true });
  await picker.getByRole('button', { name: title, exact: true }).click();
  await picker.waitFor({ state: 'hidden' });
  await page.getByRole('button', { name: 'Current workspace: ' + title, exact: true }).waitFor();
}
async function note(page, content) {
  await page.getByRole('article').getByText(content, { exact: true }).filter({ visible: true }).first().waitFor({ timeout: 45_000 });
}
async function openNote(page, title) {
  const open = page.getByRole('button', { name: 'Open ' + title, exact: true });
  if (await open.isVisible()) await open.click();
  await page.getByRole('button', { name: 'Edit', exact: true }).waitFor();
}
async function edit(page, content) {
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  await page.getByLabel('Content', { exact: true }).fill(content);
  await page.getByRole('button', { name: 'Save note', exact: true }).click();
  await page.getByLabel('Content', { exact: true }).waitFor({ state: 'hidden' });
  await note(page, content);
}
async function currentWork(page, title, hiddenTitle) {
  await page.getByRole('button', { name: 'Current workspace: ' + title, exact: true }).waitFor();
  assert.equal(await page.getByRole('dialog').count(), 0, 'Ordinary work must not leave a dialog open.');
  assert.equal(await page.getByRole('navigation', { name: /workspaces/i }).count(), 0, 'Workspace navigation must only appear on request.');
  assert.equal(await page.getByRole('button', { name: title, exact: true }).count(), 0, 'The workspace option must disappear when its picker closes.');
  if (hiddenTitle) assert.equal(await page.getByRole('button', { name: hiddenTitle, exact: true }).count(), 0, 'Other workspace choices must not persist beside current work.');
  assert.equal(await page.getByText(/\bToolkit\b/).filter({ visible: true }).count(), 0, 'Product surfaces must not show Toolkit branding.');
  assert.equal(await page.locator('[aria-label="Toolkit"]').count(), 0, 'Accessible branding must also omit Toolkit.');
  const width = await page.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth }));
  assert(width.content <= width.viewport + 1, 'The working surface must not overflow horizontally.');
}
async function modalChecks(page, name, checkRefreshFocus = false) {
  const modal = page.getByRole('dialog', { name, exact: true });
  await modal.waitFor();
  const geometry = await modal.evaluate(element => {
    const rect = element.getBoundingClientRect();
    const points = [[1, 1], [innerWidth - 2, 1], [1, innerHeight - 2], [innerWidth - 2, innerHeight - 2]];
    const background = document.querySelector('button[aria-label^="Current workspace:"]');
    background?.focus();
    return {
      native_modal: element.tagName === 'DIALOG' && element.matches(':modal'),
      backdrop: getComputedStyle(element, '::backdrop').backgroundColor,
      corners_blocked: points.every(([x, y]) => { const hit = document.elementFromPoint(x, y); return hit === element || element.contains(hit); }),
      background_cannot_focus: element.contains(document.activeElement),
      in_viewport: rect.left >= -1 && rect.top >= -1 && rect.right <= innerWidth + 1 && rect.bottom <= innerHeight + 1,
      focusable_count: element.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary, a[href]').length,
    };
  });
  assert(geometry.native_modal && geometry.corners_blocked, 'The native modal must intercept the whole viewport, including its corners.');
  assert(!['transparent', 'rgba(0, 0, 0, 0)'].includes(geometry.backdrop), 'The entire modal backdrop must have visible shading.');
  assert(geometry.background_cannot_focus, 'Underlying work must not receive focus while the dialog is open.');
  assert(geometry.in_viewport, 'The dialog must remain inside the visible viewport.');
  const chromeFocusTransitions = [];
  for (let i = 0; i < Math.min(geometry.focusable_count + 2, 24); i++) {
    await page.keyboard.press(i === 0 ? 'Shift+Tab' : 'Tab');
    const focus = await modal.evaluate(element => ({ inside: element.contains(document.activeElement), active_tag: document.activeElement?.tagName, document_has_focus: document.hasFocus() }));
    if (!focus.inside) {
      // Native dialogs in both engines allow traversal into browser chrome.
      // That loses document focus; it must never focus an underlying control.
      assert(focus.active_tag === 'BODY' && !focus.document_has_focus, 'Keyboard focus escaped to the background: ' + JSON.stringify(focus));
      await page.keyboard.press('Tab');
      assert(await modal.evaluate(element => element.contains(document.activeElement) && document.hasFocus()), 'Tab from browser chrome must return focus to the dialog.');
      chromeFocusTransitions.push({ ...focus, returned_to_dialog: true });
    }
  }
  if (checkRefreshFocus) {
    const close = modal.getByRole('button', { name: 'Close dialog', exact: true });
    await close.focus();
    // The production controller refreshes every five seconds. Observe one real
    // refresh period; no clock, controller or host fixture is substituted here.
    await page.waitForTimeout(5500);
    assert(await close.evaluate(element => document.activeElement === element), 'Periodic refresh must preserve the focused dialog control.');
  }
  observations.push({ stage, engine: page.context().browser().browserType().name(), dialog: name, ...geometry, chrome_focus_transitions: chromeFocusTransitions, refresh_focus_checked: checkRefreshFocus });
}
async function pickerChecks(page, title) {
  const trigger = page.getByRole('button', { name: 'Current workspace: ' + title, exact: true });
  await trigger.click(); await modalChecks(page, 'Switch workspace', true);
  await page.keyboard.press('Escape');
  await page.getByRole('dialog', { name: 'Switch workspace', exact: true }).waitFor({ state: 'hidden' });
  assert(await trigger.evaluate(element => document.activeElement === element), 'Escape must restore focus to the workspace control.');
  await trigger.click();
  await page.getByRole('dialog', { name: 'Switch workspace', exact: true }).waitFor();
  await page.goBack();
  await page.getByRole('dialog', { name: 'Switch workspace', exact: true }).waitFor({ state: 'hidden' });
  assert(await trigger.evaluate(element => document.activeElement === element), 'Back must close the picker and restore focus.');
  record('Picker is on demand; its top-layer modal covers the viewport, contains focus, and closes with Escape and Back.');
}
async function captureChecks(peer, originalTitle) {
  const { page } = peer;
  const entry = () => page.getByRole('button', { name: 'Type instead', exact: true });
  const capture = page.getByRole('dialog', { name: 'Capture', exact: true });
  await entry().click(); await modalChecks(page, 'Capture');
  const field = page.getByLabel('Your words', { exact: true });
  const submit = capture.locator('#run-command');
  const unsupported = 'Open absent acceptance application';
  await field.fill(unsupported);
  assert(await submit.isDisabled(), 'An unavailable application must not manufacture an executable command.');
  await capture.getByRole('button', { name: 'Close dialog', exact: true }).click();
  await capture.waitFor({ state: 'hidden' });
  assert(await entry().evaluate(element => document.activeElement === element), 'Closing capture must restore focus to its dock control.');
  await entry().click(); assert.equal(await field.inputValue(), unsupported, 'Dismissal must preserve captured text.');
  const body = 'Acceptance capture remains in its original workspace.';
  const command = body;
  await field.fill(command);
  await page.keyboard.press('Escape'); await capture.waitFor({ state: 'hidden' });
  await page.getByRole('button', { name: /^Current workspace:/ }).click();
  await page.getByRole('dialog', { name: 'Switch workspace', exact: true }).getByRole('button', { name: 'Create workspace', exact: true }).click();
  const otherTitle = 'Other acceptance work';
  await page.getByLabel('Workspace name', { exact: true }).fill(otherTitle);
  await page.getByRole('dialog', { name: 'Create workspace', exact: true }).getByRole('button', { name: 'Create workspace', exact: true }).click();
  await currentWork(page, otherTitle, originalTitle);
  await entry().click();
  assert.equal(await field.inputValue(), command, 'Navigation must retain the addressed draft.');
  assert(await submit.isDisabled(), 'A draft from another workspace must not execute in the current workspace.');
  await capture.getByRole('button', { name: 'Return to ' + originalTitle, exact: true }).click();
  assert.equal(await field.inputValue(), command);
  assert(!await submit.isDisabled(), 'Returning to the addressed workspace must permit saving the note.');
  assert.equal(await submit.innerText(), 'Save as note', 'Free input must visibly propose a note without requiring a command prefix.');
  await submit.click();
  await capture.waitFor({ state: 'hidden' });
  await currentWork(page, originalTitle, otherTitle);
  const allWork = page.getByRole('button', { name: /All work/ });
  if (await allWork.isVisible()) await allWork.click();
  await note(page, body);
  await navigate(page, otherTitle);
  assert.equal(await page.getByText(body, { exact: true }).filter({ visible: true }).count(), 0, 'The capture must not appear in the workspace visited afterward.');
  await navigate(page, originalTitle);
  await page.getByRole('button', { name: 'Share this workspace', exact: true }).click();
  const sharing = page.getByRole('dialog', { name: 'Share workspace', exact: true });
  assert(await sharing.getByRole('checkbox', { name: originalTitle, exact: true }).isChecked(), 'Sharing must select the current workspace.');
  assert(!await sharing.getByRole('checkbox', { name: otherTitle, exact: true }).isChecked(), 'Sharing must not silently include other workspaces.');
  await sharing.getByRole('button', { name: 'Close dialog', exact: true }).click();
  await sharing.waitFor({ state: 'hidden' });
  record('Typed capture survives dismissal and workspace changes, executes only at its original address, and sharing retains explicit scope.');
  if (peer.mobile) {
    await page.setViewportSize({ width: 390, height: 460 });
    await entry().click(); await modalChecks(page, 'Capture');
    assert(await field.evaluate(element => Number.parseFloat(getComputedStyle(element).fontSize) >= 16), 'Phone text entry must not trigger small-font input zoom.');
    await field.fill('Remember a draft in a reduced viewport');
    await capture.getByRole('button', { name: 'Close dialog', exact: true }).click();
    await capture.waitFor({ state: 'hidden' });
    await page.setViewportSize({ width: 844, height: 390 });
    await currentWork(page, originalTitle, otherTitle);
    await entry().click(); await modalChecks(page, 'Capture');
    await page.keyboard.press('Escape'); await capture.waitFor({ state: 'hidden' });
    await page.setViewportSize({ width: 390, height: 900 });
    record('Narrow, reduced-height and landscape layouts keep work and capture dialogs reachable; this is not physical keyboard evidence.');
  }
}
async function settingsChecks(page, workspaceTitle) {
  const gear = page.getByRole('button', { name: 'Settings', exact: true });
  await gear.waitFor();
  assert.equal(await gear.getAttribute('id'), 'workspace-settings', 'Settings must have its dedicated top-level control.');
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  const content = page.getByLabel('Content', { exact: true });
  const draft = 'Unsaved draft retained while Settings adapts to the window.';
  await content.fill(draft);
  await gear.click();
  const settings = page.getByRole('dialog', { name: 'Settings', exact: true });
  await settings.getByRole('button', { name: 'Appearance', exact: true }).waitFor();
  assert(await settings.getByRole('button', { name: 'Connections', exact: true }).isVisible());
  assert(await settings.getByRole('button', { name: 'Install and storage', exact: true }).isVisible());
  const sizes = [{ width: 1024, height: 768 }, { width: 540, height: 720 }, { width: 390, height: 844 }];
  for (const size of sizes) {
    const focusedControl = settings.getByRole('button', { name: 'Appearance', exact: true });
    await focusedControl.focus();
    await page.setViewportSize(size);
    assert(await focusedControl.evaluate(element => document.activeElement === element), 'Resizing must preserve the active Settings control.');
    assert.equal(await content.inputValue(), draft, 'Resizing an open Settings dialog must retain the underlying unsaved draft.');
    assert.equal(await page.locator('#workspace-picker').getAttribute('aria-label'), 'Current workspace: ' + workspaceTitle, 'Resizing Settings must preserve workspace context.');
    await modalChecks(page, 'Settings');
    const bounds = await page.locator('#workspace-settings').boundingBox();
    assert(bounds && bounds.width >= 44 && bounds.height >= 44, 'The Settings target must remain at least 44 by 44 pixels.');
    observations.push({ stage, engine: page.context().browser().browserType().name(), settings_viewport: size, draft_retained: true, focused_control_retained: true });
  }
  await page.goBack();
  await settings.waitFor({ state: 'hidden' });
  assert(await gear.evaluate(element => document.activeElement === element), 'Back must close Settings and restore focus to its gear.');
  assert.equal(await content.inputValue(), draft, 'Closing Settings must retain the unsaved edit.');
  await gear.click(); await settings.waitFor();
  await page.keyboard.press('Escape'); await settings.waitFor({ state: 'hidden' });
  assert(await gear.evaluate(element => document.activeElement === element), 'Escape must restore focus to the Settings gear.');
  await page.getByRole('button', { name: 'Discard local edit', exact: true }).click();
  await content.waitFor({ state: 'hidden' });
  await page.getByRole('button', { name: /^Current workspace:/ }).click();
  const picker = page.getByRole('dialog', { name: 'Switch workspace', exact: true });
  assert.equal(await picker.getByRole('button', { name: 'Appearance', exact: true }).count(), 0, 'Workspace switching must not mix in device settings.');
  await picker.getByRole('button', { name: 'Close dialog', exact: true }).click();
  await picker.waitFor({ state: 'hidden' });
  await page.setViewportSize({ width: 390, height: 900 });
  record('Dedicated Settings covers the viewport, restores Back/Escape focus, and retains an unsaved draft across 1024/540/390-pixel windows.');
}
try {
  assert(engines[inviterEngine] && engines[joinerEngine], 'Choose chromium or webkit for each participant.');
  assert(!(dist && args.includes('--url')), 'Choose an existing URL or a local distribution, not both.');
  if (dist) { servedAssets = await hashes(resolve(dist)); server = await startStaticServer(resolve(dist)); target = server.url; }
  const destination = new URL(target);
  assert(!destination.hash && !destination.search && !destination.username && !destination.password, 'The target must be a base app URL without credentials, query, or fragment.');
  profiles = await mkdtemp(join(tmpdir(), 'workspace-public-smoke-'));
  const inviter = await start('inviter', inviterEngine, false);
  stage = 'inviter-startup';
  await ready(inviter.page, target, 'inviter');
  assert(await inviter.page.getByRole('button', { name: 'Join existing work', exact: true }).isVisible(), 'Fresh users must have a visible Join path.');
  stage = 'create-workspace';
  const title = `Join diagnosis ${Date.now()}`;
  await inviter.page.getByLabel('Workspace name', { exact: true }).fill(title);
  await inviter.page.getByRole('button', { name: 'Create workspace', exact: true }).click();
  await currentWork(inviter.page, title);
  record('Fresh startup provides Create and Join; created work opens without a mode choice or persistent workspace navigation.');
  stage = 'create-note';
  const initialContent = 'Public smoke: initial note retained.';
  const joinerContent = 'Public smoke: revision from the joining device.';
  const inviterContent = 'Public smoke: revision returned by the inviting device.';
  await inviter.page.getByRole('button', { name: 'Add to workspace', exact: true }).click();
  await inviter.page.getByRole('dialog', { name: 'Add to workspace', exact: true }).locator('[data-action="new-note"]').click();
  const noteTitle = 'Public smoke note';
  await inviter.page.getByLabel('Title', { exact: true }).fill(noteTitle);
  await inviter.page.getByLabel('Content', { exact: true }).fill(initialContent);
  await inviter.page.getByRole('button', { name: 'Save note', exact: true }).click();
  await inviter.page.getByLabel('Content', { exact: true }).waitFor({ state: 'hidden' });
  await note(inviter.page, initialContent);
  await inviter.page.getByRole('button', { name: 'Open ' + noteTitle, exact: true }).click();
  await inviter.page.getByRole('button', { name: 'Edit', exact: true }).waitFor();
  await pickerChecks(inviter.page, title);
  stage = 'create-invitation';
  await inviter.page.getByRole('button', { name: 'Share this workspace' }).click();
  await modalChecks(inviter.page, 'Share workspace');
  assert(await inviter.page.getByRole('dialog', { name: 'Share workspace', exact: true }).getByRole('checkbox', { name: title, exact: true }).isChecked(), 'Share must explicitly select the intended workspace.');
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
  await modalChecks(joiner.page, 'Join workspaces');
  assert(new URL(joiner.page.url()).hash === '', 'The invitation fragment must be removed after startup.');
  await joiner.page.getByLabel('Invitation to join', { exact: true }).waitFor({ state: 'attached' });
  const loadedInvitation = await joiner.page.getByLabel('Invitation to join', { exact: true }).inputValue();
  assert(loadedInvitation === ticket || loadedInvitation === encoded, 'Displayed invitation must retain the same token');
  assert.equal(await joiner.page.getByRole('button', { name: 'Current workspace: ' + title, exact: true }).count(), 0, 'Opening an invitation must not automatically join.');
  observations.push({ stage, ticket_preserved: true, inviter_visible: await inviter.page.evaluate(() => document.visibilityState) });
  stage = 'join-selected-workspaces';
  const started = Date.now();
  assert(await inviter.page.evaluate(() => document.visibilityState === 'visible'), 'The inviting page must stay visible during Join.');
  await joiner.page.getByRole('button', { name: 'Join selected workspaces', exact: true }).click();
  try {
    await joiner.page.getByRole('button', { name: 'Current workspace: ' + title, exact: true }).waitFor({ timeout: 45_000 });
    await joiner.page.getByRole('dialog', { name: 'Join workspaces', exact: true }).waitFor({ state: 'hidden' });
    joined = true;
  } finally {
    joinStatus = (await joiner.page.locator('.status-message').allTextContents()).map(scrub);
    observations.push({ stage, elapsed_ms: Date.now() - started, joined, inviter_visible: await inviter.page.evaluate(() => document.visibilityState) });
  }
  assert(joined, 'Join must import the selected workspace before timing out');
  stage = 'verify-imported-note';
  await currentWork(joiner.page, title);
  await note(joiner.page, initialContent);
  transfers.initial_note = true;
  record('Explicit Join opens the single newly received workspace and its actual note without extra navigation.');
  await openNote(joiner.page, noteTitle);
  await pickerChecks(joiner.page, title);
  stage = 'joiner-edit-transfer';
  await edit(joiner.page, joinerContent);
  await inviter.page.getByRole('dialog', { name: 'Share workspace', exact: true }).getByRole('button', { name: 'Close dialog', exact: true }).click();
  await inviter.page.getByRole('dialog', { name: 'Share workspace', exact: true }).waitFor({ state: 'hidden' });
  await note(inviter.page, joinerContent);
  transfers.joiner_edit = true;
  stage = 'inviter-edit-transfer';
  await edit(inviter.page, inviterContent);
  await note(joiner.page, inviterContent);
  transfers.inviter_edit = true;
  record('Direct reader edits save through durable host receipts and converge over real Iroh in both directions.');
  stage = 'capture-and-mobile-journeys';
  await captureChecks(joiner, title);
  stage = 'returning-user';
  await joiner.page.reload({ waitUntil: 'domcontentloaded' });
  await currentWork(joiner.page, title, 'Other acceptance work');
  await note(joiner.page, inviterContent);
  await openNote(joiner.page, noteTitle);
  await note(joiner.page, inviterContent);
  record('Reload resumes the current workspace and retained note, ready for direct reading and editing.');
  stage = 'settings-and-draft-resize';
  await settingsChecks(joiner.page, title);
  if (dist) assert.deepEqual(await hashes(resolve(dist)), servedAssets, 'Served build files must remain unchanged during acceptance.');
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
  let serverClosed = !server;
  try { if (server) { await server.close(); serverClosed = true; } }
  catch { serverClosed = false; }
  if (!contextsClosed || !profilesRemoved || !serverClosed) failure ??= { stage: 'cleanup', message: 'Temporary browser cleanup did not complete.' };
  const report = {
    passed: !failure, target: (() => { try { const url = new URL(target); return url.origin + url.pathname; } catch { return '[invalid target]'; } })(), timestamp: new Date().toISOString(), observations,
    joined, transfers, assertions, join_status: joinStatus, relay_routes: routes,
    harness_sha256: createHash('sha256').update(await readFile(new URL(import.meta.url))).digest('hex'),
    ...(servedAssets ? { served_assets: servedAssets } : {}),
    cleanup: { contexts_closed: contextsClosed, profiles_removed: profilesRemoved, static_server_closed: serverClosed },
    scope: 'Served production UI, separate browser engines and fresh temporary persistent profiles, real Iroh pairing and bidirectional note edits, current-work/modal/capture journeys; Linux WebKit with resized viewports is not physical iPhone touch, keyboard, or microphone evidence. No screenshots, traces, profiles, databases, seeds, or invitation values are exported.',
    ...(failure ? { failure } : {}),
  };
  await mkdir(evidence, { recursive: true });
  await writeFile(resolve(evidence, 'report.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
  if (failure) process.exitCode = 1;
}
