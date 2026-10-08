#!/usr/bin/env bash
# Analytics E2E: a dedicated database (abb_p7), an API on :8150 and a worker started by this script, the built web
# server on :3150 reading through its proxy, and Playwright driving a real browser over runs written by
# scripts/analytics_driver.py. ANALYTICS_E2E_MODE=seed seeds the Stage A dataset (scripts/analytics_seed.py) and
# ANALYTICS_E2E_MODE=bench measures its endpoints (scripts/bench_analytics.py). Needs `.env` (Postgres on 5433) and, for the
# browser run,
# `pnpm --filter @abb/web build`. Never touches the shared dev/test databases or the compose stack.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; . ./.env; set +a
DB_NAME="${ANALYTICS_E2E_DB_NAME:-abb_p7}"
export ABB_DATABASE_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
export ABB_SUMMARY_DEBOUNCE_SECONDS=0 ABB_ANALYTICS_REFRESH_DELAY_SECONDS=0
PORT="${ANALYTICS_E2E_API_PORT:-8150}"; API="http://localhost:$PORT"
WORK="$(mktemp -d)"; pids=()
cleanup() { for p in "${pids[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; rm -rf "$WORK"; }
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "ANALYTICS-E2E FAILED: $*" >&2; exit 1; }
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

if [ "${ANALYTICS_E2E_MODE:-}" = seed ]; then
  step "seed the Stage A dataset into $DB_NAME (deterministic; replaces the 'bench' workspace) and build the rollups"
  uv run python ../../scripts/analytics_seed.py --database-url "$ABB_DATABASE_URL" \
    --runs-per-day "${SEED_RUNS_PER_DAY:-100000}" --days "${SEED_DAYS:-7}"
  uv run python -m abb_api.cli refresh-analytics --workspace bench
  exit 0
fi

step "start API (:$PORT) and worker"
uv run uvicorn abb_api.main:app_from_env --factory --port "$PORT" >"$WORK/api.log" 2>&1 & pids+=($!)
uv run python -m abb_api.worker >"$WORK/worker.log" 2>&1 & pids+=($!)
for _ in $(seq 1 40); do curl -sf "$API/readyz" >/dev/null && break; sleep 0.5; done
curl -sf "$API/readyz" >/dev/null || fail "API did not become ready: $(tail -5 "$WORK/api.log")"
cli() { uv run python -m abb_api.cli "$@" 2>/dev/null; }

if [ "${ANALYTICS_E2E_MODE:-}" = bench ]; then
  step "benchmark: Stage A dataset (seed it first: uv run python ../../scripts/analytics_seed.py --database-url \$ABB_DATABASE_URL)"
  KEY="$(cli create-key --workspace bench --scopes runs:read --name bench)"
  [[ "$KEY" == abb_live_* ]] || fail "no 'bench' workspace: seed the dataset first"
  cd "$root"
  BENCH_API_URL="$API" BENCH_KEY="$KEY" uv run --project apps/api python scripts/bench_analytics.py \
    --rounds "${BENCH_ROUNDS:-15}" --json-out "${BENCH_OUT:-$root/.local/analytics-bench.json}"
  exit 0
fi

step "provision a workspace, a project, a write key and a workspace-wide read-only key (secrets never printed)"
suffix="$(date +%s)$RANDOM"
cli create-workspace --name "Analytics E2E" --slug "analytics-e2e-$suffix" >/dev/null
cli create-project --workspace "analytics-e2e-$suffix" --name Demo --slug demo >/dev/null
WRITE_KEY="$(cli create-key --workspace "analytics-e2e-$suffix" --project demo --scopes events:write runs:read --name analytics-write)"
READ_KEY="$(cli create-key --workspace "analytics-e2e-$suffix" --scopes runs:read --name analytics-web-read)"
[[ "$WRITE_KEY" == abb_live_* && "$READ_KEY" == abb_live_* ]] || fail "key creation"

step "write the known runs and wait for the worker to derive them"
DRIVER_API_URL="$API" DRIVER_WRITE_KEY="$WRITE_KEY" \
  uv run --project "$root/packages/sdk-python" python "$root/scripts/analytics_driver.py" "$WORK/expect.json" \
  || fail "driver"
for _ in $(seq 1 60); do
  body="$(curl -sf -H "Authorization: Bearer $READ_KEY" "$API/v1/analytics/summary" || true)"
  echo "$body" | grep -q '"finished":4' && break
  sleep 0.5
done
echo "$body" | grep -q '"finished":4' || fail "worker did not derive the runs: $body"

step "Playwright: browser <-> web server <-> API"
cd "$root/apps/web"
mkdir -p "$root/docs/screenshots/phase-7"
E2E_ANALYTICS_API_KEY="$READ_KEY" E2E_ANALYTICS_API_URL="$API" E2E_ANALYTICS_EXPECT="$WORK/expect.json" \
  pnpm exec playwright test e2e/analytics.spec.ts
printf '\nANALYTICS-E2E PASSED\n'
