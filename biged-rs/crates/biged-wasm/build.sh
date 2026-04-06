#!/bin/bash
# Build WASM bundle for browser deployment.
# Requires: wasm-pack (cargo install wasm-pack)
#
# Output: pkg/ directory with biged_wasm.js + biged_wasm_bg.wasm
# Deploy: copy index.html + pkg/ to the Rust server's static dir

set -e

cd "$(dirname "$0")"

echo "Building WASM..."
wasm-pack build --target web --out-dir pkg --release

echo ""
echo "Output: $(du -sh pkg/ | cut -f1) in pkg/"
echo "Deploy: copy index.html + pkg/ to serve from Rust HTTP server"
echo ""
echo "Quick test: python -m http.server 8080"
