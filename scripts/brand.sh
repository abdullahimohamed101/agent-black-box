#!/usr/bin/env bash
# Regenerate the brand assets in site/assets from brand/pulse-mark.svg (see scripts/brand.mjs), or `--check` that the web
# app's copy of the mark equals the source. Needs `pnpm install` (Playwright, already an apps/web dev dependency) and its Chromium.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
[ -d apps/web/node_modules/@playwright/test ] || { echo "brand: run 'pnpm install' first (Playwright lives in apps/web)" >&2; exit 2; }
exec node scripts/brand.mjs "$@"
