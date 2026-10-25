import './style.css';
import { mountApp } from './app.ts';
import { NativeHost } from './host.ts';
import { NativeVoice } from './voice.ts';
import { NativeScanner } from './scanner.ts';
const root = document.getElementById('app');
if (!root) throw new Error('Workspace mount point is missing.');
const app = mountApp(root, { host: new NativeHost(), voice: new NativeVoice(), scanner: new NativeScanner() });
window.addEventListener('pagehide', () => app.dispose(), { once: true });
