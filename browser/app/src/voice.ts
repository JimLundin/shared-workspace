import type { VoiceCapability, VoiceEvent, VoicePort } from '../../../native/ui/src/voice-session.ts';

type Result = Readonly<{ isFinal: boolean; 0: Readonly<{ transcript: string }> }>;
type SpeechResult = Readonly<{ results: ArrayLike<Result> }>;
type SpeechError = Readonly<{ error: string }>;
export interface Recognition {
  lang: string; continuous: boolean; interimResults: boolean;
  onstart: (() => void) | null; onend: (() => void) | null;
  onerror: ((event: SpeechError) => void) | null;
  onresult: ((event: SpeechResult) => void) | null;
  start(): void; stop(): void; abort(): void;
}
type RecognitionConstructor = new () => Recognition;
type SpeechWindow = Window & { SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor };
const errors: Readonly<Record<string, string>> = {
  'not-allowed': 'Microphone or speech permission was denied. You can type instead.',
  'service-not-allowed': 'Your browser’s speech service is unavailable. You can type instead.',
  'audio-capture': 'No microphone is available. You can type instead.',
  'no-speech': 'No speech was recognized. Try again or type instead.',
  network: 'The speech provider could not be reached. You can type instead.',
};

/** Explicit browser recognition. Audio is owned by the browser's provider. */
export class BrowserVoice implements VoicePort {
  #current: { id: string; recognition: Recognition; emit: (event: VoiceEvent) => void } | null = null;
  private readonly factory: RecognitionConstructor | undefined;
  private readonly locale: string;
  constructor(factory: RecognitionConstructor | undefined = (globalThis as unknown as SpeechWindow).SpeechRecognition ?? (globalThis as unknown as SpeechWindow).webkitSpeechRecognition,
    locale: string = navigator.language) { this.factory = factory; this.locale = locale; }
  async capability(): Promise<VoiceCapability> {
    return this.factory ? { type: 'browser_speech', locale: this.locale } : { type: 'unavailable', reason: 'Speech recognition is unavailable in this browser. Type or use your keyboard’s dictation.' };
  }
  async start(id: string, emit: (event: VoiceEvent) => void, locale = this.locale): Promise<void> {
    if (!this.factory) throw new Error('Browser speech is unavailable.');
    if (this.#current) throw new Error('Speech capture is already active.');
    const recognition = new this.factory();
    recognition.lang = locale; recognition.continuous = true; recognition.interimResults = true;
    const active = { id, recognition, emit }; this.#current = active;
    const current = () => this.#current === active;
    recognition.onstart = () => { if (current()) emit({ type: 'listening', session_id: id }); };
    recognition.onresult = event => {
      if (!current()) return;
      const results = Array.from(event.results);
      emit({ type: 'transcript', session_id: id, text: results.map(result => result[0].transcript).join(' ').slice(0, 12000), is_final: results.every(result => result.isFinal) });
    };
    recognition.onerror = event => {
      if (!current()) return;
      this.#current = null;
      emit({ type: 'error', session_id: id, message: errors[event.error] ?? 'Speech capture ended. Review existing input or type instead.' });
    };
    recognition.onend = () => { if (current()) { this.#current = null; emit({ type: 'stopped', session_id: id }); } };
    emit({ type: 'starting', session_id: id });
    try { recognition.start(); } catch (error) { this.#current = null; throw error; }
  }
  async stop(id: string): Promise<void> { if (this.#current?.id === id) this.#current.recognition.stop(); }
  async cancel(id: string): Promise<void> {
    if (this.#current?.id !== id) return;
    const active = this.#current; this.#current = null;
    active.recognition.abort(); active.emit({ type: 'cancelled', session_id: id });
  }
}
