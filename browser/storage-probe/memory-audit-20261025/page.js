const worker = new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });
const pending = new Map();
let id = 0;
worker.onmessage = ({ data }) => {
  const waiter = pending.get(data.id);
  if (!waiter) return;
  pending.delete(data.id);
  if (data.error) waiter.reject(new Error(data.error));
  else waiter.resolve(data.value);
};
worker.onerror = (error) => {
  for (const waiter of pending.values()) waiter.reject(new Error(error.message));
  pending.clear();
};
window.auditCall = (method, value) => new Promise((resolve, reject) => {
  const token = ++id;
  pending.set(token, { resolve, reject });
  worker.postMessage({ id: token, method, value });
});
window.auditReady = window.auditCall('open', new URLSearchParams(location.search).get('engine') ?? 'empty');
window.auditReady.then(() => document.querySelector('#status').textContent = 'Ready', error => document.querySelector('#status').textContent = String(error));
