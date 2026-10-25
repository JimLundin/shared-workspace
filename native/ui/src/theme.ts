/** Appearance belongs to this display; it never travels with workspace records. */
export type Appearance = 'system' | 'light' | 'dark';
export type Theme = Exclude<Appearance, 'system'>;
const key = 'workspace.appearance.v1';
export function readAppearance(value: string | null): Appearance {
  return value === 'light' || value === 'dark' ? value : 'system';
}
export function resolveTheme(preference: Appearance, darkSystem: boolean): Theme {
  return preference === 'system' ? darkSystem ? 'dark' : 'light' : preference;
}
export function observeAppearance(onChange: (appearance: Appearance) => void): { get(): Appearance; set(value: Appearance): void; dispose(): void } {
  const query = window.matchMedia('(prefers-color-scheme: dark)');
  let appearance: Appearance = 'system';
  try { appearance = readAppearance(localStorage.getItem(key)); } catch { /* System appearance remains available. */ }
  function apply(notify = true): void {
    const theme = resolveTheme(appearance, query.matches);
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name=theme-color]')?.setAttribute('content', theme === 'dark' ? '#17181a' : '#f5f5f5');
    if (notify) onChange(appearance);
  }
  const systemChanged = (): void => { if (appearance === 'system') apply(); };
  query.addEventListener('change', systemChanged);
  apply(false);
  return { get: () => appearance, set(value) { appearance = value; try { localStorage.setItem(key, value); } catch { /* Current session still uses the selection. */ } apply(); }, dispose() { query.removeEventListener('change', systemChanged); } };
}
