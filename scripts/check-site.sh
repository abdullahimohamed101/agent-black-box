#!/usr/bin/env bash
# Landing page checks (Phase 20, AC11, brief section 9). `make site-check` locally, CI job `site-check`.
#   scripts/check-site.sh [--only NAME]... [--release] [--static] [DIR]
#     (no DIR)    build the Next export (site/out, deployed under /agent-black-box/, plus site/out-root for "/", plus a flag-on
#                 export for the claims check) and check it
#     DIR         check an already-built export (any stack with an index.html under /agent-black-box/); no build
#     --only NAME run one subcommand (repeatable): build fixture commands images links size claims nojs copy gallery network
#                 a11y motion perf shots planted
#     --static    only the subcommands that need no browser
#     --release   also fail while any [CONFIRM ...] marker remains in the export
#   SHOTS_DIR=docs/screenshots/site   `shots` writes site-<width>-<scheme>.png there (unset in CI: nothing is written)
#   ABB_RELEASE_CLAIMS=1                 build the page with the publication claims on (G2); `claims` then checks that export too
# The browser subcommands serve the export on a loopback port under /agent-black-box/ (and "/" for network) and need `pnpm install`
# (apps/web's Playwright and axe) and Chromium (Google Chrome is used when present).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

ALL=(build fixture commands images links size claims nojs copy gallery network a11y motion perf shots)
STATIC=(build fixture commands images links size claims)
only=(); dir=""; static_only=false; release=()
while [ $# -gt 0 ]; do
  case "$1" in
    --only) only+=("$2"); shift ;;
    --static) static_only=true ;;
    --release) release=(--release) ;;
    -*) echo "unknown argument: $1" >&2; exit 2 ;;
    *) dir="$1" ;;
  esac
  shift
done
if [ ${#only[@]} -gt 0 ]; then steps=("${only[@]}"); elif [ "$static_only" = true ]; then steps=("${STATIC[@]}"); else steps=("${ALL[@]}"); fi

BASE=/agent-black-box
REPO_URL="$(node --input-type=module -e "import { REPO_URL } from './site/repo-url.mjs'; console.log(REPO_URL)")"
built_here=false
if [ -z "$dir" ]; then dir="$root/site/out"; built_here=true; else dir="$(cd "$dir" && pwd)"; fi
ROOT_DIR="$root/site/out-root"; FLAG_DIR="$root/site/out-flag"

want() { local s; for s in "${steps[@]}"; do [ "$s" = "$1" ] && return 0; done; return 1; }
need_export() { [ -f "$dir/index.html" ] || { echo "no $dir/index.html (run without DIR to build it)" >&2; exit 2; }; }
sc() { python3 scripts/check_site.py "$1" --repo-url "$REPO_URL" --base-path "$2" --budget-kb 0 ${release[@]+"${release[@]}"}; }
br() { node scripts/site-checks.mjs "$1" "$dir" --base "$BASE"; }
step() { printf '\n== %s\n' "$*"; }

if want build; then
  step "build: inputs fresh, no colour literal outside tokens.css, then the exports"
  node site/scripts/tokens.mjs --check
  python3 scripts/check_site_literals.py site/src/styles/site.css site/src/styles/hero.css site/src/styles/trace.css site/src/styles/sections.css
  if [ "$built_here" = true ]; then
    pnpm --filter @abb/site build >/dev/null
    ABB_SITE_BASE_PATH= ABB_SITE_OUT=out-root pnpm --filter @abb/site build >/dev/null
    ABB_RELEASE_CLAIMS=1 ABB_SITE_OUT=out-flag pnpm --filter @abb/site build >/dev/null
  fi
  need_export
  sc "$dir" "$BASE"
  [ "$built_here" = true ] && { sc "$ROOT_DIR" "" >/dev/null; echo "domain-root export (ABB_SITE_BASE_PATH empty) passes the static rules too"; }
fi
if want fixture; then
  step "fixture: the hero's events validate against packages/event-schema and its figures recompute"
  uv run --project packages/event-schema python -I site/tests/validate_fixture.py
fi
if want commands; then
  step "commands: the quickstart commands are the ones in docs/quickstart.md (when that document exists)"
  node scripts/site-commands-check.mjs
fi
if want images; then
  step "images: gallery files within budget and listed in the manifest"
  node scripts/site-images.mjs --check
fi
if want links; then
  need_export
  step "links: every href and src resolves, only the repository is external, no dead anchors"
  sc "$dir" "$BASE"
fi
if want size; then
  need_export
  step "size: JavaScript, CSS, fonts, initial view, images"
  python3 scripts/check_site_size.py "$dir"
fi
if want claims; then
  need_export
  step "claims: banned words, qualifiers, status line, gated claims"
  python3 scripts/check_site_claims.py "$dir"
  if [ "$built_here" = true ] && [ -f "$FLAG_DIR/index.html" ]; then
    echo "-- the same export built with ABB_RELEASE_CLAIMS=1"
    python3 scripts/check_site_claims.py "$FLAG_DIR"
    python3 scripts/check_site.py "$FLAG_DIR" --repo-url "$REPO_URL" --base-path "$BASE" --budget-kb 0 >/dev/null
  fi
fi
if want planted; then
  step "planted: each check fails on a violation planted in a copy of the export"
  need_export
  bash scripts/check-site-planted.sh "$dir"
fi

if [ "$static_only" = false ] || [ ${#only[@]} -gt 0 ]; then
  for s in nojs copy gallery network a11y motion perf shots; do
    want "$s" || continue
    need_export
    step "$s"
    if [ "$s" = network ] && [ "$built_here" = true ]; then SITE_ROOT_DIR="$ROOT_DIR" br network; else br "$s"; fi
  done
fi
echo
echo "site check passed (${steps[*]})"
