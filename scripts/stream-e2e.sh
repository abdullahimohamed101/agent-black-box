#!/usr/bin/env bash
# Live streaming E2E: a dedicated database (abb_p5), an API on :8120 and a worker started by this script, the
# built web server on :3103 reading through its proxy, and Playwright driving a real browser while
# scripts/stream_driver.py (the Python SDK and plain HTTP) writes runs. Needs `.env` (Postgres on 5433) and
# `pnpm --filter @abb/web build`. Never touches the shared dev/test databases or the compose stack.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; . ./.env; set +a
DB_NAME="${STREAM_E2E_DB_NAME:-abb_p5}"
export ABB_DATABASE_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
export ABB_SUMMARY_DEBOUNCE_SECONDS=1
PORT="${STREAM_E2E_API_PORT:-8120}"; API="http://localhost:$PORT"
WORK="$(mktemp -d)"; pids=()
cleanup() { for p in "${pids[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; rm -rf "$WORK"; }
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "STREAM-E2E FAILED: $*" >&2; exit 1; }
cd apps/api

step "create database $DB_NAME if missing, migrate to head"
ADMIN_URL="${ABB_DATABASE_URL%/*}/postgres" DB_NAME="$DB_NAME" uv run python - <<'PY'
import asyncio, os, asyncpg
async def main():
    url = os.environ["ADMIN_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    if not await conn.fetchval("select 1 from pg_database where datname=$1", os.environ["DB_NAME"]):
        await conn.execute(f'create database "{os.environ["DB_NAME"]}"')
    await conn.close()
asyncio.run(main())
PY
uv run alembic upgrade head >/dev/null 2>&1

step "start API (:$PORT) and worker"
if [ "${STREAM_E2E_MODE:-}" = bench ]; then   # many viewers on one key
  export ABB_STREAM_MAX_PER_KEY=200 ABB_STREAM_MAX_TOTAL=200
fi
uv run uvicorn abb_api.main:app_from_env --factory --port "$PORT" >"$WORK/api.log" 2>&1 & pids+=($!)
uv run python -m abb_api.worker >"$WORK/worker.log" 2>&1 & pids+=($!)
for _ in $(seq 1 40); do curl -sf "$API/readyz" >/dev/null && break; sleep 0.5; done
curl -sf "$API/readyz" >/dev/null || fail "API did not become ready: $(tail -5 "$WORK/api.log")"

step "provision a workspace, a project, a write key and a script-only read key (secrets never printed)"
suffix="$(date +%s)$RANDOM"
cli() { uv run python -m abb_api.cli "$@" 2>/dev/null; }
cli create-workspace --name "Stream E2E" --slug "stream-e2e-$suffix" >/dev/null
cli create-project --workspace "stream-e2e-$suffix" --name Demo --slug demo >/dev/null
WRITE_KEY="$(cli create-key --workspace "stream-e2e-$suffix" --project demo --scopes events:write runs:read --name stream-write)"
READ_KEY="$(cli create-key --workspace "stream-e2e-$suffix" --scopes runs:read --name stream-script-read)"
[[ "$WRITE_KEY" == abb_live_* && "$READ_KEY" == abb_live_* ]] || fail "key creation"

step "mint a development session for a workspace owner (the browser's only credential; gated by ABB_ENVIRONMENT + ABB_ALLOW_DEV_SESSIONS)"
cli add-member --workspace "stream-e2e-$suffix" --email e2e-owner@local.test --role OWNER >/dev/null
SESSION="$(ABB_ENVIRONMENT=development ABB_ALLOW_DEV_SESSIONS=1 cli create-session --email e2e-owner@local.test --hours 1)"
[ -n "$SESSION" ] || fail "could not mint a session"

if [ "${STREAM_E2E_MODE:-}" = bench ]; then
  step "fan-out benchmark: many viewers of one hot run while it ingests (scripts/bench_stream_fanout.py)"
  cd "$root"
  BENCH_API_URL="$API" BENCH_WRITE_KEY="$WRITE_KEY" BENCH_READ_KEY="$READ_KEY" \
    uv run --project apps/api python scripts/bench_stream_fanout.py
  grep -c "stream failed" "$WORK/api.log" | sed 's/^/stream failures logged by the API: /'
  exit 0
fi

step "Playwright: browser <-> web server <-> API, writers driven by scripts/stream_driver.py"
cd "$root/apps/web"
E2E_SESSION_TOKEN="$SESSION" E2E_WORKSPACE="stream-e2e-$suffix" E2E_STREAM_WRITE_KEY="$WRITE_KEY" E2E_STREAM_API_URL="$API" \
  STREAM_LATENCY_OUT="${STREAM_LATENCY_OUT:-$root/apps/web/test-results/stream-latency.json}" \
  pnpm exec playwright test e2e/stream.spec.ts
printf '\nSTREAM-E2E PASSED\n'
