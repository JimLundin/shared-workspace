import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
import { generateWorker } from './scripts/service-worker.mjs';

const directory = fileURLToPath(new URL('.', import.meta.url));
export default defineConfig(({ command }) => ({
  base: process.env.WORKSPACE_BASE ?? (command === 'serve' ? '/' : '/shared-workspace/'),
  resolve: { alias: { 'qrcode-generator': fileURLToPath(new URL('./node_modules/qrcode-generator/dist/qrcode.mjs', import.meta.url)) } },
  plugins: [{ name: 'workspace-offline-shell', async closeBundle() { await generateWorker(directory + 'dist'); } }],
  server: { host: '127.0.0.1', fs: { allow: [fileURLToPath(new URL('../../', import.meta.url))] }, headers: {
    'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp',
  } },
  worker: { format: 'es' },
  optimizeDeps: { exclude: ['@tursodatabase/database-wasm', '@shared-workspace/browser-host'] },
  build: { target: 'esnext', sourcemap: false },
}));
