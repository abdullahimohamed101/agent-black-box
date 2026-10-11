#!/usr/bin/env bash
# Findings E2E (Phase 10): a dedicated database (abb_p10), an API on :8135 and a worker (detector delays 0), the built web
# server on :3105 (started by Playwright). The ten pathological runs (scripts/pathological_runs.py) go in through the real
# ingestion path in shuffled batches; the API assertions (scripts/findings_e2e_check.py) check each run's findings against
# the hand-written expectations, that every cited event resolves, that a VIEWER sees the same bytes as the OWNER, and that
# `rebuild-findings` reproduces the findings; Playwright then reads them in a real browser.
# Needs `.env` (Postgres on 5433) and `pnpm --filter @abb/web build`. Never touches the shared dev/test databases.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; . ./.env; set +a
DB_NAME="${FINDINGS_E2E_DB_NAME:-abb_p10}"
export ABB_DATABASE_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
export ABB_SUMMARY_DEBOUNCE_SECONDS=0
export ABB_FINDINGS_DELAY_SECONDS=0
PORT="${FINDINGS_E2E_API_PORT:-8135}"; API="http://localhost:$PORT"
WORK="$(mktemp -d)"; pids=()
# `uv run` starts the real server as a child: kill every descendant, not just the wrapper.
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() { for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done; rm -rf "$WORK"; }
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "FINDINGS-E2E FAILED: $*" >&2; exit 1; }
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
if curl -sf "$API/readyz" >/dev/null 2>&1; then fail "something is already serving $API; stop it or set FINDINGS_E2E_API_PORT"; fi
uv run uvicorn abb_api.main:app_from_env --factory --port "$PORT" >"$WORK/api.log" 2>&1 & pids+=($!)
uv run python -m abb_api.worker >"$WORK/worker.log" 2>&1 & pids+=($!)
for _ in $(seq 1 40); do curl -sf "$API/readyz" >/dev/null && break; sleep 0.5; done
curl -sf "$API/readyz" >/dev/null || fail "API did not become ready: $(tail -5 "$WORK/api.log")"

step "provision a workspace, a project, keys, a throwaway OWNER and a throwaway VIEWER (generated, local-only)"
suffix="$(date +%s)$RANDOM"
WS="findings-e2e-$suffix"; OWNER="owner-$suffix@findings.test"; VIEWER="viewer-$suffix@findings.test"
cli() { uv run python -m abb_api.cli "$@" 2>/dev/null; }
cli create-workspace --name "Findings E2E" --slug "$WS" >/dev/null
cli create-project --workspace "$WS" --name Demo --slug demo >/dev/null
WRITE_KEY="$(cli create-key --workspace "$WS" --project demo --scopes events:write --name findings-write)"
READ_KEY="$(cli create-key --workspace "$WS" --scopes runs:read --name findings-script-read)"   # polling only; never given to the web
[[ "$WRITE_KEY" == abb_live_* && "$READ_KEY" == abb_live_* ]] || fail "key creation"
cli add-member --workspace "$WS" --email "$OWNER" --role OWNER >/dev/null
cli add-member --workspace "$WS" --email "$VIEWER" --role VIEWER >/dev/null   # the only OWNER cannot be demoted, so a second person
OWNER_SESSION="$(ABB_ENVIRONMENT=development ABB_ALLOW_DEV_SESSIONS=1 cli create-session --email "$OWNER" --hours 1)"
VIEWER_SESSION="$(ABB_ENVIRONMENT=development ABB_ALLOW_DEV_SESSIONS=1 cli create-session --email "$VIEWER" --hours 1)"
[ -n "$OWNER_SESSION" ] && [ -n "$VIEWER_SESSION" ] || fail "could not mint sessions"
printf '%s' "$WRITE_KEY" >"$WORK/write.key"; chmod 600 "$WORK/write.key"

check() { E2E_API="$API" E2E_WORKSPACE="$WS" E2E_READ_KEY="$READ_KEY" E2E_OWNER_SESSION="$OWNER_SESSION" \
  E2E_VIEWER_SESSION="$VIEWER_SESSION" E2E_FIXTURES="$root/apps/api/tests/fixtures/pathological" \
  uv run python "$root/scripts/findings_e2e_check.py" "$@"; }

step "post the ten pathological runs through the real ingestion path (batches shuffled)"
uv run python "$root/scripts/pathological_runs.py" --api "$API" --key-file "$WORK/write.key" --shuffle 7
step "wait for every run's findings to be current"
check wait
step "re-derive once so the cost baseline includes every summarised run, then assert the findings"
check snapshot "$WORK/settle.json" >/dev/null
cli rebuild-findings --workspace "$WS" --limit 1000 >/dev/null
check wait --after "$WORK/settle.json"
check verify
step "a second rebuild reproduces the same findings (ids, fingerprints, evidence, created_at)"
check snapshot "$WORK/before.json"
cli rebuild-findings --workspace "$WS" --limit 1000 >/dev/null
check wait --after "$WORK/before.json"
check compare "$WORK/before.json"
RUNS_JSON="$(check runs)"

step "Playwright: browser <-> web server <-> API"
cd "$root/apps/web"
mkdir -p "$root/docs/screenshots/phase-10"
E2E_SESSION_TOKEN="$OWNER_SESSION" E2E_VIEWER_SESSION_TOKEN="$VIEWER_SESSION" E2E_WORKSPACE="$WS" \
  E2E_FINDINGS_API_URL="$API" E2E_FINDINGS_RUNS="$RUNS_JSON" pnpm exec playwright test e2e/findings.spec.ts
printf '\nFINDINGS-E2E PASSED\n'
