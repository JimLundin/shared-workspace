# Turso browser memory: upstream source audit

Checked 2026-10-25. This note covers package/source evidence and memory-accounting semantics; runtime measurements belong in the accompanying experiment results.

## Finding

`@tursodatabase/database-wasm` 0.8.3 initializes **250 MiB of addressable WebAssembly linear memory** through its JavaScript wrapper. That number alone does **not** establish 250 MiB of resident RAM, live database allocations, or incremental browser-process memory. The current stable package, published prerelease, and inspected upstream main expose no supported consumer option to reduce that initial size. The exact 4,000-page default predates Turso: it appears in napi-rs loader generation in April 2024. Sources and qualifications follow.

## Published versions and actual initialization

The npm registries for both [database-wasm](https://registry.npmjs.org/@tursodatabase/database-wasm) and [database-wasm-common](https://registry.npmjs.org/@tursodatabase/database-wasm-common) reported these dist-tags when checked:

| Tag | Version | npm `gitHead` |
| --- | --- | --- |
| `latest` | `0.8.3` | `607bff9fd0e547a25a97b3bf06e83bcc5d99d53c` |
| `next` | `0.9.0-pre.1` | `a820079e8ee03ed900e871821096529e2a6ca643` |

At the exact stable release commit, [`setupMainThread`](https://github.com/tursodatabase/turso/blob/607bff9fd0e547a25a97b3bf06e83bcc5d99d53c/bindings/javascript/packages/wasm-common/index.ts#L419-L446) constructs:

```ts
new WebAssembly.Memory({
    initial: 4000,
    maximum: 65536,
    shared: true,
})
```

The installed `node_modules/@tursodatabase/database-wasm-common/dist/index.js:403–407` contains this same descriptor. I also read the actual [0.9.0-pre.1 wasm-common tarball](https://registry.npmjs.org/@tursodatabase/database-wasm-common/-/database-wasm-common-0.9.0-pre.1.tgz): its `dist/index.js:403–407` retains the identical values. Its declaration remains `setupMainThread(wasmFile: ArrayBuffer, factory: () => Worker): Promise<any>`.

The [WebAssembly core specification](https://webassembly.github.io/spec/core/exec/runtime.html#memory-instances) defines a page as 65,536 bytes. Therefore:

- Initial logical extent: `4000 × 65536 = 262144000 bytes = 250 MiB`.
- Declared maximum logical extent: `65536 × 65536 = 4294967296 bytes = 4 GiB`.

These are descriptor limits, not measured residency. The maximum is not a claim that 4 GiB of physical RAM has been allocated.

The [stable entrypoint](https://github.com/tursodatabase/turso/blob/607bff9fd0e547a25a97b3bf06e83bcc5d99d53c/bindings/javascript/packages/wasm/index-default.ts#L1-L17) fetches/initializes the module at top level, before any `connect()` call. The [worker initialization](https://github.com/tursodatabase/turso/blob/607bff9fd0e547a25a97b3bf06e83bcc5d99d53c/bindings/javascript/packages/wasm-common/index.ts#L350-L378) receives the supplied `wasmMemory`. This is one shared memory backing store for that runtime and its worker, not evidence of another independent 250 MiB store per worker or database connection.

## Supported configuration

For the Turso package consumer, I found **no initial/maximum-memory parameter**:

- [`DatabaseOpts` in 0.8.3](https://github.com/tursodatabase/turso/blob/607bff9fd0e547a25a97b3bf06e83bcc5d99d53c/bindings/javascript/packages/common/types.ts#L13-L24) contains read-only/file-existence, timeout, tracing, experimental-feature, and encryption options. It has no linear-memory setting. The [`next` version's interface](https://github.com/tursodatabase/turso/blob/a820079e8ee03ed900e871821096529e2a6ca643/bindings/javascript/packages/common/types.ts#L13-L24) is the same in this respect.
- The wrapper creates its memory internally, rather than accepting a caller-owned `WebAssembly.Memory` or descriptor. The [inspected main revision `aa93d669…`](https://github.com/tursodatabase/turso/blob/aa93d669c7940d13e3f124138db0adb77133636e/bindings/javascript/packages/wasm-common/index.ts#L419-L430) still does this.
- napi-rs itself has producer/build settings `napi.wasm.initialMemory` and `maximumMemory`, introduced in [PR #2038](https://github.com/napi-rs/napi-rs/pull/2038). These are **not Turso `connect()` options**. Turso's [build script](https://github.com/tursodatabase/turso/blob/607bff9fd0e547a25a97b3bf06e83bcc5d99d53c/bindings/javascript/packages/wasm/package.json#L40-L51) uses its own TypeScript wrapper and removes generated WASI loader files; that custom wrapper still contains the literals above. Adding napi config to an application does not parameterize it.

There is consequently no demonstrated, supported drop-in package configuration or version upgrade here that lowers the 250 MiB initial extent. A wrapper patch or custom loader could be an experiment without changing the Rust engine binary, but it is not an exposed Turso API, and a passing small workload would not establish a generally safe minimum. This audit did not modify the package or engine.

## Where 4,000 pages came from

1. napi-rs [PR #2035](https://github.com/napi-rs/napi-rs/pull/2035), merged 2024-04-13 as [`16f0d492…`](https://github.com/napi-rs/napi-rs/commit/16f0d49284b8d35174de72ee7b05fc10142f99b6), raised generated-loader initial memory from 1,024 to 16,384 pages and maximum from 10,240 to 65,536 pages.
2. A [review comment](https://github.com/napi-rs/napi-rs/pull/2035#discussion_r1564068625) objected to the 1 GiB initial `SharedArrayBuffer`, noted that memory can grow, and asked about multiple modules in one page. The maintainer [replied](https://github.com/napi-rs/napi-rs/pull/2035#discussion_r1564161413), “Good point. Let's make it configurableble”. This is historical rationale for configurability, not proof about physical residency.
3. napi-rs [PR #2038](https://github.com/napi-rs/napi-rs/pull/2038), merged 2024-04-15 as [`cc937e1d…`](https://github.com/napi-rs/napi-rs/commit/cc937e1dbabcbc05bcb026126f82fe842554891d), introduced configurable values and the [`initialMemory = 4000`, `maximumMemory = 65536` defaults](https://github.com/napi-rs/napi-rs/blob/cc937e1dbabcbc05bcb026126f82fe842554891d/cli/src/api/templates/load-wasi-template.ts#L1-L5). Its [config comment](https://github.com/napi-rs/napi-rs/blob/cc937e1dbabcbc05bcb026126f82fe842554891d/cli/src/utils/config.ts#L45-L57) incorrectly calls 4,000 pages “256MiB”; the arithmetic is 250 MiB.
4. Turso [PR #2307](https://github.com/tursodatabase/turso/pull/2307), replacing custom WASM bindings with napi-rs, introduced the [same memory descriptor](https://github.com/tursodatabase/turso/blob/98bec9868bbe5d77f9bdd817d387bdde51bdca5f/bindings/javascript/turso.wasi-browser.js#L18-L22) on 2025-07-28. Its stated rationale was reducing maintenance overhead and complexity by compiling the existing JavaScript bindings through napi v3. Later restructuring preserved the value; the browser-to-wasm package rename is [`57c279e2…`](https://github.com/tursodatabase/turso/commit/57c279e2b0cdda0c4c6e608fa4abd3960d0810dd).

I found no measured justification for exactly 4,000 pages in those Turso or napi-rs PR bodies/review comments, and no Turso issue establishing it as a minimum necessary for ordinary database use. The narrower, supported conclusion is that Turso adopted a historical napi-rs loader default. Current [napi-rs documentation](https://github.com/napi-rs/napi-rs/blob/6c1fdc1e34e7a0b38cdd0b0ab3538aa4bd586e83/cli/docs/wasi.md#L497-L537) calls it a historical default and explains the general distinction between one shared pool for threaded workers and threadless memory; that general explanation does not prove Turso needs all 4,000 pages.

## Logical memory, reservation, commitment, and resident RAM

| Quantity | What the primary sources establish | What it does not establish |
| --- | --- | --- |
| `memory.buffer.byteLength` | Size of the currently exposed linear-memory buffer; the [JS API specification](https://webassembly.github.io/spec/js-api/index.html#memories) assigns buffer byte length from the underlying memory block. The [threads specification](https://webassembly.github.io/threads/js-api/index.html#memories) supplies a shared backing block/`SharedArrayBuffer`. | Live Rust allocations, touched pages, RSS, or total browser cost. |
| Virtual address reservation | V8 [reserves inaccessible address space](https://github.com/v8/v8/blob/5f114fe1b4ecd7d6a11b3c9fb3326901aff27be4/src/objects/backing-store.cc#L338-L378) based on capacity and its guard-region policy. | Physical RAM use; reservation can be larger than the logical memory because of guard regions. |
| Accessible/committed initial extent | V8 then [makes the initial pages readable/writable](https://github.com/v8/v8/blob/5f114fe1b4ecd7d6a11b3c9fb3326901aff27be4/src/objects/backing-store.cc#L382-L397), explicitly calling this “Commit the initial pages”. Its [Windows permission implementation](https://github.com/v8/v8/blob/5f114fe1b4ecd7d6a11b3c9fb3326901aff27be4/src/base/platform/platform-win32.cc#L1056-L1070) uses `MEM_COMMIT`. | Immediate physical residency. Microsoft documents that [`VirtualAlloc(MEM_COMMIT)` allocates physical pages only when addresses are accessed](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualalloc). Thus “only reserved” also loses a meaningful distinction. |
| Resident RAM / working set | Microsoft defines the [working set](https://learn.microsoft.com/en-us/windows/win32/memory/working-set) as virtual pages currently resident in physical memory. Linux documents [RSS and PSS](https://www.kernel.org/doc/html/latest/filesystems/proc.html) separately from mapping size. | Unique incremental Turso cost without a baseline and attention to shared pages/processes. |
| Process private commitment | Windows [`PROCESS_MEMORY_COUNTERS_EX.PrivateUsage`](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters_ex) reports private commit charge, separately from `WorkingSetSize`. | A synonym for physical RAM. |

The V8 citations above identify an implementation mechanism, not the precise telemetry of an unspecified installed browser version. Browser/OS accounting must be measured on the actual target. Chrome's own [memory guide](https://developer.chrome.com/docs/devtools/memory-problems) also distinguishes OS memory footprint from the JavaScript heap; a JS-heap-only measurement is not a complete WASM footprint measurement.

For reporting the experiments, keep at least these labels separate: initial/current linear-memory bytes, process resident/working-set change, and (where available) process commit/private-byte change. Use a control baseline and the same browser/build/platform. Do not sum an identical shared backing store once per worker, and do not convert a `byteLength` observation into “250 MiB RAM required”.
