#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
host_target="${HOST_WASM_TARGET_DIR:-/tmp/browser-iroh-probe-target}"
host_bindgen="${WASM_BINDGEN:-wasm-bindgen}"
RUSTFLAGS='--cfg getrandom_backend="wasm_js"' cargo build --locked --release \
  --target wasm32-unknown-unknown --target-dir "$host_target" -j "${BUILD_JOBS:-4}"
"$host_bindgen" --target web --out-dir pkg --out-name browser_workspace_host \
  "$host_target/wasm32-unknown-unknown/release/browser_workspace_host.wasm"
