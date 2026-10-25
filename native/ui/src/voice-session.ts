import type { Address } from './commands.ts';
export type VoiceCapability = Readonly<{ type: 'native_speech'; locale: string; on_device: boolean; authorization: string }> | Readonly<{ type: 'browser_speech'; locale: string }> | Readonly<{ type: 'system_dictation' }> | Readonly<{ type: 'unavailable'; reason: string }>;
export type VoiceEvent = Readonly<{ type: 'starting' | 'listening' | 'stopped' | 'cancelled' | 'system_dictation'; session_id: string }> | Readonly<{ type: 'transcript'; session_id: string; text: string; is_final: boolean }> | Readonly<{ type: 'error'; session_id: string; message: string }>;
export interface VoicePort {
  capability(): Promise<VoiceCapability>;
  start(sessionId: string, onEvent: (event: VoiceEvent) => void, locale?: string): Promise<void>;
  stop(sessionId: string): Promise<void>;
  cancel(sessionId: string): Promise<void>;
}
export type VoiceState = Readonly<{ type: 'idle'; message: string }> | Readonly<{ type: 'starting' | 'listening' | 'stopping' | 'system_dictation'; sessionId: string; message: string }>;
/** A speech callback is accepted only while its captured session is current. */
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
    this.update({ type: 'starting', sessionId, message: capability.type === 'system_dictation' ? 'Opening Windows voice typing…' : 'Requesting microphone access…' });
    try {
      await this.port.start(sessionId, event => {
        if (this.epoch !== epoch || event.session_id !== sessionId) return;
        switch (event.type) {
          case 'transcript': this.onText(event.text, address); break;
          case 'starting': this.update({ type: 'starting', sessionId, message: 'Starting speech…' }); break;
          case 'listening': this.update({ type: 'listening', sessionId, message: 'Listening. Stop when you have finished.' }); break;
          case 'system_dictation': this.update({ type: 'system_dictation', sessionId, message: 'Windows controls voice typing and its microphone. Review the words here before running.' }); break;
          case 'stopped': this.epoch++; this.update({ type: 'idle', message: 'Capture stopped. Review the transcript before running.' }); break;
          case 'cancelled': this.epoch++; this.update({ type: 'idle', message: 'Capture cancelled. Existing input is retained.' }); break;
          case 'error': this.epoch++; this.update({ type: 'idle', message: event.message }); break;
        }
      }, capability.type === 'native_speech' || capability.type === 'browser_speech' ? capability.locale : undefined);
    } catch { if (this.epoch === epoch) { this.epoch++; this.update({ type: 'idle', message: 'Voice could not start. Type your command instead.' }); } }
  }
  async stop(): Promise<void> {
    if (this.state.type === 'idle') return;
    if (this.state.type === 'system_dictation') { this.epoch++; this.update({ type: 'idle', message: 'Input retained. Close the Windows voice control when finished; Windows owns its microphone.' }); return; }
    const sessionId = this.state.sessionId, epoch = this.epoch;
    this.update({ type: 'stopping', sessionId, message: 'Finishing speech…' });
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
