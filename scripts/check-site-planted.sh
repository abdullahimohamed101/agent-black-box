#!/usr/bin/env bash
# Each site check must FAIL on a violation planted in a scratch copy of the export (H11). usage: check-site-planted.sh EXPORT_DIR
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
src="$(cd "$1" && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
fresh() { rm -rf "$tmp/x"; cp -R "$src" "$tmp/x"; }
REPO="$(node --input-type=module -e "import { REPO_URL } from './site/repo-url.mjs'; console.log(REPO_URL)")"
fails=0
expect_fail() {  # expect_fail "what was planted" command...
  local what="$1"; shift
  if "$@" >"$tmp/out" 2>&1; then echo "NOT CAUGHT: $what"; fails=$((fails + 1)); else echo "caught: $what  ($(grep -m1 -E 'FAIL|fail' "$tmp/out" | cut -c1-110))"; fi
}
static() { python3 scripts/check_site.py "$tmp/x" --repo-url "$REPO" --base-path /agent-black-box --budget-kb 0 "$@"; }
css() { ls "$tmp"/x/_next/static/chunks/*.css | head -1; }

fresh; sed -i.bak 's|</main>|<img src="https://example.com/track.png" alt=""></main>|' "$tmp/x/index.html"; expect_fail "an external <img src> (links)" static
fresh; sed -i.bak 's|</main>|<button onclick="alert(1)">x</button></main>|' "$tmp/x/index.html"; expect_fail "an inline event handler (links)" static
fresh; sed -i.bak 's|</main>|<a href="#">x</a></main>|' "$tmp/x/index.html"; expect_fail "a dead # link (links)" static
fresh; printf '@keyframes plant{from{opacity:0}to{opacity:1}}.plant{animation:plant 1s}' >>"$(css)"; expect_fail "animation outside prefers-reduced-motion: no-preference (motion rule)" static
fresh; printf '.plant{transition:color 2s}' >>"$(css)"; expect_fail "a transition outside the media query (motion rule)" static
fresh; sed -i.bak 's|</main>|<p>[CONFIRM planted]</p></main>|' "$tmp/x/index.html"; expect_fail "a [CONFIRM] marker under --release" static --release
fresh; head -c 300000 /dev/urandom >"$tmp/x/assets/shots/where-it-broke-light.webp"; expect_fail "a 300 KB WebP (size)" python3 scripts/check_site_size.py "$tmp/x"
fresh; sed -i.bak 's|</main>|<p>A seamless SaaS for AI agents.</p></main>|' "$tmp/x/index.html"; expect_fail "the words SaaS and seamless (claims)" python3 scripts/check_site_claims.py "$tmp/x"
fresh; sed -i.bak 's|</main>|<p>Open source and self-hosted.</p></main>|' "$tmp/x/index.html"; expect_fail "'open source' with the release-claims flag off (claims)" python3 scripts/check_site_claims.py "$tmp/x"
fresh; sed -i.bak 's|</main>|<a href="https://github.com/o/r">Source on GitHub</a></main>|' "$tmp/x/index.html"; expect_fail "a repository link with the flag off (claims)" python3 scripts/check_site_claims.py "$tmp/x"
fresh; sed -i.bak 's|Early release\.|Production ready.|' "$tmp/x/index.html"; expect_fail "a changed status line (claims)" python3 scripts/check_site_claims.py "$tmp/x"
fresh; sed -i.bak 's|</main>|<script>fetch("http://example.invalid/x")</script><img src="http://example.invalid/p.png" alt=""></main>|' "$tmp/x/index.html"; expect_fail "a third-party request at load (network)" node scripts/site-checks.mjs network "$tmp/x"
fresh; sed -i.bak 's|id="cmd-quickstart"|id="cmd-gone"|' "$tmp/x/index.html"; expect_fail "a missing quickstart block (nojs)" node scripts/site-checks.mjs nojs "$tmp/x"
printf 'body{background:#ff00ff}' >"$tmp/lit.css"; expect_fail "a colour literal in a site stylesheet" python3 scripts/check_site_literals.py "$tmp/lit.css"
python3 - "$tmp/fx.json" <<'P'
import json, sys
d = json.load(open("site/fixtures/migrate-billing-schema.json")); d["figures"]["cost"] = "$9.9999"
json.dump(d, open(sys.argv[1], "w"))
P
expect_fail "a fixture whose figures do not add up" uv run --project packages/event-schema python -I site/tests/validate_fixture.py "$tmp/fx.json"
if [ "$fails" -gt 0 ]; then echo "$fails planted violation(s) were NOT caught"; exit 1; fi
echo "every planted violation was caught"
