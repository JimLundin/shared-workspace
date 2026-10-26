# Meeting material provider

The browser worker can turn pinned transcript revisions into cited notes,
assigned or unassigned actions, and editable **unsent follow-up text**. The provider
does not create an Outlook draft, send mail, save workspace resources, or execute
actions. The host owner validates the active Work/run and publishes the complete
result through the domain's atomic publication command.

## Provider and connection boundary

`browser/host/src/meeting-generation.ts` exports `generateMeeting(value, resolve,
signal, request = fetch)`. Its immutable input is:

```ts
{
  workspace_id: string;
  connection_version: string;
  outcome: string;
  sources: readonly {
    source: { workspace_id: string; resource_id: string; revision_id: string };
    text: string;
  }[];
}
```

The owner must resolve source text from its actual snapshot using those exact
identities, check the preparing run belongs to this device, and retain the
expected Work heads for publication. Generation copies and freezes the capture
before any await; changing UI selection or a caller's mutable object cannot
retarget an in-flight request. The same workspace and connection version are
resolved immediately before upload and again before returning the validated
result. Revoking or replacing that connection invalidates the result. Work/run
checks and the final publication transaction remain the owner's responsibility.

The fixed destination is `https://api.openai.com/v1/responses`. The worker reuses
the workspace-scoped OpenAI credential resolver used by transcription, while
selecting the text model `gpt-6-luna` with `reasoning.effort: "none"`. The
connection's `gpt-4o-transcribe` model remains specific to audio transcription.

The request explicitly sets `store: false`, `truncation: "disabled"`, a 16,000
output-token limit, and strict Structured Outputs through `text.format`. Every
schema object rejects additional properties and requires every declared field.
There are no tools, automatic retries, previous-response IDs, conversation IDs,
or configurable endpoints. Fetch omits ambient browser credentials and referrers,
disables caching, and rejects redirects. `store: false` controls Responses storage;
it is not a claim about account-wide data-retention policies.

## Evidence and review

The result contains `notes` and `follow_up`, each with `title`, `body`, and
`citations`, plus `actions` with `text`, `owner`, `deadline`, and `basis`. The owner
allocates output resource and action identities. Each citation has the exact
source reference and a nonempty `quote`. Every quotation must occur verbatim in
that pinned revision. Another revision of the same resource cannot substitute for
it, and only sources in the captured workspace are accepted.

Generated action text, assigned owner names, and stated deadlines require
`{ type: "transcript", citations }` evidence. A generated owner name or deadline
must also appear verbatim in one of its own supporting quotations. Missing owners
remain `{ type: "unassigned" }`; missing deadlines remain
`{ type: "unspecified" }`. A stated deadline preserves original wording such as
“next Friday”; the provider is instructed not to infer a calendar date. The
generator rejects `{ type: "user" }` evidence, which belongs to later user
corrections through the Work edit interface.

These checks establish source identity and exact quotation provenance. They do
**not** prove that a quotation semantically entails a generated summary or that a
mentioned person actually owns an action. The prompt requires explicit support;
the interface must keep materials reviewable and support corrections. An empty
action list is valid when the transcript contains no explicit actions.

Transcript content is JSON-encoded as user input, separate from fixed provider
instructions that identify it as untrusted data. The provider has no tools with
which to follow embedded commands. Prompt separation cannot guarantee perfect
model behavior; local validation and human review remain necessary.

## Bounds, cancellation, and errors

- 1–16 distinct source references, at most 128 KiB per transcript and 512 KiB of
  combined transcript text; 4 KiB for the requested outcome.
- At most 1 MiB for the serialized request and 1 MiB for the streamed response.
  Oversized inputs/results are rejected rather than truncated.
- At most 64 actions; 1–16 citations for each material or evidence basis; at most
  2 KiB per quote, 4 KiB per action/deadline, 120 bytes per title/owner, and 128 KiB
  per material body. The validated material JSON is limited to 384 KiB, leaving
  room for host-allocated identities in the domain's 512 KiB publication limit.
- One 120-second time limit covers credential resolution, request, body reading,
  validation, and the final lookup. Cancellation aborts fetch, cancels response
  reading, and bounds waits even when an injected resolver/stream ignores abort.
  Cancellation does not prove the provider performed no processing after upload.
- Direct HTTP Responses output is decoded through `output[].content[]`, not the
  SDK-only `output_text` convenience field. Refused, incomplete, malformed,
  duplicate-message, or tool-call output is rejected.
- Provider error bodies and arbitrary exceptions are never reflected to the UI.
  Successful material containing the workspace API key is rejected after JSON
  decoding. This module has no logging or credential-persistence path.

## Evidence status and checks

The automated checks use synthetic transcripts, synthetic credentials, and
controlled fetch responses. They prove local request construction, immutable
capture, source validation, connection invalidation, bounded streaming,
cancellation, and error behavior. They do not establish authenticated account
access, live model quality, physical iPhone operation, or peer delivery.

Run from `browser/host`:

```sh
npm run check
node --experimental-strip-types --test tests/meeting-generation.test.mjs
node tests/meeting-generation.mutations.mjs
```

The mutation runner uses temporary copies, leaving the shared implementation
untouched. Its eight changes disable provider-storage policy, fixed endpoint,
quotation checking, owner/deadline evidence, final connection validation,
credential-reflection prevention, workspace source scope, and request byte limit.
Each must fail its named behavioral assertion; compilation failures, setup
failures, or timeouts do not count as killed mutations.

## Official OpenAI documentation

Consulted 26 October 2026 using the OpenAI Docs skill:

- [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna): Responses,
  Structured Outputs, model identifier, and supported reasoning efforts.
- [Create a response](https://developers.openai.com/api/reference/resources/responses/methods/create):
  endpoint, `store`, output limits, truncation, response status, and raw output
  structure.
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs):
  Responses `text.format`, strict schemas, nested alternatives, required fields,
  refusals, and incomplete responses.
