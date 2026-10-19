#!/usr/bin/env bash
# Demo determinism E2E (Phase 20, D9): a dedicated database (abb_demo_e2e), an API on :8170 and a worker started here, then
# scripts/demo.sh run TWICE against it. Each run's stored result (agents, spans, tool and model calls, errors, retries, costs,
# statuses, findings) is reduced to a normalised summary (ids and timestamps removed) and the two summaries must be equal.
# Also asserts the demo never calls a provider: model keys are scrubbed, BLACKBOX_ENDPOINT is loopback, and the demo key file
# is owner-only. Needs `.env` (Postgres on 5433) or ABB_DATABASE_URL. Never touches the shared dev/test databases or your dev stack's ports.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
# An ABB_DATABASE_URL already in the environment wins (a different Postgres); otherwise .env (Postgres on 5433).
if [ -z "${ABB_DATABASE_URL:-}" ]; then set -a; . ./.env; set +a; fi
DB_NAME="${DEMO_E2E_DB_NAME:-abb_demo_e2e}"
export ABB_DATABASE_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
export ABB_SUMMARY_DEBOUNCE_SECONDS=1 ABB_FINDINGS_DELAY_SECONDS=1
# The waiting agent publishes the demo policy as the demo owner: that needs development sessions on THIS throwaway API.
export ABB_ENVIRONMENT=development ABB_ALLOW_DEV_SESSIONS=1
PORT="${DEMO_E2E_API_PORT:-8170}"; API="http://localhost:$PORT"
export ABB_WEB_ORIGIN="http://localhost:3000"   # only the Origin header of the owner's session; no web server runs here
WORK="$(mktemp -d)"; pids=()
export ABB_ARTIFACT_DIR="$WORK/artifacts"
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() {
  for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done
  [ ! -f "$WORK/state/waiters.pid" ] || while read -r p; do [ -n "$p" ] && kill_tree "$p"; done <"$WORK/state/waiters.pid"
  rm -rf "$WORK"
}
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "DEMO-E2E FAILED: $*" >&2; exit 1; }

step "create database $DB_NAME if missing, migrate to head"
cd apps/api
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
if curl -sf "$API/readyz" >/dev/null 2>&1; then fail "something is already serving $API; stop it or set DEMO_E2E_API_PORT"; fi
uv run uvicorn abb_api.main:app_from_env --factory --port "$PORT" >"$WORK/api.log" 2>&1 & pids+=($!)
uv run python -m abb_api.worker >"$WORK/worker.log" 2>&1 & pids+=($!)
for _ in $(seq 1 40); do curl -sf "$API/readyz" >/dev/null && break; sleep 0.5; done
curl -sf "$API/readyz" >/dev/null || fail "API did not become ready: $(tail -5 "$WORK/api.log")"
cd "$root"

# demo.sh keeps its key in WORK so nothing under .local changes; model keys are planted to prove they are scrubbed.
export DEMO_API_URL="$API" DEMO_WEB_URL="$ABB_WEB_ORIGIN" DEMO_KEY_FILE="$WORK/demo-key" DEMO_STATE_DIR="$WORK/state" DEMO_KEEP_WAITERS=1 DEMO_PACE=0
export ANTHROPIC_API_KEY="planted-not-a-key" OPENAI_API_KEY="planted-not-a-key"
for n in 1 2; do
  step "demo run $n (scripts/demo.sh)"
  DEMO_RUNS_FILE="$WORK/runs-$n.txt" scripts/demo.sh >"$WORK/demo-$n.log" 2>&1 || { tail -20 "$WORK/demo-$n.log" >&2; fail "demo run $n failed"; }
  tail -8 "$WORK/demo-$n.log"
  [ "$(wc -l <"$WORK/runs-$n.txt")" -eq 12 ] || fail "demo run $n recorded $(wc -l <"$WORK/runs-$n.txt") runs, expected 12"
done
[ "$(stat -c %a "$WORK/demo-key" 2>/dev/null || stat -f %Lp "$WORK/demo-key")" = 600 ] || fail "the demo key file is not owner-only"
! grep -q "planted-not-a-key" "$WORK"/demo-*.log || fail "a model key reached the demo output"

step "summarise both runs (bounded waits for the worker, ids and timestamps removed)"
for n in 1 2; do
  DEMO_E2E_API="$API" DEMO_E2E_KEY_FILE="$WORK/demo-key" \
    uv run --project apps/api python scripts/demo_e2e_check.py summarise "$WORK/runs-$n.txt" >"$WORK/summary-$n.json" || fail "could not summarise demo run $n"
done
uv run --project apps/api python scripts/demo_e2e_check.py report "$WORK/summary-1.json"
step "the data the demo video needs (docs/design/launch/video-brief.md, section 6)"
uv run --project apps/api python scripts/demo_e2e_check.py asks "$WORK/summary-1.json"
DEMO_E2E_API="$API" DEMO_E2E_ORIGIN="$ABB_WEB_ORIGIN" DEMO_E2E_SESSION="$(cd apps/api && uv run python -m abb_api.cli create-session --email owner@local.test --hours 1 2>/dev/null)" \
  uv run --project apps/api python scripts/demo_e2e_check.py approvals || fail "the pending approval is not as the video needs it"
if diff -u "$WORK/summary-1.json" "$WORK/summary-2.json"; then
  echo "demo run 1 == demo run 2 (normalised)"
else
  fail "the two demo runs differ (diff above)"
fi
printf '\nDEMO-E2E PASSED\n'
