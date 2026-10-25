import { test, expect } from '@playwright/test';

test('code editor retains selection and focus across language, theme and observed text updates', async ({ page }) => {
  await page.goto('/tests/code-editor.html');
  const content = page.getByRole('textbox', { name: 'Code content' });
  await content.click();
  await page.keyboard.press('Control+Home');
  for (let index = 0; index < 6; index++) await page.keyboard.press('ArrowRight');
  for (let index = 0; index < 6; index++) await page.keyboard.press('Shift+ArrowRight');
  await expect.poll(() => page.evaluate(() => window.getSelection()?.toString())).toBe('answer');
  expect(await page.evaluate(() => window.codeEditorFixture.selection())).toEqual({ from: 6, to: 12 });
  const color = await content.locator('.cm-line').first().locator('span').first().evaluate(element => getComputedStyle(element).color);
  await page.evaluate(() => {
    window.codeEditorFixture.setLanguage('typescript');
    document.documentElement.dataset.theme = 'dark';
    window.codeEditorFixture.setTheme('dark');
  });
  await expect(content).toBeFocused();
  await expect.poll(() => page.evaluate(() => window.getSelection()?.toString())).toBe('answer');
  await expect.poll(() => content.locator('.cm-line').first().locator('span').first().evaluate(element => getComputedStyle(element).color)).not.toBe(color);
  await page.evaluate(() => window.codeEditorFixture.setText('// observed revision\n' + window.codeEditorFixture.read()));
  await expect(content).toBeFocused();
  await expect.poll(() => page.evaluate(() => window.getSelection()?.toString())).toBe('answer');
  expect(await page.evaluate(() => window.codeEditorFixture.selection())).toEqual({ from: 27, to: 33 });
  expect(await page.evaluate(() => window.codeEditorFixture.changes())).toEqual([]);
  await page.keyboard.type('result');
  await expect.poll(() => page.evaluate(() => window.codeEditorFixture.read())).toContain('const result = 42;');
  expect((await page.evaluate(() => window.codeEditorFixture.changes())).length).toBeGreaterThan(0);
  await page.keyboard.press('Control+z');
  await expect.poll(() => page.evaluate(() => window.codeEditorFixture.read())).toBe('// observed revision\nconst answer = 42;\nconsole.log(answer);\n');
});

test('readonly stops editing commands, Tab exits, and disposal releases the editor', async ({ page }) => {
  await page.goto('/tests/code-editor.html');
  const content = page.getByRole('textbox', { name: 'Code content' });
  await content.click();
  await page.keyboard.press('Control+Home');
  await page.keyboard.type('// local');
  const before = await page.evaluate(() => window.codeEditorFixture.read());
  await page.evaluate(() => window.codeEditorFixture.setReadonly(true));
  await expect(content).toBeFocused();
  await expect(content).toHaveAttribute('aria-readonly', 'true');
  await page.keyboard.press('Control+z');
  await page.keyboard.press('Control+]');
  await page.keyboard.press('Backspace');
  await page.keyboard.press('Enter');
  expect(await page.evaluate(() => window.codeEditorFixture.read())).toBe(before);
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'After editor' })).toBeFocused();
  await page.evaluate(() => { window.codeEditorFixture.setReadonly(false); window.codeEditorFixture.focus(); });
  await page.keyboard.press('Control+z');
  await expect.poll(() => page.evaluate(() => window.codeEditorFixture.read())).toBe('const answer = 42;\nconsole.log(answer);\n');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'After editor' })).toBeFocused();
  const changes = await page.evaluate(() => window.codeEditorFixture.changes());
  await page.evaluate(() => { window.codeEditorFixture.dispose(); window.codeEditorFixture.dispose(); window.codeEditorFixture.setText('late write'); });
  await expect(page.locator('.cm-editor')).toHaveCount(0);
  expect(await page.evaluate(() => window.codeEditorFixture.changes())).toEqual(changes);
});
