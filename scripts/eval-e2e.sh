#!/usr/bin/env bash
# Evaluations E2E (Phase 11): a dedicated database (abb_p11), an API on :8180 with the fake judge and a worker, the built web
# server on :3180, the scripted coding agent run twice through the SDK (--variant baseline and --variant wrong-fix), an owner
# who evaluates, records a verdict, compares the runs and reads an experiment, and a second person added as VIEWER who sees
# scores and labels but no explanation text and a metadata-only comparison. Playwright drives a real browser.
# Needs `.env` (Postgres on 5433) and `pnpm --filter @abb/web build`. Never touches the shared dev/test databases.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; . ./.env; set +a
DB_NAME="${EVAL_E2E_DB_NAME:-abb_p11}"
export ABB_DATABASE_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
export ABB_SUMMARY_DEBOUNCE_SECONDS=1
export ABB_JUDGE_PROVIDER=fake                      # the deterministic fake: plumbing, not a signal (ADR-091)
export ABB_EVAL_BATCH_DELAY_SECONDS=1
export ABB_WEB_ORIGIN="http://localhost:3180"       # fixed: playwright.config.ts starts the web server there
PORT="${EVAL_E2E_API_PORT:-8180}"; API="http://localhost:$PORT"
WORK="$(mktemp -d)"; pids=()
export ABB_ARTIFACT_DIR="$WORK/artifacts"
# `uv run` starts the real server as a child: kill every descendant, not just the wrapper.
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() { for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done; rm -rf "$WORK"; }
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "EVAL-E2E FAILED: $*" >&2; exit 1; }
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

step "start API (:$PORT, fake judge) and worker"
if curl -sf "$API/readyz" >/dev/null 2>&1; then fail "something is already serving $API; stop it or set EVAL_E2E_API_PORT"; fi
uv run uvicorn abb_api.main:app_from_env --factory --port "$PORT" >"$WORK/api.log" 2>&1 & pids+=($!)
uv run python -m abb_api.worker >"$WORK/worker.log" 2>&1 & pids+=($!)
for _ in $(seq 1 40); do curl -sf "$API/readyz" >/dev/null && break; sleep 0.5; done
curl -sf "$API/readyz" >/dev/null || fail "API did not become ready: $(tail -5 "$WORK/api.log")"

step "provision a workspace, a project, a write key, a throwaway OWNER and a throwaway VIEWER (generated, local-only)"
suffix="$(date +%s)$RANDOM"
WS="eval-e2e-$suffix"; OWNER="owner-$suffix@eval.test"; VIEWER="viewer-$suffix@eval.test"
cli() { uv run python -m abb_api.cli "$@" 2>/dev/null; }
cli create-workspace --name "Eval E2E" --slug "$WS" >/dev/null
cli create-project --workspace "$WS" --name Demo --slug demo >/dev/null
WRITE_KEY="$(cli create-key --workspace "$WS" --project demo --scopes events:write artifacts:write --name eval-write)"
[[ "$WRITE_KEY" == abb_live_* ]] || fail "key creation"
cli add-member --workspace "$WS" --email "$OWNER" --role OWNER >/dev/null
cli add-member --workspace "$WS" --email "$VIEWER" --role VIEWER >/dev/null   # the only OWNER cannot be demoted, so a second person
OWNER_SESSION="$(ABB_ENVIRONMENT=development ABB_ALLOW_DEV_SESSIONS=1 cli create-session --email "$OWNER" --hours 1)"
VIEWER_SESSION="$(ABB_ENVIRONMENT=development ABB_ALLOW_DEV_SESSIONS=1 cli create-session --email "$VIEWER" --hours 1)"
[ -n "$OWNER_SESSION" ] && [ -n "$VIEWER_SESSION" ] || fail "could not mint sessions"

cd "$root"
step "run the scripted coding agent twice through the SDK: baseline (passes) and wrong-fix (the tests still fail)"
run_variant() {
  BLACKBOX_API_KEY="$WRITE_KEY" BLACKBOX_ENDPOINT="$API" \
    uv run --project packages/sdk-python python examples/coding-agent/run_demo.py --variant "$1" | tail -1
}
OUT_BASE="$(run_variant baseline)"
OUT_WRONG="$(run_variant wrong-fix || true)"   # the wrong fix ends the run in error: exit status 1 is the point
field() { echo "$1" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d['$2'])"; }
[ "$(field "$OUT_BASE" tests_passed)" = "True" ] && [ "$(field "$OUT_BASE" flushed)" = "True" ] || fail "baseline run"
[ "$(field "$OUT_WRONG" tests_passed)" = "False" ] && [ "$(field "$OUT_WRONG" flushed)" = "True" ] || fail "wrong-fix run"
BASELINE_RUN="$(field "$OUT_BASE" run_id)"; WRONG_RUN="$(field "$OUT_WRONG" run_id)"

check() { E2E_API="$API" E2E_WEB_ORIGIN="$ABB_WEB_ORIGIN" E2E_WORKSPACE="$WS" E2E_OWNER_SESSION="$OWNER_SESSION" \
  E2E_VIEWER_SESSION="$VIEWER_SESSION" E2E_BASELINE_RUN="$BASELINE_RUN" E2E_WRONG_RUN="$WRONG_RUN" \
  uv run --project apps/api python scripts/eval_e2e_check.py "$@"; }
step "evaluate both runs (rule, metric, fake judge, a human verdict), prove a failing evaluator leaves the run alone, create the experiment"
IDS="$(check setup)"
step "API-level role checks: the viewer's comparison is metadata only and carries no explanation"
check verify

step "Playwright: browser <-> web server <-> API"
cd "$root/apps/web"
mkdir -p "$root/docs/screenshots/phase-11"
E2E_SESSION_TOKEN="$OWNER_SESSION" E2E_VIEWER_SESSION_TOKEN="$VIEWER_SESSION" E2E_WORKSPACE="$WS" \
  E2E_EVAL_API_URL="$API" E2E_EVAL_IDS="$IDS" pnpm exec playwright test e2e/eval.spec.ts
printf '\nEVAL-E2E PASSED\n'
