const worker = new Worker(new URL('./work-compatibility-worker.ts', import.meta.url), { type: 'module' });
const pending = new Map<string, { resolve: (value: unknown) => void; reject: (error: Error) => void }>();
const phases: string[] = [];
let workerFailure: Error | undefined;
worker.addEventListener('message', event => {
  if (typeof event.data.phase === 'string') { phases.push(event.data.phase); return; }
  const entry = pending.get(event.data.id); if (!entry) return;
  pending.delete(event.data.id);
  if (event.data.ok) entry.resolve(event.data.value); else entry.reject(new Error(event.data.error));
});
worker.addEventListener('error', event => { workerFailure = new Error('Compatibility fixture worker failed: ' + event.message); for (const entry of pending.values()) entry.reject(workerFailure); pending.clear(); });
const methods = {
  diagnostics() { return { phases: [...phases], worker_error: workerFailure?.message ?? null }; },
  call(method: string, value?: unknown): Promise<unknown> {
    if (workerFailure) return Promise.reject(workerFailure);
    const id = crypto.randomUUID();
    return new Promise((resolve, reject) => { pending.set(id, { resolve, reject }); worker.postMessage({ id, method, value }); });
  },
};
declare global { interface Window { workspaceCompatibilityCheck: typeof methods } }
window.workspaceCompatibilityCheck = methods;
export {};
