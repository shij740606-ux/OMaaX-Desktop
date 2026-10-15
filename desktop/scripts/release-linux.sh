#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo 'This release script requires Linux x86_64.' >&2
  exit 1
fi
export BIODSH_TARGETS=linux-x64
export ELECTRON_SKIP_BINARY_DOWNLOAD=1
export CI=true
node scripts/fetch-node.mjs
export PATH="$PWD/resources/node/linux-x64/bin:$PATH"
node scripts/fetch-uv.mjs
npm ci --no-audit --no-fund
node scripts/stage-dsh.mjs
npm run typecheck
node --test scripts/download-skill.test.mjs scripts/sync-skills.test.mjs
npx tauri build --bundles deb
