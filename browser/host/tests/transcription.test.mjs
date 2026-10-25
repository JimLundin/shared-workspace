import assert from 'node:assert/strict';
import { test } from 'node:test';
import { transcribe } from '../src/transcription.ts';
import { connectionError } from '../src/connections.ts';
const synthetic = 'SYNTHETIC_ONLY_PROVIDER_CREDENTIAL';
const connection = { type: 'set', api_key: synthetic, model: 'gpt-4o-transcribe' };
const input = () => ({ workspace_id: 'work', connection_version: 'aa'.repeat(32), audio: new Blob(['synthetic audio bytes'], { type: 'audio/mp4' }), locale: '' });
const signal = () => new AbortController().signal;
const success = () => new Response(JSON.stringify({ text: 'Create a note about the meeting.' }), { headers: { 'Content-Type': 'application/json' } });
test('audio uploads only to fixed OpenAI endpoint with selected workspace version and no ambient browser credentials', async () => {
  const resolved = [], requests = [];
  const result = await transcribe(input(), async (...args) => { resolved.push(args); return connection; }, signal(), async (url, options) => { requests.push({ url, options }); return success(); });
  assert.equal(result, 'Create a note about the meeting.');
  assert.deepEqual(resolved, [['work', 'aa'.repeat(32)], ['work', 'aa'.repeat(32)]]);
  assert.equal(requests.length, 1);
  const { url, options } = requests[0];
  assert.equal(url, 'https://api.openai.com/v1/audio/transcriptions', 'PROVIDER_ENDPOINT_CHANGED');
  assert.equal(options.credentials, 'omit', 'AMBIENT_CREDENTIALS_INCLUDED');
  assert.equal(options.cache, 'no-store', 'PROVIDER_RESPONSE_CACHEABLE');
  assert.equal(options.redirect, 'error', 'CREDENTIAL_REDIRECT_ALLOWED');
  assert.equal(options.referrerPolicy, 'no-referrer');
  assert.equal(options.headers.Authorization, 'Bearer ' + synthetic);
  assert.equal(options.body.get('model'), 'gpt-4o-transcribe');
  assert.equal(options.body.get('file').type, 'audio/mp4');
  assert.equal(options.body.get('file').name, 'recording.mp4');
  assert.equal(options.body.has('language'), false, 'AUTO_DETECTION_REPLACED');
});
test('stale captured credentials refuse audio before any network request', async () => {
  let uploads = 0;
  await assert.rejects(transcribe(input(), async () => { throw connectionError('The workspace connection changed.'); }, signal(), async () => { uploads++; return success(); }), { message: 'The workspace connection changed.' });
  assert.equal(uploads, 0, 'STALE_AUDIO_UPLOADED');
});
test('changed credentials during upload refuse stale transcription publication', async () => {
  let calls = 0;
  await assert.rejects(transcribe(input(), async () => { if (++calls > 1) throw connectionError('The workspace connection changed.'); return connection; }, signal(), async () => success()), { message: 'The workspace connection changed.' }, 'STALE_TRANSCRIPT_PUBLISHED');
});
test('provider error bodies and network exceptions cannot leak credentials', async () => {
  for (const request of [async () => new Response(synthetic, { status: 401 }), async () => { throw new Error('Authorization: ' + synthetic); }, async () => new Response('{\"text\":' + synthetic)]) {
    await assert.rejects(transcribe(input(), async () => connection, signal(), request), error => !String(error).includes(synthetic), 'PROVIDER_SECRET_REFLECTED');
  }
});
test('a provider success body cannot reflect the workspace key into generic voice history', async () => {
  await assert.rejects(transcribe(input(), async () => connection, signal(), async () => new Response(JSON.stringify({ text: synthetic }))), undefined, 'KEY_REFLECTED_AS_TRANSCRIPT');
});
test('aborting a recording while credential resolution waits prevents upload', async () => {
  const controller = new AbortController(); let requests = 0;
  await assert.rejects(transcribe(input(), async () => { controller.abort(); return connection; }, controller.signal, async () => { requests++; return success(); }));
  assert.equal(requests, 0, 'CANCELLED_AUDIO_UPLOADED');
});
test('aborting an active upload reaches fetch and returns only a safe cancellation', async () => {
  const controller = new AbortController(); let started;
  const entered = new Promise(resolve => { started = resolve; });
  const running = transcribe(input(), async () => connection, controller.signal, async (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error(synthetic)), { once: true }); started();
  }));
  await entered; controller.abort();
  await assert.rejects(running, { message: 'Transcription was cancelled or exceeded its time limit.' });
});
test('malformed audio and ambient endpoint fields are rejected before key lookup', async () => {
  let lookups = 0;
  for (const value of [{ ...input(), endpoint: 'https://untrusted.invalid/' }, { ...input(), audio: new Blob([]) }, { ...input(), audio: new Blob(['x'], { type: 'audio/ogg' }) }, { ...input(), locale: '../../../unsafe' }]) {
    await assert.rejects(transcribe(value, async () => { lookups++; return connection; }, signal(), async () => success()));
  }
  assert.equal(lookups, 0);
});
