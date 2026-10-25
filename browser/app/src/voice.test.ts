import test from 'node:test';
import assert from 'node:assert/strict';
import { BrowserVoice } from './voice.ts';
import type { Recognition } from './voice.ts';
import { VoiceSession } from '../../../native/ui/src/voice-session.ts';
import type { VoiceEvent } from '../../../native/ui/src/voice-session.ts';

class Speech implements Recognition {
  static created: Speech[] = [];
  lang = ''; continuous = false; interimResults = false;
  onstart: Recognition['onstart'] = null; onend: Recognition['onend'] = null;
  onerror: Recognition['onerror'] = null; onresult: Recognition['onresult'] = null;
  started = false; stopped = false; aborted = false;
  constructor() { Speech.created.push(this); }
  start() { this.started = true; this.onstart?.(); }
  stop() { this.stopped = true; }
  abort() { this.aborted = true; }
}
test('speech starts explicitly, retains final results during stop, and fences cancelled capture', async () => {
  Speech.created = []; const voice = new BrowserVoice(Speech, 'en-GB'); const events: VoiceEvent[] = [];
  assert.deepEqual(await voice.capability(), { type: 'browser_speech', locale: 'en-GB' });
  assert.equal(Speech.created.length, 0);
  await voice.start('first', event => events.push(event));
  const first = Speech.created[0]!; assert.equal(first.started, true); assert.equal(first.lang, 'en-GB');
  await voice.stop('wrong'); assert.equal(first.stopped, false);
  await voice.stop('first'); assert.equal(first.stopped, true);
  first.onresult?.({ results: [{ 0: { transcript: 'Retained' }, isFinal: true }] });
  first.onend?.(); assert.ok(events.some(event => event.type === 'transcript' && event.text === 'Retained'));
  await voice.start('second', event => events.push(event));
  const second = Speech.created[1]!; await voice.cancel('second');
  const length = events.length; second.onresult?.({ results: [{ 0: { transcript: 'Late' }, isFinal: true }] });
  second.onend?.(); assert.equal(second.aborted, true); assert.equal(events.length, length);
});
test('provider errors expose a typing fallback without reflecting remote error text', async () => {
  Speech.created = []; const events: VoiceEvent[] = []; const voice = new BrowserVoice(Speech, 'en');
  await voice.start('s', event => events.push(event)); Speech.created[0]!.onerror?.({ error: 'not-allowed' });
  const last = events.at(-1); assert.equal(last?.type, 'error');
  if (last?.type === 'error') assert.match(last.message, /permission was denied.*type instead/);
  await voice.start('s2', event => events.push(event)); assert.equal(Speech.created.length, 2);
});
test('browser transcripts keep their captured workspace and never run commands', async () => {
  Speech.created = []; const captured: unknown[] = []; const voice = new BrowserVoice(Speech, 'en');
  const session = new VoiceSession(voice, (text, address) => captured.push({ text, address }), () => {}, () => 'capture');
  const address = { workspaceId: 'work', workspaceTitle: 'Work', resourceId: 'note', resourceTitle: 'Note', heads: ['revision'] };
  await session.start(address, await voice.capability());
  Speech.created[0]!.onresult?.({ results: [{ 0: { transcript: 'Remember this' }, isFinal: true }] });
  assert.deepEqual(captured, [{ text: 'Remember this', address }]);
  await session.cancel(); Speech.created[0]!.onresult?.({ results: [{ 0: { transcript: 'Another workspace' }, isFinal: true }] });
  assert.equal(captured.length, 1);
});
