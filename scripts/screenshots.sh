#!/usr/bin/env bash
# The release screenshots (Phase 20, D11): a throwaway, demo-seeded Agent BlackBox stack, a Playwright spec that photographs real
# pages light and dark at 1440x900, and the result in docs/screenshots/release/ (and nowhere else).
#
#   scripts/screenshots.sh            one pass: fresh database, seed the demo, take 12 pictures, write docs/screenshots/release/
#   scripts/screenshots.sh --verify   two complete passes on two fresh databases, compare them with a tolerant pixel diff
#                                     (scripts/screenshots-diff.mjs), and write the first pass only if they agree
#   then: node scripts/site-images.mjs   (turns the set into the landing page's WebP/PNG files)
#
# Nothing here touches your dev stack. It starts its own Postgres container (the digest-pinned image from docker-compose.yml, data in tmpfs, a random password
# that is never printed), an API, a worker and a production build of apps/web, on ports of its own, and removes all of it on exit.
# Needs Docker, uv, pnpm and Chrome. Builds apps/web when it is not built yet.
# Environment (all optional): SHOTS_PG_PORT (5462), SHOTS_API_PORT (8190), SHOTS_WEB_PORT (3190), SHOTS_OUT (docs/screenshots/release),
# SHOTS_VERIFY_ALLOW (comma list of image-name prefixes whose pass-to-pass difference is reported, not failed; default
# waterfall-multi-agent: the demo's multi-agent job runs threads, so lane order and the critical path can differ between seedings),
# SHOTS_KEEP_DIR (copy every pass there as pass1/, pass2/, to look at a difference).
# What is normalised, and why, is written at the top of apps/web/e2e/release-shots.spec.ts.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
PG_PORT="${SHOTS_PG_PORT:-5462}"; API_PORT="${SHOTS_API_PORT:-8190}"; WEB_PORT="${SHOTS_WEB_PORT:-3190}"
OUT="${SHOTS_OUT:-$root/docs/screenshots/release}"
MAX_IMAGES=12; MAX_BYTES=$((400 * 1024))
WORK="$(mktemp -d)"; PG="abb-shots-pg-$$"
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "SCREENSHOTS FAILED: $*" >&2; exit 1; }
cleanup() {
  if [ -n "${SHOTS_KEEP_DIR:-}" ]; then mkdir -p "$SHOTS_KEEP_DIR"; for d in "$WORK"/pass*; do [ -d "$d" ] && cp -R "$d" "$SHOTS_KEEP_DIR"/; done; fi
  DEMO_STACK_DIR="$WORK/stack" scripts/demo-stack.sh down >/dev/null 2>&1 || true
  docker rm -f "$PG" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

verify=0
case "${1:-}" in "") ;; --verify) verify=1 ;; *) echo "usage: scripts/screenshots.sh [--verify]" >&2; exit 2 ;; esac
command -v docker >/dev/null || fail "Docker is not installed (UNVERIFIED (env) without it)"
docker info >/dev/null 2>&1 || fail "the Docker daemon is not running"
for port in "$PG_PORT" "$API_PORT" "$WEB_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then fail "port $port is in use; set SHOTS_PG_PORT / SHOTS_API_PORT / SHOTS_WEB_PORT"; fi
done
if [ ! -f apps/web/.next/BUILD_ID ]; then step "build apps/web (production)"; pnpm --filter @abb/web build >"$WORK/build.log" 2>&1 || { tail -20 "$WORK/build.log" >&2; fail "web build"; }; fi

# One complete pass into $1: new Postgres, new database, seeded demo, the spec, everything stopped again.
one_pass() {
  local dest="$1" pw
  pw="$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
  step "throwaway Postgres on 127.0.0.1:$PG_PORT"
  docker run -d --name "$PG" -e POSTGRES_USER=abb -e POSTGRES_PASSWORD="$pw" -p "127.0.0.1:$PG_PORT:5432" \
    --tmpfs /var/lib/postgresql/data postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea >/dev/null
  for _ in $(seq 1 60); do docker exec "$PG" pg_isready -U abb >/dev/null 2>&1 && break; sleep 1; done
  docker exec "$PG" pg_isready -U abb >/dev/null 2>&1 || fail "Postgres did not become ready"
  sleep 2  # the image restarts once after its init scripts
  rm -rf "$WORK/stack"
  export ABB_DATABASE_URL="postgresql+asyncpg://abb:$pw@127.0.0.1:$PG_PORT/postgres"
  export DEMO_STACK_API_PORT="$API_PORT" DEMO_STACK_WEB_PORT="$WEB_PORT" DEMO_STACK_DB_NAME=abb_shots DEMO_STACK_DIR="$WORK/stack"
  step "demo-seeded stack (API :$API_PORT, web :$WEB_PORT, database abb_shots)"
  scripts/demo-stack.sh up >"$WORK/stack.log" 2>&1 || { tail -20 "$WORK/stack.log" >&2; fail "the stack did not come up"; }
  step "Playwright: 12 pictures into $dest"
  mkdir -p "$dest"
  (cd apps/web && SHOTS_DIR="$dest" E2E_SHOTS_BASE_URL="http://localhost:$WEB_PORT" E2E_WORKSPACE=local \
    E2E_SESSION_TOKEN="$(cat "$WORK/stack/session")" pnpm exec playwright test -c playwright.shots.config.ts) || fail "the screenshot spec failed"
  scripts/demo-stack.sh down >/dev/null
  docker rm -f "$PG" >/dev/null
  unset ABB_DATABASE_URL
}

check_budget() {  # D11: at most 12 images, each under 400 KB, 1440 px wide
  local dir="$1" n=0 f bytes width
  for f in "$dir"/*.png; do
    n=$((n + 1)); bytes="$(wc -c <"$f" | tr -d ' ')"
    [ "$bytes" -le "$MAX_BYTES" ] || fail "$(basename "$f") is $bytes bytes (limit $MAX_BYTES)"
    width="$(python3 -c 'import struct,sys; print(struct.unpack(">I", open(sys.argv[1],"rb").read(24)[16:20])[0])' "$f")"
    [ "$width" = 1440 ] || fail "$(basename "$f") is $width px wide, expected 1440"
  done
  [ "$n" -le "$MAX_IMAGES" ] || fail "$n images (limit $MAX_IMAGES)"
  echo "budget ok: $n images, each <= $((MAX_BYTES / 1024)) KB and 1440 px wide"
}

if [ "$verify" = 1 ]; then
  one_pass "$WORK/pass1"; one_pass "$WORK/pass2"
  step "compare the two passes"
  node scripts/screenshots-diff.mjs "$WORK/pass1" "$WORK/pass2" --allow="${SHOTS_VERIFY_ALLOW-waterfall-multi-agent}" || fail "the two passes differ: something varying between runs is not normalised"
  python3 - "$WORK/pass1/boxes.json" "$WORK/pass2/boxes.json" "${SHOTS_VERIFY_ALLOW-waterfall-multi-agent}" <<'PY' || fail "callout boxes differ between the two passes"
import json, sys
a, b = (json.load(open(p)) for p in sys.argv[1:3])
allow = [x for x in sys.argv[3].split(",") if x]
bad = [k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k) and not any(k.startswith(x) for x in allow)]
if bad: print("callout boxes differ for:", ", ".join(bad)); sys.exit(1)
print("callout boxes identical" + (" (except the allowed images)" if a != b else ""))
PY
  FINAL="$WORK/pass1"
else
  one_pass "$WORK/pass1"; FINAL="$WORK/pass1"
fi
check_budget "$FINAL"
step "write $OUT"
mkdir -p "$OUT"; rm -f "$OUT"/*.png
cp "$FINAL"/*.png "$OUT"/
cp "$FINAL/boxes.json" "$root/site/screenshots.boxes.json"  # where the callouts land, for site/screenshots.json
ls -l "$OUT" | sed 's/^/  /'
printf '\nSCREENSHOTS DONE (%s). Next: node scripts/site-images.mjs\n' "$([ "$verify" = 1 ] && echo "verified twice" || echo "one pass")"
