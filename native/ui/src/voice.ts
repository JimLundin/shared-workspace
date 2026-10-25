import { Channel, invoke, isTauri } from '@tauri-apps/api/core';
import { object, string } from './protocol.ts';
import type { Address } from './commands.ts';
export type VoiceCapability = Readonly<{ type: 'native_speech'; locale: string; on_device: boolean; authorization: string }> | Readonly<{ type: 'system_dictation' }> | Readonly<{ type: 'unavailable'; reason: string }>;
export type VoiceEvent = Readonly<{ type: 'starting' | 'listening' | 'stopped' | 'cancelled' | 'system_dictation'; session_id: string }> | Readonly<{ type: 'transcript'; session_id: string; text: string; is_final: boolean }> | Readonly<{ type: 'error'; session_id: string; message: string }>;
export interface VoicePort {
  capability(): Promise<VoiceCapability>;
  start(sessionId: string, onEvent: (event: VoiceEvent) => void, locale?: string): Promise<void>;
  stop(sessionId: string): Promise<void>;
  cancel(sessionId: string): Promise<void>;
}
export function decodeVoiceEvent(value: unknown): VoiceEvent {
  const e = object(value), session_id = string(e.session_id);
  switch (e.type) {
    case 'starting': case 'listening': case 'stopped': case 'cancelled': case 'system_dictation': return { type: e.type, session_id };
    case 'transcript': if (typeof e.is_final !== 'boolean') throw new Error('Invalid speech event.'); return { type: e.type, session_id, text: string(e.text), is_final: e.is_final };
    case 'error': return { type: e.type, session_id, message: string(e.message) };
    default: throw new Error('Unknown speech event.');
  }
}
export class NativeVoice implements VoicePort {
  async capability(): Promise<VoiceCapability> {
    if (!isTauri()) return { type: 'unavailable', reason: 'Open the native app to use this device’s voice input.' };
    try {
      const c = object(await invoke('plugin:workspace-voice|capability'));
      if (c.type === 'system_dictation') return { type: c.type };
      if (c.type === 'unavailable') return { type: c.type, reason: string(c.reason) };
      if (c.type === 'native_speech' && typeof c.on_device === 'boolean') return { type: c.type, locale: string(c.locale), on_device: c.on_device, authorization: string(c.authorization) };
      return { type: 'unavailable', reason: 'This host returned an unsupported voice capability.' };
    } catch { return { type: 'unavailable', reason: 'Native voice is unavailable on this host. Typing remains available.' }; }
  }
  async start(sessionId: string, onEvent: (event: VoiceEvent) => void, locale?: string): Promise<void> {
    const channel = new Channel<unknown>();
    channel.onmessage = value => { try { onEvent(decodeVoiceEvent(value)); } catch { onEvent({ type: 'error', session_id: sessionId, message: 'The host returned an invalid speech event.' }); } };
    await invoke('plugin:workspace-voice|start', { session_id: sessionId, ...(locale ? { locale } : {}), on_event: channel });
  }
  async stop(sessionId: string): Promise<void> { await invoke('plugin:workspace-voice|stop', { session_id: sessionId }); }
  async cancel(sessionId: string): Promise<void> { await invoke('plugin:workspace-voice|cancel', { session_id: sessionId }); }
}
export type VoiceState = Readonly<{ type: 'idle'; message: string }> | Readonly<{ type: 'starting' | 'listening' | 'stopping' | 'system_dictation'; sessionId: string; message: string }>;
/** The native callback is accepted only while its captured session is current. */
export class VoiceSession {
  state: VoiceState = { type: 'idle', message: 'Voice starts only when you choose it.' };
  private epoch = 0;
  private readonly port: VoicePort;
  private readonly onText: (text: string, address: Address) => void;
  private readonly onChange: () => void;
  private readonly id: () => string;
  constructor(port: VoicePort, onText: (text: string, address: Address) => void, onChange: () => void, id: () => string = () => crypto.randomUUID()) { this.port = port; this.onText = onText; this.onChange = onChange; this.id = id; }
  private update(state: VoiceState): void { this.state = state; this.onChange(); }
  async start(address: Address, capability: VoiceCapability): Promise<void> {
    if (this.state.type !== 'idle' || capability.type === 'unavailable') return;
    const epoch = ++this.epoch, sessionId = this.id();
    this.update({ type: 'starting', sessionId, message: capability.type === 'system_dictation' ? 'Opening Windows voice typing…' : 'Requesting native microphone access…' });
    try {
      await this.port.start(sessionId, event => {
        if (this.epoch !== epoch || event.session_id !== sessionId) return;
        switch (event.type) {
          case 'transcript': this.onText(event.text, address); break;
          case 'starting': this.update({ type: 'starting', sessionId, message: 'Starting native speech…' }); break;
          case 'listening': this.update({ type: 'listening', sessionId, message: 'Listening. Stop when you have finished.' }); break;
          case 'system_dictation': this.update({ type: 'system_dictation', sessionId, message: 'Windows controls voice typing and its microphone. Review the words here before running.' }); break;
          case 'stopped': this.epoch++; this.update({ type: 'idle', message: 'Capture stopped. Review the transcript before running.' }); break;
          case 'cancelled': this.epoch++; this.update({ type: 'idle', message: 'Capture cancelled. Existing input is retained.' }); break;
          case 'error': this.epoch++; this.update({ type: 'idle', message: event.message }); break;
        }
      }, capability.type === 'native_speech' ? capability.locale : undefined);
    } catch { if (this.epoch === epoch) { this.epoch++; this.update({ type: 'idle', message: 'Native voice could not start. Type your command instead.' }); } }
  }
  async stop(): Promise<void> {
    if (this.state.type === 'idle') return;
    if (this.state.type === 'system_dictation') { this.epoch++; this.update({ type: 'idle', message: 'Input retained. Close the Windows voice control when finished; Windows owns its microphone.' }); return; }
    const sessionId = this.state.sessionId, epoch = this.epoch;
    this.update({ type: 'stopping', sessionId, message: 'Finishing native speech…' });
    try { await this.port.stop(sessionId); } catch { if (this.epoch === epoch) this.update({ type: 'listening', sessionId, message: 'Stop was not confirmed. Try cancelling capture.' }); }
  }
  async cancel(): Promise<void> {
    if (this.state.type === 'idle') return;
    const state = this.state; const cancelEpoch = ++this.epoch;
    this.update({ type: 'idle', message: state.type === 'system_dictation' ? 'Input retained. Windows owns its microphone; close its voice control when finished.' : 'Input retained; requesting capture cancellation…' });
    if (state.type !== 'system_dictation') {
      try { await this.port.cancel(state.sessionId); if (this.epoch === cancelEpoch) this.update({ type: 'idle', message: 'Capture cancellation acknowledged. Input remains in its original context.' }); }
      catch { if (this.epoch === cancelEpoch) this.update({ type: 'idle', message: 'Capture cancellation was not confirmed. Use the device’s microphone control; late results will not change this command.' }); }
    }
  }
}
