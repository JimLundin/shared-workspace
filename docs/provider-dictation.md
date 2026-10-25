# Provider dictation

The browser records a short audio clip and asks the workspace's selected OpenAI
connection to transcribe it. The transcript remains editable command input; it
does not automatically run a command. Browser `SpeechRecognition` remains a
separate explicit fallback, rather than the provider path.

## Adapter and ownership

[`ProviderVoice`](../browser/app/src/provider-voice.ts) implements the existing
`VoicePort`. It reports `provider_transcription` capability and a `transcribing`
phase between microphone capture and transcript completion. Checking capability
does not request microphone permission. Recording starts from the voice button.

The adapter receives only an address snapshot and an injected callback:

```typescript
type ProviderVoiceContext = Readonly<{
  workspaceId: string;
  connectionVersion: string;
}>;

transcribe(audio: Blob, options: Readonly<{
  signal: AbortSignal;
  locale: string;
  context: ProviderVoiceContext;
}>): Promise<string>;
```

`captureContext()` is called synchronously when capture starts, before awaiting
microphone permission. The adapter copies the workspace ID and signed connection
revision into an immutable snapshot. The worker port must verify that exact
workspace/version before sending audio; a changed or revoked connection must not
silently redirect a pending recording. No credential is an adapter argument,
voice event, transcript field or diagnostic.

The default locale is the empty string: the provider should detect the spoken
language rather than forcing the interface/browser language. An explicit locale
can still be supplied. The HTTP adapter omits `language` for automatic detection.

Recording negotiates supported `audio/webm` with Opus or `audio/mp4` with AAC,
preserving the recorder's actual MIME type in the final Blob. The browser's own
`MediaRecorder.isTypeSupported()` decides availability. WebKit documents MP4/AAC
recording support, but support must still be checked on the actual device. [1]

The adapter bounds recordings at 120 seconds, 20,000,000 retained audio bytes
and 4,096 chunks. These are application limits, beneath the documented 25 MB
file-transcription limit. Browser suspension can delay JavaScript timers and
recorder events; the byte limit rejects an oversized delivered chunk rather than
uploading truncated audio. [2]

Stopping requests the recorder's final chunk and releases every microphone
track. Only the recorder's final event triggers transcription, and only a
nonempty provider result produces a final transcript followed by `stopped`.
A missing final event times out after five seconds. Provider transcription times
out after 120 seconds and aborts the request. `stop()` acknowledges the request
to stop; the subsequent events describe actual progress and completion.

Cancellation aborts provider work and rejects late callbacks from the old
session. A microphone granted after cancellation is immediately released without
starting its recorder. Recorder errors, permission denial, interrupted tracks,
page hiding and page departure while recording stop capture with a fixed message.
Arbitrary provider or browser exception text is not reflected into the UI.

## Provider protocol and browser feasibility

The current OpenAI implementation preserves the existing `gpt-4o-transcribe`
choice. The official OpenAI speech documentation describes multipart file
uploads to `POST /v1/audio/transcriptions` and accepts MP3, MP4, MPEG, MPGA, M4A,
WAV and WebM files up to 25 MB. Preserve the recorder's supported container and
filename extension; let `FormData` generate the multipart boundary. A JSON
transcription response contains recognized text. [2][3]

OpenAI's browser WebRTC documentation describes a different, streaming Realtime
flow: a developer-controlled backend either initiates the session or mints an
ephemeral credential for the browser. That flow does not establish that file
transcription is browser-compatible, and is not implemented by this recording
adapter. [4]

For the file path, a real Chromium browser smoke test sent its synthetic WebM
recording to `https://api.openai.com/v1/audio/transcriptions` using an explicitly
invalid, fixed test credential. In a cross-origin-isolated page, the multipart
request returned a readable **401 `invalid_api_key`** response with Fetch
response type `cors`. A separate unauthenticated OPTIONS request also allowed
POST and the authorization header. This establishes browser request/error
handling for the tested origin and endpoint. It does not establish paid
transcription, account/model access, speech quality, gateway compatibility, or
the user's device/network permissions.

The pre-existing desktop key belongs to a work gateway. It is not a direct
OpenAI key and must not be sent to `api.openai.com`. Successful authenticated
transcription remains a separate acceptance check after the user configures the
appropriate connection through the private UI. The browser does not acquire
server-secret guarantees merely by moving a request into a worker: same-origin
code remains inside the browser application's trust boundary.

## Verification

Run the focused lifecycle suite from `browser/app`:

```sh
node --experimental-strip-types --test src/provider-voice.test.ts
node tests/provider-voice-mutations.mjs
node tests/provider-voice-capture.mjs
node tests/provider-voice-capture.mjs --openai-cors
```

The first suite passes 13 tests covering final chunks, workspace/version capture,
pending permission cancellation, provider aborts and late results, duration and
byte bounds, recorder/provider failures, empty audio/output, and unavailable
formats and automatic language detection. Eight targeted temporary-copy mutations were killed: sharing a mutable
context, accepting late results, leaking a late microphone, ignoring audio size,
omitting abort, accepting an empty transcript, uploading before the final
chunk, and forcing an interface language. The unmodified temporary-copy baseline
also passed all 13 tests; mutations must fail through assertions to count as
killed, rather than merely fail to load.

The Chromium 156.0.8078.4 integration run used its synthetic microphone through
real `getUserMedia` and `MediaRecorder`. It produced a valid WebM/Opus Blob and
verified that tracks were ended before the injected transcription callback,
after cancellation, and after a simulated `pagehide` event. The transcript in
that harness is a fixture, not an OpenAI result. `--openai-cors` adds only the
explicit synthetic-invalid-key request described above. The script writes a
receipt under `/tmp/provider-voice-capture-chromium-evidence.json`; it does not
write the audio recording or any real credential.

The harness also provides `--webkit`: a synthesized Web Audio oscillator feeds
`MediaStreamDestination`, replacing microphone acquisition while using WebKit's
real MP4 recorder. MIME negotiation is deliberately limited to the MP4 formats
that that engine reports supporting. This is intended to exercise final chunks
and track cleanup in the MP4 branch, not physical iPhone microphone permissions.
The cached Playwright Linux WebKit build has no `MediaRecorder` global in either
headless WPE or headed GTK mode. Both were checked in
`mcr.microsoft.com/playwright:v1.64.1-noble`; the headed check used Xvfb. This is
an unavailable-capability result, not an MP4 recording pass. The harness records
that limitation and fails the requested recording check. Physical iPhone capture
and authenticated OpenAI transcription remain outstanding.

## Primary sources

1. [WebKit: MediaRecorder API](https://webkit.org/blog/11353/mediarecorder-api/).
2. [Official OpenAI documentation: speech to text](https://developers.openai.com/api/docs/guides/speech-to-text).
3. [Official OpenAI documentation: GPT-4o Transcribe](https://developers.openai.com/api/docs/models/gpt-4o-transcribe).
4. [Official OpenAI documentation: Realtime with WebRTC](https://developers.openai.com/api/docs/guides/realtime-webrtc).

Documentation fetched and request/capture evidence collected on 25 October 2026.
