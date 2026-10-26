import test from 'node:test';
import assert from 'node:assert/strict';
import { ProviderVoice, PROVIDER_VOICE_LIMITS } from './provider-voice.ts';
import type { AudioCapture, CapturePlatform, ProviderTranscribe, ProviderVoiceContext } from './provider-voice.ts';
import type { VoiceEvent } from '../../../native/ui/src/voice-session.ts';

type Handlers = Parameters<AudioCapture['start']>[0];
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };
const context = (): ProviderVoiceContext => ({ workspaceId: 'first', connectionVersion: 'a'.repeat(64) });
class Capture implements AudioCapture {
  mimeType = 'audio/webm;codecs=opus';
  handlers: Handlers | undefined;
  started = false; stopped = 0; released = 0; failStart = false; failStop = false;
  start(handlers: Handlers) {
    this.handlers = handlers;
    if (this.failStart) throw new Error('private recording internals');
    this.started = true; handlers.started();
  }
  stop() { this.stopped++; if (this.failStop) throw new Error('stop failed'); }
  release() { this.released++; }
  data(text: string) { this.handlers?.data(new Blob([text], { type: this.mimeType })); }
  finish(text = 'final audio chunk') { this.data(text); this.handlers?.stopped(); }
}
class Platform implements CapturePlatform {
  enabled = true;
  types = ['audio/webm;codecs=opus'];
  opened: string[] = [];
  captures: Capture[] = [];
  next: Promise<AudioCapture> | undefined;
  timers: { callback: () => void; milliseconds: number; cancelled: boolean }[] = [];
  available() { return this.enabled; }
  supports(type: string) { return this.types.includes(type); }
  async open(type: string) {
    this.opened.push(type);
    if (this.next) return this.next;
    const capture = new Capture(); capture.mimeType = type; this.captures.push(capture); return capture;
  }
  schedule(callback: () => void, milliseconds: number) {
    const timer = { callback, milliseconds, cancelled: false }; this.timers.push(timer);
    return () => { timer.cancelled = true; };
  }
  fire(milliseconds: number) {
    const timer = this.timers.find(item => !item.cancelled && item.milliseconds === milliseconds);
    assert.ok(timer, `Expected active timer of ${milliseconds} ms`);
    timer.cancelled = true; timer.callback();
  }
}
function fixture(transcribe: ProviderTranscribe = async () => 'Recognized words') {
  const platform = new Platform(); const events: VoiceEvent[] = [];
  const voice = new ProviderVoice({ captureContext: context, transcribe, locale: 'en-GB' }, platform);
  const start = (id = 'capture') => voice.start(id, event => events.push(event));
  return { platform, voice, events, start };
}

test('provider capture negotiates Safari MP4 and does not report success before the final upload result', async () => {
  const result = deferred<string>();
  const requests: { audio: Blob; options: Parameters<ProviderTranscribe>[1] }[] = [];
  const f = fixture(async (audio, options) => { requests.push({ audio, options }); return result.promise; });
  f.platform.types = ['audio/mp4'];
  assert.deepEqual(await f.voice.capability(), { type: 'provider_transcription', locale: 'en-GB', provider_label: 'OpenAI' });
  assert.deepEqual(f.platform.opened, []);
  await f.start();
  assert.deepEqual(f.platform.opened, ['audio/mp4']);
  const capture = f.platform.captures[0]!;
  capture.data('first chunk ');
  await f.voice.stop('other'); assert.equal(capture.stopped, 0);
  await f.voice.stop('capture'); await f.voice.stop('capture');
  assert.equal(capture.stopped, 1); assert.ok(capture.released > 0);
  assert.equal(requests.length, 0, 'AUDIO_UPLOADED_BEFORE_FINAL_CHUNK'); assert.equal(f.events.some(event => event.type === 'stopped'), false);
  capture.finish();
  assert.equal(requests.length, 1); assert.equal(requests[0]!.audio.type, 'audio/mp4');
  assert.equal(await requests[0]!.audio.text(), 'first chunk final audio chunk');
  assert.equal(f.events.at(-1)?.type, 'transcribing');
  result.resolve('  Useful transcript  '); await flush();
  assert.deepEqual(f.events.slice(-2), [
    { type: 'transcript', session_id: 'capture', text: 'Useful transcript', is_final: true },
    { type: 'stopped', session_id: 'capture' },
  ]);
  assert.ok(f.platform.timers.every(timer => timer.cancelled));
});

test('workspace and connection are copied before microphone permission resolves', async () => {
  const platform = new Platform(); const pending = deferred<AudioCapture>(); platform.next = pending.promise;
  const snapshot = { workspaceId: 'original', connectionVersion: 'b'.repeat(64) };
  const requests: Parameters<ProviderTranscribe>[1][] = [];
  const voice = new ProviderVoice({ captureContext: () => snapshot, transcribe: async (_audio, options) => { requests.push(options); return 'words'; } }, platform);
  const started = voice.start('s', () => {}, 'sv-SE');
  snapshot.workspaceId = 'different'; snapshot.connectionVersion = 'c'.repeat(64);
  const capture = new Capture(); pending.resolve(capture); await started;
  await voice.stop('s'); capture.finish(); await flush();
  assert.deepEqual(requests[0]?.context, { workspaceId: 'original', connectionVersion: 'b'.repeat(64) }, 'CAPTURE_CONTEXT_RETARGETED');
  assert.equal(requests[0]?.locale, 'sv-SE'); assert.equal(Object.isFrozen(requests[0]?.context), true);
});

test('default transcription language is automatic rather than the browser interface language', async () => {
  const platform = new Platform(); const locales: string[] = [];
  const voice = new ProviderVoice({ captureContext: context, transcribe: async (_audio, options) => { locales.push(options.locale); return 'Hej'; } }, platform);
  assert.deepEqual(await voice.capability(), { type: 'provider_transcription', locale: '', provider_label: 'OpenAI' }, 'AUTOMATIC_TRANSCRIPTION_LANGUAGE_REPLACED');
  await voice.start('s', () => {}); await voice.stop('s'); platform.captures[0]!.finish(); await flush();
  assert.deepEqual(locales, ['']);
});

test('cancelling a pending permission request releases its late microphone without recording', async () => {
  const f = fixture(); const pending = deferred<AudioCapture>(); f.platform.next = pending.promise;
  const started = f.start('old'); await f.voice.cancel('old');
  f.platform.next = undefined; await f.start('new');
  const late = new Capture(); pending.resolve(late); await started;
  assert.equal(late.started, false); assert.equal(late.released, 1, 'LATE_MICROPHONE_NOT_RELEASED');
  assert.equal(f.events.at(-1)?.session_id, 'new');
  await f.voice.cancel('new');
});

test('stopping before microphone permission resolves cancels that request', async () => {
  const f = fixture(); const pending = deferred<AudioCapture>(); f.platform.next = pending.promise;
  const started = f.start(); await f.voice.stop('capture');
  const capture = new Capture(); pending.resolve(capture); await started;
  assert.equal(capture.started, false); assert.equal(capture.released, 1);
  assert.equal(f.events.at(-1)?.type, 'cancelled');
});

test('cancel aborts provider work and fences a late result after a new session starts', async () => {
  const result = deferred<string>(); const signals: AbortSignal[] = [];
  const f = fixture(async (_audio, { signal }) => { signals.push(signal); return result.promise; });
  await f.start('old'); const old = f.platform.captures[0]!;
  await f.voice.stop('old'); old.finish(); assert.equal(signals[0]?.aborted, false);
  await f.voice.cancel('old'); assert.equal(signals[0]?.aborted, true, 'PROVIDER_UPLOAD_NOT_ABORTED');
  await f.start('new'); const count = f.events.length;
  result.resolve('Late text from a different workspace'); await flush();
  old.handlers?.failed(); old.handlers?.stopped();
  assert.equal(f.events.length, count, 'CANCELLED_TRANSCRIPT_PUBLISHED'); await f.voice.cancel('new');
});

test('duration stops microphone capture and provider timeout aborts without inventing a transcript', async () => {
  const result = deferred<string>(); let signal: AbortSignal | undefined;
  const f = fixture(async (_audio, options) => { signal = options.signal; return result.promise; });
  await f.start(); const capture = f.platform.captures[0]!;
  f.platform.fire(PROVIDER_VOICE_LIMITS.recordingMs);
  assert.equal(capture.stopped, 1); assert.ok(capture.released > 0);
  capture.finish(); f.platform.fire(PROVIDER_VOICE_LIMITS.transcriptionMs);
  assert.equal(signal?.aborted, true); assert.equal(f.events.at(-1)?.type, 'error');
  result.resolve('too late'); await flush();
  assert.equal(f.events.some(event => event.type === 'transcript' || event.type === 'stopped'), false);
});

test('missing final recorder event times out after releasing the microphone', async () => {
  let requests = 0; const f = fixture(async () => { requests++; return 'wrong'; });
  await f.start(); await f.voice.stop('capture');
  f.platform.fire(PROVIDER_VOICE_LIMITS.finalChunkMs);
  assert.ok(f.platform.captures[0]!.released > 0);
  f.platform.captures[0]!.finish(); await flush();
  assert.equal(requests, 0); assert.equal(f.events.at(-1)?.type, 'error');
});

test('oversized recording is rejected and never uploaded, including late final chunks', async () => {
  let requests = 0; const f = fixture(async () => { requests++; return 'wrong'; });
  await f.start(); const capture = f.platform.captures[0]!;
  capture.handlers?.data(new Blob([new Uint8Array(PROVIDER_VOICE_LIMITS.audioBytes + 1)]));
  capture.finish(); await flush();
  assert.equal(requests, 0, 'OVERSIZED_RECORDING_UPLOADED'); assert.ok(capture.released > 0);
  assert.equal(f.events.at(-1)?.type, 'error');
});

test('empty audio, interruption and stop failure release capture without success', async () => {
  for (const outcome of ['empty', 'interrupted', 'stop_failed']) {
    let requests = 0; const f = fixture(async () => { requests++; return 'wrong'; });
    await f.start(); const capture = f.platform.captures[0]!;
    if (outcome === 'empty') capture.handlers?.stopped();
    else if (outcome === 'interrupted') { capture.failStop = true; capture.handlers?.failed(); }
    else { capture.failStop = true; await f.voice.stop('capture'); }
    await flush(); assert.equal(requests, 0); assert.ok(capture.released > 0);
    assert.equal(f.events.at(-1)?.type, 'error');
  }
});

test('permission and provider errors use fixed messages rather than arbitrary exception details', async () => {
  const permission = fixture(); const denied = new Error('sensitive permission details'); denied.name = 'NotAllowedError';
  permission.platform.next = Promise.reject(denied); await permission.start();
  const last = permission.events.at(-1); assert.equal(last?.type, 'error');
  if (last?.type === 'error') assert.match(last.message, /permission was denied/);
  const provider = fixture(async () => { throw new Error('sensitive provider credential value'); });
  await provider.start(); await provider.voice.stop('capture'); provider.platform.captures[0]!.finish(); await flush();
  assert.equal(provider.events.at(-1)?.type, 'error');
  assert.equal(JSON.stringify([...permission.events, ...provider.events]).includes('sensitive'), false);
});

test('empty and oversized provider output are errors rather than partial or successful transcripts', async () => {
  for (const text of ['   ', 'x'.repeat(PROVIDER_VOICE_LIMITS.transcriptCharacters + 1)]) {
    const f = fixture(async () => text); await f.start();
    await f.voice.stop('capture'); f.platform.captures[0]!.finish(); await flush();
    assert.equal(f.events.at(-1)?.type, 'error', 'INVALID_PROVIDER_TRANSCRIPT_ACCEPTED'); assert.equal(f.events.some(event => event.type === 'transcript'), false);
  }
});

test('missing capability, unsupported recorder output and missing connection never upload', async () => {
  const unavailable = fixture(); unavailable.platform.enabled = false;
  assert.equal((await unavailable.voice.capability()).type, 'unavailable'); await unavailable.start();
  assert.equal(unavailable.platform.opened.length, 0);
  const badFormat = fixture(); const capture = new Capture(); capture.mimeType = 'audio/ogg';
  badFormat.platform.next = Promise.resolve(capture); await badFormat.start();
  assert.equal(capture.started, false); assert.equal(capture.released, 1);
  const platform = new Platform(); const events: VoiceEvent[] = [];
  const voice = new ProviderVoice({ captureContext: () => ({ workspaceId: 'w', connectionVersion: 'invalid' }), transcribe: async () => 'wrong' }, platform);
  await voice.start('s', event => events.push(event));
  assert.equal(platform.opened.length, 0); assert.equal(events.at(-1)?.type, 'error');
});
