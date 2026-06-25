#!/usr/bin/env bash
# Bundles+minifies the dashboard's CSS/JS with esbuild and writes a
# content-hashed manifest.json. Run during `docker build` (ephemeral Node
# stage) or locally with `npx esbuild` available — never needed at runtime.
set -euo pipefail
cd "$(dirname "$0")"

rm -rf dist
mkdir -p dist

# No --bundle for the JS: dashboard.js has no imports (plain script, relies
# on top-level functions being global for the HTML's inline onclick="..."
# handlers) — esbuild's --bundle wraps output in an IIFE that would hide
# them from global scope and break every onclick handler in the page.
npx esbuild src/dashboard.js --minify --outfile=dist/dashboard.js
npx esbuild src/dashboard.css --bundle --minify --outfile=dist/dashboard.css

JS_HASH=$(sha256sum dist/dashboard.js | cut -c1-10)
CSS_HASH=$(sha256sum dist/dashboard.css | cut -c1-10)

mv dist/dashboard.js "dist/dashboard.${JS_HASH}.js"
mv dist/dashboard.css "dist/dashboard.${CSS_HASH}.css"

cat > dist/manifest.json <<EOF
{
  "js": "dashboard.${JS_HASH}.js",
  "css": "dashboard.${CSS_HASH}.css"
}
EOF

echo "Built dist/dashboard.${JS_HASH}.js and dist/dashboard.${CSS_HASH}.css"
