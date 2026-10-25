import { Channel, invoke, isTauri } from '@tauri-apps/api/core';
import { object, string } from './protocol.ts';
import type { VoiceCapability, VoiceEvent, VoicePort } from './voice-session.ts';
export { VoiceSession } from './voice-session.ts';
export type { VoiceCapability, VoiceEvent, VoicePort, VoiceState } from './voice-session.ts';
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
