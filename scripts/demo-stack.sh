#!/usr/bin/env bash
# A private, demo-seeded Agent BlackBox stack for recording the demo video and screenshots (Phase 20, G1): a dedicated database
# (abb_demo_video on the Postgres that ABB_DATABASE_URL names), an API, a worker and a PRODUCTION build of apps/web, each on its own
# port, then `scripts/demo.sh` against it. It never touches your dev stack: it needs its own ports and its own database name, and
# it refuses to start when one is taken.
#
#   scripts/demo-stack.sh up      create the database, start API + worker + web, record the demo; prints the recording env
#   scripts/demo-stack.sh down    stop what `up` started (the database is left in place; DROP it yourself if you want it gone)
#   scripts/demo-stack.sh env     print the variables scripts/record-demo.mjs needs (the session token is read from a 0600 file)
#
# Environment: ABB_DATABASE_URL (a Postgres server URL; the database name in it is replaced; .env is used only when unset),
# DEMO_STACK_API_PORT (8180), DEMO_STACK_WEB_PORT (3180), DEMO_STACK_DB_NAME (abb_demo_video), DEMO_STACK_DIR (video-out/.stack).
# Build the web app first: `pnpm --filter @abb/web build`.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
DIR="${DEMO_STACK_DIR:-$root/video-out/.stack}"; mkdir -p "$DIR"
API_PORT="${DEMO_STACK_API_PORT:-8180}"; WEB_PORT="${DEMO_STACK_WEB_PORT:-3180}"
API="http://localhost:$API_PORT"; WEB="http://localhost:$WEB_PORT"
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "DEMO-STACK FAILED: $*" >&2; exit 1; }
kill_tree() {
  local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done
  kill "$1" 2>/dev/null || true
  for _ in 1 2 3 4 5 6; do kill -0 "$1" 2>/dev/null || return 0; sleep 0.5; done
  kill -9 "$1" 2>/dev/null || true  # uvicorn waits for open connections; a recording stack has nothing to lose
}

down() {
  if [ -f "$DIR/pids" ]; then while read -r p; do [ -n "$p" ] && kill_tree "$p"; done <"$DIR/pids"; rm -f "$DIR/pids"; fi
  [ ! -f "$DIR/demo/waiters.pid" ] || { while read -r p; do [ -n "$p" ] && kill_tree "$p"; done <"$DIR/demo/waiters.pid"; rm -f "$DIR/demo/waiters.pid"; }
  echo "stopped"
}
print_env() {
  echo "ABB_VIDEO_BASE_URL=$WEB ABB_VIDEO_PROFILE=demo ABB_VIDEO_SESSION_FILE=$DIR/session"
}
case "${1:-}" in
  down) down; exit 0 ;;
  env) print_env; exit 0 ;;
  up) ;;
  *) echo "usage: scripts/demo-stack.sh up|down|env" >&2; exit 2 ;;
esac

if [ -z "${ABB_DATABASE_URL:-}" ]; then set -a; . ./.env; set +a; fi
DB_NAME="${DEMO_STACK_DB_NAME:-abb_demo_video}"
export ABB_DATABASE_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
export ABB_ENVIRONMENT=development ABB_ALLOW_DEV_SESSIONS=1 ABB_WEB_ORIGIN="$WEB" ABB_API_INTERNAL_URL="$API"
export ABB_SUMMARY_DEBOUNCE_SECONDS=1 ABB_FINDINGS_DELAY_SECONDS=1 ABB_ARTIFACT_DIR="$DIR/artifacts"
for u in "$API/readyz" "$WEB"; do curl -sf --max-time 2 "$u" >/dev/null 2>&1 && fail "something is already serving $u; set DEMO_STACK_API_PORT / DEMO_STACK_WEB_PORT"; done
[ -f apps/web/.next/BUILD_ID ] || fail "apps/web is not built: pnpm --filter @abb/web build"
down >/dev/null

step "create database $DB_NAME if missing, migrate to head"
(cd apps/api && ADMIN_URL="${ABB_DATABASE_URL%/*}/postgres" DB_NAME="$DB_NAME" uv run python - <<'PY'
import asyncio, os, asyncpg
async def main():
    url = os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    if not await conn.fetchval("select 1 from pg_database where datname=$1", os.environ["DB_NAME"]):
        await conn.execute(f'create database "{os.environ["DB_NAME"]}"')
    await conn.close()
asyncio.run(main())
PY
uv run alembic upgrade head >/dev/null 2>&1)

step "start API (:$API_PORT), worker and the built web app (:$WEB_PORT)"
: >"$DIR/pids"
(cd apps/api && exec uv run uvicorn abb_api.main:app_from_env --factory --port "$API_PORT") >"$DIR/api.log" 2>&1 & echo $! >>"$DIR/pids"
(cd apps/api && exec uv run python -m abb_api.worker) >"$DIR/worker.log" 2>&1 & echo $! >>"$DIR/pids"
(cd apps/web && exec pnpm exec next start --port "$WEB_PORT") >"$DIR/web.log" 2>&1 & echo $! >>"$DIR/pids"
for _ in $(seq 1 80); do curl -sf "$API/readyz" >/dev/null && curl -sf "$WEB" >/dev/null && break; sleep 0.5; done
curl -sf "$API/readyz" >/dev/null || { down; fail "API did not become ready: $(tail -5 "$DIR/api.log")"; }
curl -sf "$WEB" >/dev/null || { down; fail "web did not become ready: $(tail -5 "$DIR/web.log")"; }

step "record the demo (scripts/demo.sh) against the private stack"
mkdir -p "$DIR/demo"
if ! DEMO_API_URL="$API" DEMO_WEB_URL="$WEB" DEMO_KEY_FILE="$DIR/demo-key" DEMO_STATE_DIR="$DIR/demo" DEMO_RUNS_FILE="$DIR/runs.txt" \
     scripts/demo.sh >"$DIR/demo.log" 2>&1; then tail -20 "$DIR/demo.log" >&2; down; fail "the demo failed"; fi
tail -3 "$DIR/demo.log"

step "mint a development session for the demo owner (0600 file, never printed)"
(umask 077; cd apps/api && uv run python -m abb_api.cli create-session --email owner@local.test --hours 6 2>/dev/null >"$DIR/session")
[ -s "$DIR/session" ] || { down; fail "could not mint a session"; }
echo "stack is up: web $WEB, api $API. Recording env: $(print_env)"
echo "stop it with: scripts/demo-stack.sh down"
