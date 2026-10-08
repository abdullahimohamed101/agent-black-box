#!/usr/bin/env bash
# Phase 8 end to end: the LangGraph example (examples/langgraph/agent.py) runs through the real API.
# Own database (abb_p8), API on :8160 and a worker started here; no compose, no shared databases touched.
# Needs `.env` (Postgres on 5433) and `uv sync` in apps/api and integrations/langgraph.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; . ./.env; set +a
DB_NAME="${INTEGRATIONS_E2E_DB_NAME:-abb_p8}"
export ABB_DATABASE_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
export ABB_SUMMARY_DEBOUNCE_SECONDS=1
PORT="${INTEGRATIONS_E2E_API_PORT:-8160}"; API="http://localhost:$PORT"
WORK="$(mktemp -d)"; pids=()
cleanup() { for p in "${pids[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; rm -rf "$WORK"; }
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "INTEGRATIONS-E2E FAILED: $*" >&2; exit 1; }
if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  fail "port $PORT is already in use; refusing to talk to a process this script did not start"
fi
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

step "provision a workspace, project and key (secret never printed)"
suffix="$(date +%s)$RANDOM"
cli() { uv run python -m abb_api.cli "$@" 2>/dev/null; }
cli create-workspace --name "Integrations E2E" --slug "int-e2e-$suffix" >/dev/null
cli create-project --workspace "int-e2e-$suffix" --name Demo --slug demo >/dev/null
KEY="$(cli create-key --workspace "int-e2e-$suffix" --project demo --scopes events:write runs:read --name int-e2e)"
[[ "$KEY" == abb_live_* ]] || fail "key creation"

step "run the LangGraph example (real langgraph, local fake model) against the API"
cd "$root/integrations/langgraph"
out="$(BLACKBOX_API_KEY="$KEY" BLACKBOX_ENDPOINT="$API" uv run python ../../examples/langgraph/agent.py)"
RUN_ID="${out##run }"; [[ "$RUN_ID" == run_* ]] || fail "no run id in: $out"
echo "example finished: $RUN_ID"

get() { curl -fsS -H "Authorization: Bearer $KEY" "$API/v1/runs/$RUN_ID$1"; }
want="SUCCESS current 14 2 1 320"
for _ in $(seq 1 40); do
  state="$(get "" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'], d['summary_state'], *[d['summary'].get(k) for k in ('event_count','llm_calls','tool_calls','input_tokens')])")" || state=""
  [[ "$state" == "$want" ]] && break
  sleep 0.5
done
[[ "$state" == "$want" ]] || fail "unexpected run state: '$state' (wanted '$want')"

types="$(get /events | python3 -c "import sys,json; print(','.join(e['event_type'] for e in json.load(sys.stdin)['items']))")"
expected="run.started,span.started,llm.request.started,llm.request.completed,span.completed,span.started,tool.call.started,tool.call.completed,span.completed,span.started,llm.request.started,llm.request.completed,span.completed,run.completed"
[[ "$types" == "$expected" ]] || fail "event order: $types"

nested="$(get /spans | python3 -c "
import sys,json
items = json.load(sys.stdin)['items']
by_id = {s['id']: s for s in items}
steps = {s['name']: s['id'] for s in items if s.get('name') in ('plan','act','answer')}
leaves = [s for s in items if s.get('kind') in ('llm','tool')]
ok = len(steps) == 3 and len(leaves) == 3 and all(by_id.get(s.get('parent_span_id'), {}).get('name') in steps for s in leaves)
print('ok' if ok else 'bad: ' + json.dumps(items)[:600])")"
[[ "$nested" == ok ]] || fail "span nesting: $nested"
cd "$root"
base="$(git merge-base HEAD origin/main 2>/dev/null || git merge-base HEAD main 2>/dev/null || true)"
if [ -n "$base" ]; then
  [ -z "$(git diff --name-only "$base" -- apps)" ] || fail "apps/ changed: this phase needs no backend change"
else
  echo "note: no main ref to compare against; the apps/ unchanged check was skipped"
fi
printf '\nINTEGRATIONS-E2E PASSED\n'
