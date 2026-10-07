#!/usr/bin/env bash
# Web E2E against a REAL ingested run: a dedicated database (abb_p4, never the shared dev/test ones), the API on :8100
# and a worker started by this script, events ingested over HTTP, then the Playwright "real" spec through the web
# server's read proxy (ADR-021). Needs `.env` (Postgres on 5433) and `pnpm --filter @abb/web build`.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; . ./.env; set +a
DB_NAME="${E2E_DB_NAME:-abb_p4}"
export ABB_DATABASE_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
export ABB_SUMMARY_DEBOUNCE_SECONDS=1
PORT="${E2E_API_PORT:-8110}"; API="http://localhost:$PORT"
WORK="$(mktemp -d)"; pids=()
cleanup() { for p in "${pids[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; rm -rf "$WORK"; }
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "E2E-REAL FAILED: $*" >&2; exit 1; }
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
uv run uvicorn abb_api.main:app_from_env --factory --port "$PORT" >"$WORK/api.log" 2>&1 & pids+=($!)
uv run python -m abb_api.worker >"$WORK/worker.log" 2>&1 & pids+=($!)
for _ in $(seq 1 40); do curl -sf "$API/readyz" >/dev/null && break; sleep 0.5; done
curl -sf "$API/readyz" >/dev/null || fail "API did not become ready: $(tail -5 "$WORK/api.log")"

step "provision a workspace, project, a write key and a workspace-wide read-only key (secrets never printed)"
suffix="$(date +%s)$RANDOM"
cli() { uv run python -m abb_api.cli "$@" 2>/dev/null; }
cli create-workspace --name "Web E2E" --slug "web-e2e-$suffix" >/dev/null
cli create-project --workspace "web-e2e-$suffix" --name Demo --slug demo >/dev/null
WRITE_KEY="$(cli create-key --workspace "web-e2e-$suffix" --project demo --scopes events:write runs:read --name e2e-write)"
READ_KEY="$(cli create-key --workspace "web-e2e-$suffix" --scopes runs:read --name e2e-web-read)"
[[ "$WRITE_KEY" == abb_live_* && "$READ_KEY" == abb_live_* ]] || fail "key creation"

step "ingest a failed-with-retries run and a successful run over HTTP"
WRITE_KEY="$WRITE_KEY" API="$API" uv run python - >"$WORK/runs.env" <<'PY'
import json, os, urllib.request
from datetime import datetime, timedelta, timezone
from abb_event_schema.ids import IdKind, new_id

def post(events):
    req = urllib.request.Request(os.environ["API"] + "/v1/events/batch", data=json.dumps({"events": events}).encode(),
        headers={"Authorization": "Bearer " + os.environ["WRITE_KEY"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        body = json.load(r)
    assert body["accepted"] == len(events) and body["rejected"] == 0, body

def build(name, ok):
    run, trace, root = new_id(IdKind.RUN), new_id(IdKind.TRACE), new_id(IdKind.SPAN)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=2)
    n = 0
    out = []
    def ev(kind, attrs=None, gap=0.5, **kw):
        nonlocal n, t0
        n += 1; t0 += timedelta(seconds=gap)
        e = {"schema_version": "1.0", "event_id": new_id(IdKind.EVENT), "run_id": run, "trace_id": trace,
             "agent_id": "coding-agent", "event_type": kind, "sequence": n,
             "occurred_at": t0.isoformat().replace("+00:00", "Z"), "attributes": attrs or {}}
        e.update(kw); out.append(e)
    tool = new_id(IdKind.SPAN); tool2 = new_id(IdKind.SPAN); llm = new_id(IdKind.SPAN)
    ev("run.started", {"run.name": name})
    ev("agent.started", span_id=root)
    ev("file.read", {"file.path": "src/auth/login.ts"})
    ev("llm.request.completed", {"llm.provider": "demo", "llm.model": "model-x", "llm.input_tokens": 1200,
       "llm.output_tokens": 80, "cost.estimated_usd": 0.0081}, span_id=llm, parent_span_id=root, status="success", duration_ms=900)
    ev("tool.call.started", {"tool.name": "run_tests"}, span_id=tool, parent_span_id=root)
    if ok:
        ev("tool.call.completed", {"tool.name": "run_tests"}, span_id=tool, parent_span_id=root, status="success", duration_ms=1200)
        ev("agent.completed", span_id=root, status="success")
        ev("run.completed", status="success")
    else:
        ev("tool.call.failed", {"tool.name": "run_tests", "tool.error_type": "TestFailure"}, span_id=tool, parent_span_id=root, status="error", duration_ms=1500)
        ev("retry.attempted", {"retry.attempt": 1, "retry.reason": "TestFailure"})
        ev("tool.call.started", {"tool.name": "run_tests"}, span_id=tool2, parent_span_id=root)
        ev("tool.call.failed", {"tool.name": "run_tests", "tool.error_type": "TestFailure"}, span_id=tool2, parent_span_id=root, status="error", duration_ms=1400)
        ev("agent.failed", span_id=root, status="error")
        ev("run.failed", status="error")
    post(out)
    return run

print("E2E_REAL_FAILED_RUN=" + build("Real: fix login bug (fails)", False))
print("E2E_REAL_OK_RUN=" + build("Real: fix login bug (passes)", True))
PY
set -a; . "$WORK/runs.env"; set +a
for id in "$E2E_REAL_FAILED_RUN" "$E2E_REAL_OK_RUN"; do
  state=""
  for _ in $(seq 1 40); do
    state="$(curl -sS "$API/v1/runs/$id" -H "Authorization: Bearer $READ_KEY" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["summary_state"], d["status"])')"
    [[ "$state" == "current "* && "$state" != "current RUNNING" ]] && break
    sleep 0.5
  done
  echo "$id -> $state"
  [[ "$state" == "current "* && "$state" != "current RUNNING" ]] || fail "worker did not derive $id (state=$state)"
done

step "Playwright against the web server reading through its proxy"
cd "$root/apps/web"
E2E_REAL_API_KEY="$READ_KEY" E2E_REAL_API_URL="$API" E2E_REAL_FAILED_RUN="$E2E_REAL_FAILED_RUN" E2E_REAL_OK_RUN="$E2E_REAL_OK_RUN" \
  pnpm exec playwright test e2e/real.spec.ts
printf '\nE2E-REAL PASSED\n'
