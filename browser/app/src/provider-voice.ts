import type { VoiceCapability, VoiceEvent, VoicePort } from '../../../native/ui/src/voice-session.ts';

export const PROVIDER_VOICE_LIMITS = Object.freeze({
  recordingMs: 120_000, audioBytes: 20_000_000, chunks: 4096,
  finalChunkMs: 5_000, transcriptionMs: 120_000, transcriptCharacters: 12_000,
});
const MIME_TYPES = ['audio/webm;codecs=opus', 'audio/mp4;codecs=mp4a.40.2', 'audio/webm', 'audio/mp4'] as const;

/** Contains addresses only. Credentials remain behind the injected provider port. */
export type ProviderVoiceContext = Readonly<{ workspaceId: string; connectionVersion: string }>;
export type ProviderTranscribe = (audio: Blob, options: Readonly<{
  signal: AbortSignal; locale: string; context: ProviderVoiceContext;
}>) => Promise<string>;
type CaptureHandlers = Readonly<{
  started(): void; data(chunk: Blob): void; stopped(): void; failed(): void;
}>;
export interface AudioCapture {
  readonly mimeType: string;
  start(handlers: CaptureHandlers): void;
  stop(): void;
  /** Release every microphone track, including after a cancelled permission request. */
  release(): void;
}
export interface CapturePlatform {
  available(): boolean;
  supports(mimeType: string): boolean;
  open(mimeType: string): Promise<AudioCapture>;
  schedule(callback: () => void, milliseconds: number): () => void;
}
export type ProviderVoiceOptions = Readonly<{
  captureContext(): ProviderVoiceContext;
  transcribe: ProviderTranscribe;
  providerLabel?: string;
  locale?: string;
}>;
type Phase = Readonly<{ type: 'requesting' | 'transcribing' }>
  | Readonly<{ type: 'recording' | 'finishing'; capture: AudioCapture }>;
type ActiveCapture = {
  readonly id: string; readonly emit: (event: VoiceEvent) => void;
  readonly context: ProviderVoiceContext; readonly locale: string;
  readonly abort: AbortController;
  phase: Phase; chunks: Blob[]; bytes: number; cancelTimer?: () => void;
};

/** Browser effects are kept behind this small capture boundary. */
function browserCapture(): CapturePlatform {
  return {
    available: () => globalThis.isSecureContext === true
      && typeof globalThis.MediaRecorder === 'function'
      && typeof globalThis.navigator?.mediaDevices?.getUserMedia === 'function',
    supports: mimeType => globalThis.MediaRecorder.isTypeSupported(mimeType),
    async open(mimeType) {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      let detach = () => {};
      const release = () => {
        detach();
        for (const track of stream.getTracks()) { track.onended = null; track.stop(); }
      };
      try {
        const recorder = new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 96_000 });
        return {
          get mimeType() { return recorder.mimeType; },
          start(handlers) {
            recorder.onstart = handlers.started;
            recorder.ondataavailable = event => handlers.data(event.data);
            recorder.onstop = handlers.stopped;
            recorder.onerror = handlers.failed;
            for (const track of stream.getTracks()) track.onended = handlers.failed;
            const hidden = () => { if (document.hidden) handlers.failed(); };
            document.addEventListener('visibilitychange', hidden);
            window.addEventListener('pagehide', handlers.failed);
            detach = () => {
              document.removeEventListener('visibilitychange', hidden);
              window.removeEventListener('pagehide', handlers.failed);
            };
            if (document.hidden) { handlers.failed(); return; }
            recorder.start(250);
          },
          stop() { if (recorder.state !== 'inactive') recorder.stop(); },
          release,
        };
      } catch (error) { release(); throw error; }
    },
    schedule(callback, milliseconds) {
      const timer = setTimeout(callback, milliseconds);
      return () => clearTimeout(timer);
    },
  };
}

function microphoneError(error: unknown): string {
  if (error instanceof Error) {
    if (error.name === 'NotAllowedError' || error.name === 'SecurityError')
      return 'Microphone permission was denied. You can type instead.';
    if (error.name === 'NotFoundError' || error.name === 'NotReadableError')
      return 'The microphone is unavailable. Check your device or type instead.';
  }
  return 'Audio recording could not start. You can type instead.';
}

/** Bounded recording followed by provider transcription; never executes a command. */
export class ProviderVoice implements VoicePort {
  readonly #options: ProviderVoiceOptions;
  readonly #platform: CapturePlatform;
  readonly #locale: string;
  #active: ActiveCapture | null = null;

  constructor(options: ProviderVoiceOptions, platform: CapturePlatform = browserCapture()) {
    this.#options = options; this.#platform = platform;
    // Empty locale asks the provider to detect speech language, independently of UI language.
    this.#locale = options.locale ?? '';
  }
  #mime(): string | undefined {
    return this.#platform.available() ? MIME_TYPES.find(mime => this.#platform.supports(mime)) : undefined;
  }
  async capability(): Promise<VoiceCapability> {
    return this.#mime()
      ? { type: 'provider_transcription', locale: this.#locale, provider_label: this.#options.providerLabel ?? 'OpenAI' }
      : { type: 'unavailable', reason: 'Audio recording is unavailable in this browser. Type or use your keyboard’s dictation.' };
  }
  /** Call directly from the voice button. Permission and capture are never requested by capability(). */
  async start(id: string, emit: (event: VoiceEvent) => void, locale = this.#locale): Promise<void> {
    if (this.#active) throw new Error('Voice capture is already active.');
    const mimeType = this.#mime();
    if (!mimeType) { emit({ type: 'error', session_id: id, message: 'Audio recording is unavailable. You can type instead.' }); return; }
    let context: ProviderVoiceContext;
    try {
      const snapshot = this.#options.captureContext();
      context = Object.freeze({ workspaceId: snapshot.workspaceId, connectionVersion: snapshot.connectionVersion });
      if (!context.workspaceId || !/^[0-9a-f]{64}$/.test(context.connectionVersion)) throw new Error('Missing connection.');
    } catch {
      emit({ type: 'error', session_id: id, message: 'Connect OpenAI in this workspace before recording.' }); return;
    }
    const active: ActiveCapture = {
      id, emit, context, locale, abort: new AbortController(),
      phase: { type: 'requesting' }, chunks: [], bytes: 0,
    };
    this.#active = active;
    emit({ type: 'starting', session_id: id });
    if (this.#active !== active) return;
    try {
      const capture = await this.#platform.open(mimeType);
      if (this.#active !== active) { capture.release(); return; }
      const actualType = capture.mimeType.toLowerCase().split(';', 1)[0];
      if (actualType !== 'audio/webm' && actualType !== 'audio/mp4') {
        capture.release(); this.#fail(active, 'This browser recorded an unsupported audio format. You can type instead.'); return;
      }
      active.phase = { type: 'recording', capture };
      active.cancelTimer = this.#platform.schedule(() => this.#stopRecording(active), PROVIDER_VOICE_LIMITS.recordingMs);
      capture.start({
        started: () => { if (this.#active === active && active.phase.type === 'recording') emit({ type: 'listening', session_id: id }); },
        data: chunk => this.#data(active, chunk),
        stopped: () => this.#recordingFinished(active),
        failed: () => this.#fail(active, 'Audio capture was interrupted. Record again or type instead.'),
      });
    } catch (error) { this.#fail(active, microphoneError(error)); }
  }
  async stop(id: string): Promise<void> {
    const active = this.#active;
    if (active?.id !== id) return;
    if (active.phase.type === 'requesting') { await this.cancel(id); return; }
    this.#stopRecording(active);
  }
  async cancel(id: string): Promise<void> {
    const active = this.#active;
    if (active?.id !== id) return;
    this.#active = null;
    this.#dispose(active);
    active.emit({ type: 'cancelled', session_id: id });
  }
  #data(active: ActiveCapture, chunk: Blob): void {
    if (this.#active !== active || (active.phase.type !== 'recording' && active.phase.type !== 'finishing') || !chunk.size) return;
    if (active.bytes + chunk.size > PROVIDER_VOICE_LIMITS.audioBytes || active.chunks.length >= PROVIDER_VOICE_LIMITS.chunks) {
      this.#fail(active, 'The recording is too large. Record a shorter command or type instead.'); return;
    }
    active.bytes += chunk.size; active.chunks.push(chunk);
  }
  #stopRecording(active: ActiveCapture): void {
    if (this.#active !== active || active.phase.type !== 'recording') return;
    const capture = active.phase.capture;
    active.phase = { type: 'finishing', capture };
    active.cancelTimer?.();
    active.cancelTimer = this.#platform.schedule(() => this.#fail(active, 'The recording did not finish. Record again or type instead.'), PROVIDER_VOICE_LIMITS.finalChunkMs);
    try { capture.stop(); }
    catch { this.#fail(active, 'The recording could not finish. Record again or type instead.'); }
    finally { capture.release(); }
  }
  #recordingFinished(active: ActiveCapture): void {
    if (this.#active !== active || (active.phase.type !== 'recording' && active.phase.type !== 'finishing')) return;
    const capture = active.phase.capture;
    capture.release(); active.cancelTimer?.();
    const audio = new Blob(active.chunks, { type: capture.mimeType });
    active.chunks = []; active.phase = { type: 'transcribing' };
    if (!audio.size) { this.#fail(active, 'No audio was recorded. Try again or type instead.'); return; }
    active.emit({ type: 'transcribing', session_id: active.id });
    if (this.#active !== active) return;
    active.cancelTimer = this.#platform.schedule(() => this.#fail(active, 'Transcription timed out. Try again or type instead.'), PROVIDER_VOICE_LIMITS.transcriptionMs);
    void this.#transcribe(active, audio);
  }
  async #transcribe(active: ActiveCapture, audio: Blob): Promise<void> {
    try {
      const text = await this.#options.transcribe(audio, {
        signal: active.abort.signal, locale: active.locale, context: active.context,
      });
      if (this.#active !== active) return;
      if (typeof text !== 'string' || !text.trim() || text.length > PROVIDER_VOICE_LIMITS.transcriptCharacters) {
        this.#fail(active, 'The provider did not return a usable transcript. Record again or type instead.'); return;
      }
      active.cancelTimer?.(); this.#active = null;
      active.emit({ type: 'transcript', session_id: active.id, text: text.trim(), is_final: true });
      active.emit({ type: 'stopped', session_id: active.id });
    } catch {
      this.#fail(active, 'Transcription failed. Check the workspace’s OpenAI connection or type instead.');
    }
  }
  #fail(active: ActiveCapture, message: string): void {
    if (this.#active !== active) return;
    this.#active = null; this.#dispose(active);
    active.emit({ type: 'error', session_id: active.id, message });
  }
  #dispose(active: ActiveCapture): void {
    active.cancelTimer?.(); active.abort.abort(); active.chunks = [];
    if (active.phase.type === 'recording' || active.phase.type === 'finishing') {
      try { active.phase.capture.stop(); } catch { /* Releasing tracks below ends microphone capture. */ }
      finally { active.phase.capture.release(); }
    }
  }
}
