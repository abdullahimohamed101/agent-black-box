#!/usr/bin/env bash
# Coding-agent E2E: a dedicated database (abb_p6), an API on :8140 with a throwaway artifact directory and a worker,
# the built web server on :3140, the scripted demo agent (examples/coding-agent) writing a real run through the SDK,
# a server-side secret scan of the database and the artifact files, and Playwright driving a real browser.
# Needs `.env` (Postgres on 5433) and `pnpm --filter @abb/web build`. Never touches the shared dev/test databases.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; . ./.env; set +a
DB_NAME="${CODING_E2E_DB_NAME:-abb_p6}"
export ABB_DATABASE_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
export ABB_SUMMARY_DEBOUNCE_SECONDS=1
PORT="${CODING_E2E_API_PORT:-8140}"; API="http://localhost:$PORT"
WORK="$(mktemp -d)"; pids=()
export ABB_ARTIFACT_DIR="$WORK/artifacts"
# `uv run` starts the real server as a child: kill every descendant, not just the wrapper.
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() { for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done; rm -rf "$WORK"; }
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "CODING-E2E FAILED: $*" >&2; exit 1; }
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

step "start API (:$PORT, artifacts in a temp directory) and worker"
if curl -sf "$API/readyz" >/dev/null 2>&1; then fail "something is already serving $API; stop it or set CODING_E2E_API_PORT"; fi
uv run uvicorn abb_api.main:app_from_env --factory --port "$PORT" >"$WORK/api.log" 2>&1 & pids+=($!)
uv run python -m abb_api.worker >"$WORK/worker.log" 2>&1 & pids+=($!)
for _ in $(seq 1 40); do curl -sf "$API/readyz" >/dev/null && break; sleep 0.5; done
curl -sf "$API/readyz" >/dev/null || fail "API did not become ready: $(tail -5 "$WORK/api.log")"

step "provision a workspace, a project, a write key (events + artifacts) and a workspace-wide read-only key"
suffix="$(date +%s)$RANDOM"
cli() { uv run python -m abb_api.cli "$@" 2>/dev/null; }
cli create-workspace --name "Coding E2E" --slug "coding-e2e-$suffix" >/dev/null
cli create-project --workspace "coding-e2e-$suffix" --name Demo --slug demo >/dev/null
WRITE_KEY="$(cli create-key --workspace "coding-e2e-$suffix" --project demo --scopes events:write artifacts:write --name coding-write)"
READ_KEY="$(cli create-key --workspace "coding-e2e-$suffix" --scopes runs:read --name coding-web-read)"
[[ "$WRITE_KEY" == abb_live_* && "$READ_KEY" == abb_live_* ]] || fail "key creation"

cd "$root"
step "run the scripted coding agent (a secret of unknown shape is planted in its environment)"
export ABB_DEMO_ENV_SECRET="zz-unknown-shape-$RANDOM$RANDOM-secret"
PLANTED="$(uv run --project packages/sdk-python python -c "
import json, sys
sys.path.insert(0, 'examples/coding-agent')
from coding_agent.workspace import planted_literals
print(json.dumps(planted_literals()))")"
OUT="$(BLACKBOX_API_KEY="$WRITE_KEY" BLACKBOX_ENDPOINT="$API" \
  uv run --project packages/sdk-python python examples/coding-agent/run_demo.py | tail -1)"
echo "$OUT" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); print({k: d[k] for k in ("run_id","tests_passed","retries","turns","flushed")}); assert d["tests_passed"] and d["flushed"] and d["retries"]==1'
RUN_ID="$(echo "$OUT" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["run_id"])')"
sleep 3   # the worker derives the run summary about a second after the last event

step "server-side scan: planted secrets must not be in the database or in any artifact file"
CODING_E2E_PLANTED="$PLANTED" CODING_E2E_ARTIFACT_DIR="$ABB_ARTIFACT_DIR" \
  uv run --project apps/api python scripts/coding_e2e_check.py "$RUN_ID" || fail "secret scan"

step "Playwright: browser <-> web server <-> API"
cd "$root/apps/web"; [ -n "${CODING_E2E_SPEC:-}" ] && set -- "$CODING_E2E_SPEC" || set -- e2e/coding.spec.ts
E2E_CODING_API_KEY="$READ_KEY" E2E_CODING_API_URL="$API" E2E_CODING_RUN_ID="$RUN_ID" \
  E2E_CODING_PLANTED="$PLANTED" pnpm exec playwright test "$1"
printf '\nCODING-E2E PASSED\n'
