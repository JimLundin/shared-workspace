/** Pure polling decisions; browser effects and their lifetime stay below. */
export const UPDATE_THROTTLE_MS = 60_000;
export const UPDATE_INTERVAL_MS = 5 * 60_000;
export type UpdateAttempt =
  | Readonly<{ type: 'idle'; lastAttemptAt: number | null }>
  | Readonly<{ type: 'checking'; startedAt: number }>
  | Readonly<{ type: 'disposed' }>;
export type UpdateObservation = Readonly<{ now: number; visible: boolean; online: boolean; active: boolean; trigger: 'wake' | 'periodic' }>;
export type UpdatePlan = Readonly<{ type: 'check'; at: number }> | Readonly<{ type: 'wait'; delayMs: number | null }>;
export function planUpdateCheck(attempt: UpdateAttempt, observation: UpdateObservation): UpdatePlan {
  if (attempt.type !== 'idle' || !observation.active || !observation.visible || !observation.online) return { type: 'wait', delayMs: null };
  const due = attempt.lastAttemptAt === null ? observation.now : attempt.lastAttemptAt + (observation.trigger === 'wake' ? UPDATE_THROTTLE_MS : UPDATE_INTERVAL_MS);
  return observation.now >= due ? { type: 'check', at: observation.now } : { type: 'wait', delayMs: due - observation.now };
}
export type UpdateDiscovery = Readonly<{ dispose(): void }>;
export type UpdateEnvironment = Readonly<{
  now(): number;
  visible(): boolean;
  online(): boolean;
  window: EventTarget;
  document: EventTarget;
  setTimer(callback: () => void, delayMs: number): number;
  clearTimer(timer: number): void;
}>;
const browserEnvironment = (): UpdateEnvironment => ({
  now: () => performance.now(), visible: () => !document.hidden, online: () => navigator.onLine,
  window, document, setTimer: (callback, delay) => window.setTimeout(callback, delay), clearTimer: timer => window.clearTimeout(timer),
});

/** No update activation here: successful discovery merely checks waiting state. */
export function discoverUpdates(check: () => Promise<unknown>, checked: () => void, environment: UpdateEnvironment = browserEnvironment()): UpdateDiscovery {
  let attempt: UpdateAttempt = { type: 'idle', lastAttemptAt: null };
  let timer: number | undefined;
  let active = true, wakePending = false;
  function clearTimer(): void { if (timer !== undefined) environment.clearTimer(timer); timer = undefined; }
  function step(trigger: 'wake' | 'periodic'): void {
    if (attempt.type === 'disposed') return;
    clearTimer();
    if (trigger === 'wake') wakePending = true;
    const plan = planUpdateCheck(attempt, { now: environment.now(), visible: environment.visible(), online: environment.online(), active, trigger: wakePending ? 'wake' : 'periodic' });
    if (plan.type === 'wait') {
      if (plan.delayMs !== null) timer = environment.setTimer(() => step('periodic'), plan.delayMs);
      return;
    }
    wakePending = false;
    attempt = { type: 'checking', startedAt: plan.at };
    const settled = () => {
      if (attempt.type === 'disposed') return;
      attempt = { type: 'idle', lastAttemptAt: plan.at };
      try { checked(); } finally { step('periodic'); }
    };
    let request: Promise<unknown>;
    try { request = check(); } catch { request = Promise.reject(); }
    void request.then(settled, settled).catch(() => undefined);
  }
  const wake = () => step('wake');
  const suspend = (event: Event) => {
    if ('persisted' in event && event.persisted === true) { active = false; clearTimer(); }
    else dispose();
  };
  const restore = (event: Event) => {
    if ('persisted' in event && event.persisted === true) { active = true; step('wake'); }
  };
  function dispose(): void {
    attempt = { type: 'disposed' }; clearTimer();
    environment.document.removeEventListener('visibilitychange', wake);
    environment.window.removeEventListener('online', wake);
    environment.window.removeEventListener('offline', wake);
    environment.window.removeEventListener('pagehide', suspend);
    environment.window.removeEventListener('pageshow', restore);
  }
  environment.document.addEventListener('visibilitychange', wake);
  environment.window.addEventListener('online', wake);
  environment.window.addEventListener('offline', wake);
  environment.window.addEventListener('pagehide', suspend);
  environment.window.addEventListener('pageshow', restore);
  step('wake');
  return { dispose };
}
