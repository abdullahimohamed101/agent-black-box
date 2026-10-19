#!/usr/bin/env bash
# Landing page checks (Phase 20, AC11): `make site-check` locally, CI job `site-check`. Stack-agnostic: they run on a BUILT output
# directory, whatever produced it.
#   scripts/check-site.sh [DIR] [--static] [--release]
#     DIR        built static output with an index.html (default: site/, the plain-HTML baseline)
#     --static   skip the browser (no Playwright needed)
#     --release  also fail on [CONFIRM] markers still in the output
#   SITE_BUILD_CHECK="cmd"   run before the checks to prove the output is fresh (default for site/: node site/build.mjs --check)
#   SITE_BUDGET_KB=N         size budget excluding fonts (default 250, 0 disables)
#   SITE_SHOTS_OUT=DIR       save full-page screenshots of every viewport and scheme there (not committed)
# The browser step serves DIR under /agent-black-box/ and needs `pnpm install` (apps/web's Playwright and axe) and Google Chrome.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
dir=""
static_only=false
release=()
for a in "$@"; do
  case "$a" in
    --static) static_only=true ;;
    --release) release=(--release) ;;
    -*) echo "unknown argument: $a" >&2; exit 2 ;;
    *) dir="$a" ;;
  esac
done
if [ -z "$dir" ]; then
  dir=site
  : "${SITE_BUILD_CHECK:=node site/build.mjs --check}"
fi
[ -f "$dir/index.html" ] || { echo "no $dir/index.html" >&2; exit 2; }

repo_url="$(node --input-type=module -e "import { REPO_URL } from './site/repo-url.mjs'; console.log(REPO_URL)")"

if [ -n "${SITE_BUILD_CHECK:-}" ]; then
  echo "== output is fresh: $SITE_BUILD_CHECK"
  bash -c "$SITE_BUILD_CHECK"
fi

echo "== static checks on $dir"
python3 scripts/check_site.py "$dir" --repo-url "$repo_url" --budget-kb "${SITE_BUDGET_KB:-250}" ${release[@]+"${release[@]}"}

if [ "$static_only" = false ]; then
  echo "== browser checks (sub-path /agent-black-box/, three widths, light and dark)"
  node scripts/check_site_browser.mjs "$dir"
fi
echo "site check passed"
