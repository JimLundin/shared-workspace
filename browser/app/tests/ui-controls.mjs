/** Reach the production controls through their visible desktop or compact layout. */
export async function closePanel(page) {
  const close = page.getByRole('button', { name: 'Close dialog', exact: true });
  if (await close.isVisible()) {
    await close.click();
    await page.locator('dialog[open]').waitFor({ state: 'hidden' });
  }
}

async function navigation(page) {
  await closePanel(page);
  const sidebar = page.locator('#workspace-navigation');
  if (await sidebar.isVisible()) return sidebar;
  const toggle = page.locator('#navigation-toggle');
  const compact = await toggle.getAttribute('aria-label') === 'Open navigation';
  await toggle.click();
  const target = compact ? page.getByRole('dialog', { name: 'Navigation', exact: true }) : sidebar;
  await target.waitFor({ state: 'visible' });
  return target;
}

export async function openWorkspacePicker(page) {
  const controls = await navigation(page);
  await controls.getByRole('button', { name: 'Workspaces', exact: true }).click();
  await page.getByRole('dialog', { name: 'Switch workspace', exact: true }).waitFor();
}

export async function currentWorkspace(page, title) {
  // The selection marker stays in the hidden compact sidebar; interactions use visible controls.
  await page.getByRole('button', { name: 'Current workspace: ' + title, exact: true, includeHidden: true })
    .waitFor({ state: 'attached' });
}

export async function openSettings(page) {
  const controls = await navigation(page);
  await controls.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('dialog', { name: 'Settings', exact: true }).waitFor();
}

export async function pageAction(page, name) {
  await closePanel(page);
  await page.getByRole('button', { name: 'Page actions', exact: true }).click();
  await page.getByRole('dialog', { name: 'Page actions', exact: true })
    .getByRole('button', { name, exact: true }).click();
}

export async function addMaterial(page) {
  await closePanel(page);
  const add = page.getByRole('button', { name: 'Add material', exact: true });
  if (await add.isVisible()) await add.click();
  else await pageAction(page, 'Add to workspace');
  await page.getByRole('dialog', { name: 'Add to workspace', exact: true }).waitFor();
}

export async function shareWorkspace(page) {
  await openWorkspacePicker(page);
  await page.getByRole('dialog', { name: 'Switch workspace', exact: true })
    .getByRole('button', { name: 'Share this workspace', exact: true }).click();
  await page.getByRole('dialog', { name: 'Share workspace', exact: true }).waitFor();
}
