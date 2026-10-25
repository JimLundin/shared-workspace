import type { Route, View } from './session.ts';
/** Only view arrangement is stored here. Shared records and tickets stay out. */
export type Presentation = Readonly<{ route: Route; views: Readonly<Record<string, View>> }>;
export function readPresentation(value: string | null): Presentation | null {
  if (!value) return null;
  try {
    const data: unknown = JSON.parse(value);
    if (!data || typeof data !== 'object' || !('version' in data) || data.version !== 1 || !('route' in data) || !('views' in data)) return null;
    const route = data.route;
    if (!route || typeof route !== 'object' || !('type' in route)) return null;
    const routeValue: Route = route.type === 'workspace' && 'id' in route && typeof route.id === 'string' ? { type: 'workspace', id: route.id } : route.type === 'home' || route.type === 'devices' ? { type: route.type } : { type: 'home' };
    if (!data.views || typeof data.views !== 'object' || Array.isArray(data.views)) return null;
    const views: Record<string, View> = {};
    for (const [id, value] of Object.entries(data.views)) {
      if (!value || typeof value !== 'object') continue;
      const v = value as Record<string, unknown>;
      if (!['canvas','focus','overview'].includes(String(v.mode)) || !(v.focused === null || typeof v.focused === 'string') || !Array.isArray(v.closed) || !v.closed.every(x => typeof x === 'string') || !Array.isArray(v.order) || !v.order.every(x => typeof x === 'string')) continue;
      views[id] = { mode: v.mode as View['mode'], focused: v.focused, closed: v.closed, order: v.order };
    }
    return { route: routeValue, views };
  } catch { return null; }
}
export function savePresentation(deviceId: string, presentation: Presentation): void { try { localStorage.setItem('workspace.presentation.v1:'+deviceId, JSON.stringify({ version:1, ...presentation })); } catch { /* View preferences may be unavailable; native work remains authoritative. */ } }
export function loadPresentation(deviceId: string): Presentation | null { try { return readPresentation(localStorage.getItem('workspace.presentation.v1:'+deviceId)); } catch { return null; } }
