#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
probe_target="${PROBE_TARGET_DIR:-/tmp/browser-iroh-probe-target}"
probe_bindgen="${WASM_BINDGEN:-wasm-bindgen}"
RUSTFLAGS='--cfg getrandom_backend="wasm_js"' cargo build --locked --release \
  --target wasm32-unknown-unknown --target-dir "$probe_target" -j "${BUILD_JOBS:-4}"
"$probe_bindgen" --target web --out-dir pkg \
  "$probe_target/wasm32-unknown-unknown/release/browser_iroh_probe.wasm"
