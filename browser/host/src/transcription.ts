/** Provider requests belong to the worker, with a fixed endpoint and scoped key. */
import { fields, hex, requireThat } from './types.ts';
import { connectionError, connectionFailure, workspaceId, type ConnectionAction, type Transcription } from './connections.ts';

export function transcription(value: unknown): Transcription {
  const input = fields(value, ['workspace_id', 'connection_version', 'audio', 'locale']);
  requireThat(input.audio instanceof Blob && input.audio.size > 0 && input.audio.size <= 20_000_000, 'Invalid audio capture.');
  requireThat(/^audio\/(webm|mp4|mpeg|mp3|wav|x-wav)(;[\w=.+ -]+)?$/i.test(input.audio.type), 'Unsupported audio capture format.');
  requireThat(typeof input.locale === 'string' && (input.locale === '' || /^[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*$/.test(input.locale)) && input.locale.length <= 64, 'Invalid transcription locale.');
  return { workspace_id: workspaceId(input.workspace_id), connection_version: hex(input.connection_version), audio: input.audio, locale: input.locale };
}
async function boundedResponse(response: Response): Promise<unknown> {
  if (!response.body) throw connectionError('OpenAI returned an empty transcription.');
  const reader = response.body.getReader(), chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.length;
      if (length > 1_048_576) { await reader.cancel(); throw connectionError('OpenAI returned an oversized transcription.'); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
    finally { bytes.fill(0); }
  } finally { reader.releaseLock(); }
}
export async function transcribe(
  value: unknown,
  resolve: (workspace: string, version: string) => Promise<ConnectionAction & { type: 'set' }>,
  signal: AbortSignal,
  request: typeof fetch = fetch,
): Promise<string> {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal.addEventListener('abort', cancel, { once: true });
  const deadline = setTimeout(cancel, 120_000);
  try {
    if (signal.aborted) controller.abort();
    if (controller.signal.aborted) throw connectionError('Transcription was cancelled.');
    const input = transcription(value);
    const body = new FormData();
    const mime = input.audio.type.split(';')[0]!.toLowerCase();
    const extension = mime.includes('mp4') ? 'mp4' : mime.includes('wav') ? 'wav' : mime.includes('webm') ? 'webm' : 'mp3';
    body.append('file', input.audio, `recording.${extension}`);
    body.append('model', 'gpt-4o-transcribe');
    if (input.locale) body.append('language', input.locale.split('-')[0]!.toLowerCase());
    body.append('response_format', 'json');
    // This is the final awaited step before fetch. Navigation cannot alter it.
    const connection = await resolve(input.workspace_id, input.connection_version);
    if (controller.signal.aborted) throw connectionError('Transcription was cancelled.');
    const response = await request('https://api.openai.com/v1/audio/transcriptions', {
      method: 'POST', headers: { Authorization: `Bearer ${connection.api_key}` }, body,
      signal: controller.signal, cache: 'no-store', credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer', mode: 'cors',
    });
    if (!response.ok) {
      await response.body?.cancel();
      if (response.status === 401 || response.status === 403) throw connectionError('OpenAI rejected this workspace connection. Review its API key and access.');
      if (response.status === 429) throw connectionError('OpenAI is rate limited or this workspace connection has no available quota.');
      throw connectionError('OpenAI could not transcribe this recording. Try again.');
    }
    const result = await boundedResponse(response);
    requireThat(result !== null && typeof result === 'object' && 'text' in result && typeof result.text === 'string' && result.text.length <= 100_000, 'Invalid transcription response.');
    requireThat(!result.text.includes(connection.api_key), 'Invalid transcription response.');
    if (controller.signal.aborted) throw connectionError('Transcription was cancelled.');
    await resolve(input.workspace_id, input.connection_version);
    if (controller.signal.aborted) throw connectionError('Transcription was cancelled.');
    return result.text;
  } catch (error) {
    if (controller.signal.aborted) throw connectionError('Transcription was cancelled or exceeded its time limit.');
    throw connectionError(connectionFailure(error));
  } finally { clearTimeout(deadline); signal.removeEventListener('abort', cancel); }
}
