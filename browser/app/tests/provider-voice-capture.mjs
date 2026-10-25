import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium, webkit } from 'playwright';
import { createServer } from 'vite';

// Real MediaRecorder: Chromium synthetic microphone, or WebKit synthetic Web Audio stream.
const safari = process.argv.includes('--webkit');
const cors = process.argv.includes('--openai-cors');
const server = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false, appType: 'custom', cacheDir: '/tmp/provider-voice-vite-cache', server: { host: '127.0.0.1', port: 0,
  headers: { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' },
} });
server.middlewares.use((request, response, next) => {
  if (request.url !== '/__capture-test') return next();
  response.setHeader('Content-Type', 'text/html');
  response.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  response.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
  response.end('<!doctype html><html lang="en"><button id="record">Record</button></html>');
});
let browser;
try {
  await server.listen();
  const address = server.httpServer.address();
  assert.ok(address && typeof address !== 'string');
  const origin = `http://127.0.0.1:${address.port}`;
  browser = safari ? await webkit.launch({ headless: !process.argv.includes('--headed') })
    : await chromium.launch({ headless: true, args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] });
  const context = await browser.newContext(safari ? {} : { permissions: ['microphone'] });
  const page = await context.newPage();
  const errors = []; const externalRequests = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('request', request => { if (/^https?:/.test(request.url()) && !request.url().startsWith(origin)) externalRequests.push(request.url()); });
  await page.goto(`${origin}/__capture-test`);
  const capabilities = await page.evaluate(() => ({
    media_recorder: typeof MediaRecorder === 'function',
    audio_context: typeof AudioContext === 'function',
    cross_origin_isolated: crossOriginIsolated,
  }));
  if (!capabilities.media_recorder) {
    const report = { verified_at: new Date().toISOString(), engine: safari ? 'WebKit' : 'Chromium', browser: browser.version(),
      mode: process.argv.includes('--headed') ? 'headed' : 'headless', status: 'unavailable', ...capabilities,
      conclusion: 'This browser build exposes no MediaRecorder. No encoded-audio or provider request assertion was established.' };
    await writeFile(`${process.env.PROVIDER_VOICE_EVIDENCE_DIR ?? '/tmp'}/provider-voice-capture-${safari ? 'webkit' : 'chromium'}-evidence.json`, JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify(report, null, 2));
    throw new Error('MediaRecorder is unavailable in this browser build.');
  }
  await page.evaluate(async ({ safari }) => {
    const { ProviderVoice } = await import('/src/provider-voice.ts');
    const events = [], uploads = [], streams = [], audioContexts = [];
    const getMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    if (safari) {
      const supports = MediaRecorder.isTypeSupported.bind(MediaRecorder);
      // Exercise the native MP4 encoder branch even if this WebKit also supports WebM.
      MediaRecorder.isTypeSupported = type => type.startsWith('audio/mp4') && supports(type);
    }
    navigator.mediaDevices.getUserMedia = async constraints => {
      let stream;
      if (safari) {
        const audio = new AudioContext(); audioContexts.push(audio);
        const destination = audio.createMediaStreamDestination();
        const oscillator = audio.createOscillator(); oscillator.frequency.value = 440;
        oscillator.connect(destination); oscillator.start(); await audio.resume();
        stream = destination.stream;
      } else stream = await getMedia(constraints);
      streams.push(stream); return stream;
    };
    let id = 0;
    const voice = new ProviderVoice({
      captureContext: () => ({ workspaceId: 'synthetic-test', connectionVersion: 'a'.repeat(64) }),
      transcribe: async (audio, options) => {
        window.captureProof.recordedAudio = audio;
        uploads.push({
          bytes: audio.size, mime_type: audio.type,
          leading_bytes: [...new Uint8Array(await audio.arrayBuffer()).slice(0, 12)],
          microphone_released: streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended')),
          captured_workspace: options.context.workspaceId,
        });
        return 'Synthetic microphone capture completed.';
      },
      locale: 'en-US',
    });
    window.captureProof = { voice, events, uploads, streams, audioContexts };
    document.querySelector('#record').onclick = () => { void voice.start(`s${++id}`, event => events.push(event)); };
  }, { safari });
  await page.click('#record');
  await page.waitForFunction(() => window.captureProof.events.some(event => event.type === 'listening'));
  await page.waitForTimeout(700);
  await page.evaluate(() => window.captureProof.voice.stop('s1'));
  await page.waitForFunction(() => window.captureProof.events.some(event => event.type === 'stopped'));
  await page.click('#record');
  await page.waitForFunction(() => window.captureProof.events.some(event => event.type === 'listening' && event.session_id === 's2'));
  await page.evaluate(() => window.captureProof.voice.cancel('s2'));
  await page.click('#record');
  await page.waitForFunction(() => window.captureProof.events.some(event => event.type === 'listening' && event.session_id === 's3'));
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  const observed = await page.evaluate(() => ({
    events: window.captureProof.events,
    uploads: window.captureProof.uploads,
    all_tracks_released: window.captureProof.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended')),
    cross_origin_isolated: crossOriginIsolated,
  }));
  assert.deepEqual(errors, []); assert.deepEqual(externalRequests, []);
  assert.equal(observed.uploads.length, 1);
  assert.ok(observed.uploads[0].bytes > 500);
  assert.match(observed.uploads[0].mime_type, safari ? /^audio\/mp4/ : /^audio\/webm/);
  if (safari) assert.equal(String.fromCharCode(...observed.uploads[0].leading_bytes.slice(4, 8)), 'ftyp');
  else assert.deepEqual(observed.uploads[0].leading_bytes.slice(0, 4), [0x1a, 0x45, 0xdf, 0xa3]);
  assert.equal(observed.uploads[0].microphone_released, true);
  assert.equal(observed.all_tracks_released, true);
  assert.equal(observed.cross_origin_isolated, true);
  assert.ok(observed.events.some(event => event.type === 'cancelled' && event.session_id === 's2'));
  assert.ok(observed.events.some(event => event.type === 'error' && event.session_id === 's3'));
  let corsProof = null;
  if (cors) {
    corsProof = await page.evaluate(async () => {
      const audio = window.captureProof.recordedAudio;
      const form = new FormData();
      form.set('file', audio, audio.type.startsWith('audio/mp4') ? 'synthetic-capture.mp4' : 'synthetic-capture.webm');
      form.set('model', 'gpt-4o-transcribe'); form.set('response_format', 'json');
      const response = await fetch('https://api.openai.com/v1/audio/transcriptions', {
        method: 'POST', headers: { Authorization: 'Bearer invalid-workspace-transcription-smoke' },
        body: form, mode: 'cors', credentials: 'omit', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer',
        signal: AbortSignal.timeout(30_000),
      });
      const body = await response.json();
      return { status: response.status, response_type: response.type, error_type: body.error?.type, error_code: body.error?.code,
        synthetic_invalid_credential: true, conclusion: 'Browser multipart request reached OpenAI and its authentication error was readable; no paid transcription success.' };
    });
    assert.equal(corsProof.status, 401); assert.equal(corsProof.response_type, 'cors');
  }
  await page.evaluate(() => Promise.all(window.captureProof.audioContexts.map(audio => audio.close())));
  const report = {
    verified_at: new Date().toISOString(), browser: browser.version(), engine: safari ? 'WebKit' : 'Chromium',
    input: safari ? 'Web Audio oscillator→MediaStreamDestination injected for getUserMedia; actual MP4 MediaRecorder encoder'
      : 'Chromium synthetic microphone through real getUserMedia and MediaRecorder',
    provider: 'Injected transcription fixture; optional separate CORS smoke uses synthetic invalid credentials only',
    ...observed, errors, external_requests: externalRequests, cors: corsProof,
  };
  await writeFile(`${process.env.PROVIDER_VOICE_EVIDENCE_DIR ?? '/tmp'}/provider-voice-capture-${safari ? 'webkit' : 'chromium'}-evidence.json`, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
} finally { await browser?.close(); await server.close(); }
