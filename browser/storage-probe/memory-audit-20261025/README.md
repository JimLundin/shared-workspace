# Turso browser memory: runtime audit, 25 October 2026

**250 MiB is the published Turso wrapper's initial logical WebAssembly memory
size, not an unavoidable 250 MiB resident-RAM requirement.** The same Turso 0.8.3
binary ran with a disposable wrapper lowered to its 63.0625 MiB import minimum.
That substantially reduced logical capacity, but saved about **11.5 MiB of startup
PSS** in this Linux Chromium experiment. The application still uses unmodified
upstream Turso.

This audit distinguishes source settings, running `Memory.buffer.byteLength`,
browser process residency, and asset size. It measures the database separately
from the complete workspace host; it does not measure iPhone Safari.

## Controlled startup comparison

Five fresh browser processes per variant, order rotated between repetitions.
All variants use the same page and dedicated application worker. Both databases
use OPFS with their own I/O worker, `STRICT` tables, WAL, `synchronous=FULL`,
exclusive locking, 4 KiB pages, and `cache_size=-2000`. Actual PRAGMA values are
recorded, rather than assuming requested settings took effect. Garbage collection
was not forced or disabled.

| Variant | Initial WASM buffer | Buffer after small database opens | Median process-family PSS | PSS above empty app | Median summed RSS |
| --- | ---: | ---: | ---: | ---: | ---: |
| Empty app + worker | None | None | 179.6 MiB | — | 423.5 MiB |
| Turso 0.8.3, published wrapper | 250 MiB | 264.125 MiB | 260.2 MiB | 80.7 MiB | 506.3 MiB |
| Same Turso binary, experimental smaller wrapper | 63.0625 MiB | 77.1875 MiB | 248.8 MiB | 69.2 MiB | 495.1 MiB |
| Official SQLite WASM 3.54.1-build1 | 8 MiB | 8 MiB | 207.7 MiB | 28.2 MiB | 454.7 MiB |

Evidence: [all 20 startup observations](evidence/startup-linux-chromium.json) and
[medians, ranges, and baseline differences](evidence/startup-summary.json).
The Turso-versus-patched startup PSS difference is approximately 11.46 MiB, while
the initial logical-size difference is 186.9375 MiB. Reducing a logical-memory
number therefore must not be advertised as an equal RAM saving.

The comparison uses actual `@tursodatabase/database-wasm@0.8.3` and official
`@sqlite.org/sqlite-wasm@3.54.1-build1`, with a checked npm lockfile. It does not use
libSQL, a cloud database, or an in-memory storage fallback. This is a matched SQL
workload comparison, not a claim that the engines have identical features or
internal designs. SQLite's engine memory is non-shared; Turso's is shared. Both
use the same isolation headers required by their OPFS implementations.

## What each memory figure means

- A WebAssembly page is 65,536 bytes. `initial:4000` requests 262,144,000 bytes
  (250 MiB) of initially addressable linear memory. `maximum:65536` allows growth
  up to 4 GiB. Neither number is a measurement of physical residency.
- The constructor observer records the actual initial
  `WebAssembly.Memory.buffer.byteLength`, then reads it again after database
  initialization and each workload step. It does not alter the constructor's
  arguments. Turso's ordinary startup grew memory by 14.125 MiB in both variants.
- The binary imports shared `env.memory` with a minimum of 1,009 pages. That
  63.0625 MiB minimum is independently established by the earlier
  [binary import audit](../evidence/footprint.json), and the runtime test exercises
  that minimum. It is a binary compatibility requirement, not a RAM measurement.
- Linux `/proc/<pid>/smaps_rollup` supplies RSS, PSS, and private-page totals.
  Only descendants of the audit's uniquely identified Chromium process are
  included. Summed RSS counts shared pages repeatedly across processes. PSS
  apportions them, so it is more useful for the aggregate comparison. Baseline
  subtraction is still an approximation; this was not an otherwise idle,
  dedicated benchmark machine. The raw per-process roles and values are retained.
- Virtual address-space size includes large V8 cages, guard regions, and other
  browser reservations. The report records it for completeness, but it is not
  used as a RAM estimate. V8 also uses the term *commit* for making initial pages
  accessible; OS commit accounting must not be equated with pages currently
  resident in physical memory.
- The Turso WASM file is 11,613,778 bytes, or 3,963,233 bytes with this audit's gzip
  settings. The official SQLite WASM file is 879,084 bytes, or 407,455 bytes gzip.
  Those are individual engine assets, not complete application downloads or RAM
  sizes. Both variants of Turso retain the identical binary SHA-256
  `2905104bf0d1e9e3b17faf474bd61d541f7b35b02477a3022dc728f7f26499b9`.

## Lower-memory experiment and workload

The only engine-related alteration is a Vite transform in `run.mjs` for the
`turso-minimum` variant:

```diff
- initial: 4000,
+ initial: 1009,
```

It applies to an isolated, temporary build of the exact pinned
`database-wasm-common/dist/index.js`, checks that source's hash before transforming,
and preserves `maximum:65536`, `shared:true`, and the WASM binary. The npm package,
workspace host, and released browser application are not edited. The harness
verifies that the installed wrapper source remains unchanged and records each
transform application. Vite processes the common source in the application and
I/O-worker build graphs.

The durability/growth scenario performs the same work on each database:

1. Create real `STRICT` tables and commit a random identity seed plus sample note.
2. Require an invalid TEXT write to an INTEGER column to fail.
3. Commit 128 additional 8 KiB notes and update the original note.
4. Write and verify successive 1, 8, and 32 MiB BLOB values through parameterized
   database statements, observing automatic memory growth.
5. Reload the page, then terminate and relaunch the entire browser with the same
   profile. Require the same identity fingerprint, sample revision, note count,
   and payload length to survive. The private seed is never written to reports.

This intentionally includes large individual BLOBs to observe growth. They exceed
the workspace host's 128 KiB document and 7 MiB retained-history limits; these
stress observations must not be presented as its normal note-writing cost.
Memory after the workload is not an instrumented instantaneous peak. Reload and
browser restart may reclaim different amounts, and no forced GC equalizes them.

The [matched workload report](evidence/workload-immediate-reload.json) preserves
both successes and failures. Both Turso variants passed typing, writes, immediate
page reload, and full browser restart with the same identity and stored content.
Automatic linear-memory growth after the 32 MiB BLOB reached 542.125 MiB with
the default wrapper and 355.1875 MiB with the smaller wrapper. Their observed
process-family PSS at that point was about 1,497 and 1,533 MiB respectively: the
smaller initial setting did not establish a large-workload RAM saving.

SQLite completed the same writes and verification, with an observed 68.875 MiB
linear buffer and about 591 MiB process-family PSS after the 32 MiB value. Its
immediate page reload then failed with `SQLITE_CANTOPEN`; that failure is retained
and is not relabeled as a durability pass. It may involve OPFS owner handoff, but
that cause has not been established. These single-run stress observations are
separate from the five-run startup comparison.

One [explicit-close follow-up](evidence/sqlite-graceful-close-followup.json), run
with `--only=sqlite --repetitions=1 --graceful-close`, also hit `SQLITE_CANTOPEN`
after reload. The audit stops there instead of changing settings or retrying
until success. SQLite is therefore a verified startup/write memory comparison
here, not a verified matching reload/restart baseline. That limitation does not
invalidate its measured startup figures or establish a general SQLite durability
problem. The original exploratory run used SQLite's different default page/cache
settings and is not used as the matched comparison.

## Recommendation and support status

Keep the application on unmodified upstream Turso for now. The experiment shows
that 250 MiB is not a hard engine floor, but a text substitution in a generated
dependency is not a satisfactory production configuration mechanism. Its observed
startup RAM saving is much smaller than its logical-size reduction.

The current stable release is still 0.8.3. The published 0.9.0-pre.1 build and
current main source retain the same default; no supported Turso consumer memory
option was found. `setupMainThread(wasmFile, factory)` and `connect` do not expose
one. The default traces to napi-rs producer-side configuration history, not a
documented Turso assertion that every database needs 250 MiB resident RAM.
See [the primary-source research](upstream-sources.md) for exact revisions,
upstream discussions, specifications, and accounting references.

A useful next step is a supported upstream initial-memory parameter or an
explicitly maintained upstream build change, followed by host-level regression
tests and Safari/iPhone/Windows measurements. The smaller wrapper has only this
Linux Chromium evidence; it does not establish acceptable phone lifecycle,
eviction, resource pressure, or long-running behavior. The larger-BLOB behavior
also merits profiling independently of the initial-memory setting.

## Reproduce

Run from this directory with Node 24 and the pinned dependencies. Browser downloads
are the standard Playwright installation; no database server is involved.

```sh
npm ci
npx playwright install chromium
npm run audit -- --repetitions=5 --startup-only
npm run audit -- --repetitions=1
```

The full-workload command runs the durability/growth workload once for each
engine and the smaller-wrapper experiment. `--only=turso-minimum` restricts a run
to that variant. `--graceful-close` explicitly closes the database before page
reload and browser shutdown; it is a different lifecycle experiment, not a retry
that silently changes the immediate-reload test. Builds and browser profiles live in a temporary directory and
are removed afterward. The HTTP server binds only `127.0.0.1` on an ephemeral
port, serves isolated static assets, and closes at completion. Nothing is
published or exposed to the phone. JSON reports are written under ignored
`artifacts/`; reviewed results are copied to `evidence/`.

The measurements use Playwright's Chromium headless shell 156.0.8078.4 on Linux.
They do not measure a user's existing browser session, battery consumption,
physical iPhone behavior, or the complete distributed application.
