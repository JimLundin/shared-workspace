import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, readdir, mkdir, writeFile } from "node:fs/promises";
import { gzipSync } from "node:zlib";

const binary = await readFile("node_modules/@tursodatabase/database-wasm/dist/turso.wasm32-wasi.wasm");
let cursor = 8;
function unsigned() {
  let result = 0;
  let shift = 0;
  for (;;) {
    const byte = binary[cursor++];
    result += (byte & 127) * 2 ** shift;
    if (byte < 128) return result;
    shift += 7;
  }
}
function name() {
  const length = unsigned();
  const value = binary.subarray(cursor, cursor + length).toString();
  cursor += length;
  return value;
}
function limits() {
  const flags = unsigned();
  const minimum = unsigned();
  const maximum = flags & 1 ? unsigned() : null;
  return { minimum_pages: minimum, maximum_pages: maximum, shared: Boolean(flags & 2) };
}
const memoryImports = [];
while (cursor < binary.length) {
  const section = binary[cursor++];
  const size = unsigned();
  const end = cursor + size;
  if (section === 2) {
    const count = unsigned();
    for (let index = 0; index < count; index++) {
      const module = name();
      const field = name();
      const kind = binary[cursor++];
      switch (kind) {
        case 0: unsigned(); break;
        case 1: cursor++; limits(); break;
        case 2: memoryImports.push({ module, field, ...limits() }); break;
        case 3: cursor += 2; break;
        case 4: cursor++; unsigned(); break;
        default: throw new Error(`Unsupported WASM import kind ${kind}`);
      }
    }
    break;
  }
  cursor = end;
}
assert.equal(memoryImports.length, 1);
const wrapper = await readFile("node_modules/@tursodatabase/database-wasm-common/dist/index.js", "utf8");
const configured = /new WebAssembly\.Memory\(\{\s*initial:\s*(\d+),\s*maximum:\s*(\d+),\s*shared:\s*true/.exec(wrapper);
assert.ok(configured, "Pinned wrapper memory configuration must remain identifiable");
const assets = [];
for (const file of await readdir("dist/assets")) {
  const value = await readFile(`dist/assets/${file}`);
  assets.push({ file, bytes: value.length, gzip_bytes: gzipSync(value).length });
}
const report = {
  engine: "@tursodatabase/database-wasm@0.8.3",
  binary_sha256: createHash("sha256").update(binary).digest("hex"),
  wasm_imports: memoryImports,
  binary_minimum_bytes: memoryImports[0].minimum_pages * 65536,
  wrapper_initial_bytes: Number(configured[1]) * 65536,
  wrapper_maximum_bytes: Number(configured[2]) * 65536,
  public_memory_configuration: false,
  interpretation: "Static binary and published wrapper configuration, not measured resident memory; engine is unmodified.",
  production_assets: assets,
};
await mkdir("artifacts", { recursive: true });
await writeFile("artifacts/footprint.json", JSON.stringify(report, null, 2) + "\n");
console.log(JSON.stringify(report, null, 2));
