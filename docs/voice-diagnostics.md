# Held-key voice diagnostics

B dictation and N agent requests send captured audio directly to the configured
transcription endpoint. Neither uses the cleanup prompt. B normalizes whitespace
and removes control characters before submitting Unicode input; N queues the
full returned transcript. Clean dictation is a separate tool.

Each completed use in `%LOCALAPPDATA%\toolkit\usage.jsonl` records:

| Field | Meaning |
| --- | --- |
| `held_seconds`, `audio_seconds` | How long the key was held and the size of the returned PCM in seconds. |
| `transcribe_seconds` | Time spent preparing and requesting transcription, including failed requests. |
| `transcript_characters` | Length of the complete text field returned by the endpoint. Absent when transcription failed. |
| `requested_characters` | Length handed to the text-input or agent-queue operation. B includes its trailing separator space. |
| `submitted_characters` | Length after that operation returned successfully. Absent when submission failed. |
| `submit_seconds` | Time spent submitting the text, including failed submissions. |

These fields store no audio or transcript content. They are measured on the
assistant worker after recording; no sampling work is added to the microphone
or keyboard event loop. Each queued recording owns its own measurements.
Successful Unicode input submission means Windows accepted the events; it is
not a readback proving that the receiving text field displayed every character.

## Investigation on 2026-10-22

The reported symptom is a whole trailing sentence disappearing, sometimes after
only the first sentence. Existing timing metadata from 227 captures showed no
held/audio discrepancy above 0.24 seconds; this does not exclude silence or
device dropouts within the buffer.

Generated English speech was sent through the configured `gpt-4o-transcribe`
endpoint without microphone capture or playback:

| Fixture | Audio duration | Result |
| --- | --- | --- |
| Two sentences, short pause | 7.64 seconds | All 16 words returned. |
| Same sentences, 2.5-second pause | 12.14 seconds | All 16 words returned. |
| Six sentences with pauses | 36.69 seconds | All 75 words returned. |

The two-sentence fixture included “Everything afterwards just kind of
disappears.” Its ending was present in both responses. The intermittent loss
has not been reproduced, so these results do not establish an audio, model, or
delivery cause. No speculative transcription or input-delivery change is made.

On another occurrence, compare that use's `transcript_characters` and
`submitted_characters` with the visible text. A short endpoint response locates
the loss before submission; complete counts with missing visible text direct
the investigation to input delivery. Audio durations alone cannot distinguish
missing microphone signal from a transcription omission.

The documented model output limit is 2,000 tokens, which does not explain a
short two-sentence cutoff on its own. Sources:
[model limits](https://developers.openai.com/api/docs/models/gpt-4o-transcribe)
and [speech-to-text guide](https://developers.openai.com/api/docs/guides/speech-to-text).
