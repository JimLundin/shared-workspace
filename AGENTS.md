# Scope

This repository owns Shared Workspace's browser/PWA and optional native hosts.
The Windows keyboard, system dictation, Python assistant and local executor belong
to [Toolkit](https://github.com/JimLundin/toolkit). Keep source, dependency pins and
release pipelines independent. Read [project boundaries](docs/project-boundaries.md)
when changing repository ownership or proposing a connection between the products.

# Design

- Model alternatives with Rust enums and TypeScript discriminated unions. Keep
  domain values immutable; use readonly payloads and exhaustive handling.
- Put decisions and state transitions in pure functions. Supply timestamps, IDs
  and observations; effect owners handle storage, network, capture and UI lifetime.
- Validate external data at typed boundaries. TypeScript annotations do not validate
  JSON, worker messages, persisted records or provider responses.
- Preserve captured workspace/resource/revision identities through asynchronous
  work. Exact retries retain operation identity; an unknown result remains unknown
  until evidence resolves it. Local save, peer delivery and external execution are
  separate observations.
- Use actual Turso Database and Iroh. Keep workspace access scoped to accepted
  grants; UI selection and device availability cannot expand authorization.
- Read [GLOSSARY.md](GLOSSARY.md) when changing work terminology and the
  [work-continuity thesis](docs/work-continuity-thesis.md) when designing execution.
  Browser/host and native/PROTOCOL.md contain the current implementation contracts.

# Interface and validation

Voice is the primary input, with optional typing. Adapt to available space and
input capabilities; retain drafts and focus through layout changes. Apply accessible
light/dark materials, reduced-motion/transparency fallbacks and safe areas.

Check the affected public interfaces and real browser behavior. Mutation checks
must fail the intended behavioral assertion; compiler errors, setup failures and
timeouts do not count. Keep fixture evidence distinct from real peers, authenticated
provider behavior and physical-device validation. Package scripts and
[the browser workflow](.github/workflows/browser.yml) define runnable checks.

# Releases

Publish checked browser assets to the existing Shared Workspace HTTPS origin.
Preserve its path, manifest identity and storage format unless a migration is part
of the change. Use isolated profiles and synthetic data for release verification.
Keep credentials, invitations and workspace content out of public artifacts.

Browser releases update this application only. Toolkit's separate repository owns
its Windows Downloads copy, ZIP and reload procedure; run that procedure only for
changes to Toolkit. Native Shared Workspace builds retain their own platform and
signing checks and are not a requirement for using the website.
