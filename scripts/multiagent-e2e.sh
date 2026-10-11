#!/usr/bin/env bash
# Multi-agent E2E (Phase 9): a dedicated database (abb_p9), an API on :8130 and a worker started by this script, the
# built web server on :3104, the multi-agent example (examples/multi-agent: threads, an asyncio task and a real
# subprocess whose clock is 1.5 s behind) writing a real run through the SDK, server-side assertions on the stored run,
# and Playwright checking the waterfall as an OWNER and as a VIEWER (structure yes, withheld text no).
# Needs `.env` (Postgres on 5433) and `pnpm --filter @abb/web build`. Never touches the shared dev/test databases.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; cd "$root"
set -a; . ./.env; set +a
DB_NAME="${MULTIAGENT_E2E_DB_NAME:-abb_p9}"
export ABB_DATABASE_URL="${ABB_DATABASE_URL%/*}/$DB_NAME"
export ABB_SUMMARY_DEBOUNCE_SECONDS=1
PORT="${MULTIAGENT_E2E_API_PORT:-8130}"; API="http://localhost:$PORT"
WORK="$(mktemp -d)"; pids=()
# `uv run` starts the real server as a child: kill every descendant, not just the wrapper.
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() { for p in "${pids[@]:-}"; do [ -n "$p" ] && kill_tree "$p"; done; rm -rf "$WORK"; }
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "MULTIAGENT-E2E FAILED: $*" >&2; exit 1; }
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
if curl -sf "$API/readyz" >/dev/null 2>&1; then fail "something is already serving $API; stop it or set MULTIAGENT_E2E_API_PORT"; fi
uv run uvicorn abb_api.main:app_from_env --factory --port "$PORT" >"$WORK/api.log" 2>&1 & pids+=($!)
uv run python -m abb_api.worker >"$WORK/worker.log" 2>&1 & pids+=($!)
for _ in $(seq 1 40); do curl -sf "$API/readyz" >/dev/null && break; sleep 0.5; done
curl -sf "$API/readyz" >/dev/null || fail "API did not become ready: $(tail -5 "$WORK/api.log")"

step "provision a workspace, a project, a write key, a script-only read key, and an OWNER and a VIEWER session (secrets never printed)"
suffix="$(date +%s)$RANDOM"; WS="multiagent-e2e-$suffix"
cli() { uv run python -m abb_api.cli "$@" 2>/dev/null; }
cli create-workspace --name "Multi-agent E2E" --slug "$WS" >/dev/null
cli create-project --workspace "$WS" --name Demo --slug demo >/dev/null
WRITE_KEY="$(cli create-key --workspace "$WS" --project demo --scopes events:write --name multiagent-write)"
READ_KEY="$(cli create-key --workspace "$WS" --scopes runs:read --name multiagent-script-read)"
[[ "$WRITE_KEY" == abb_live_* && "$READ_KEY" == abb_live_* ]] || fail "key creation"
cli add-member --workspace "$WS" --email "owner-$suffix@multiagent.test" --role OWNER >/dev/null
cli add-member --workspace "$WS" --email "viewer-$suffix@multiagent.test" --role VIEWER >/dev/null
# ABB_ALLOW_DEV_SESSIONS=1 is set for these two commands only: a development session is the browser's only credential.
OWNER_SESSION="$(ABB_ENVIRONMENT=development ABB_ALLOW_DEV_SESSIONS=1 cli create-session --email "owner-$suffix@multiagent.test" --hours 1)"
VIEWER_SESSION="$(ABB_ENVIRONMENT=development ABB_ALLOW_DEV_SESSIONS=1 cli create-session --email "viewer-$suffix@multiagent.test" --hours 1)"
[ -n "$OWNER_SESSION" ] && [ -n "$VIEWER_SESSION" ] || fail "could not mint the sessions"

cd "$root"
step "run the multi-agent example against the API (tester's clock 1.5 s behind, one failing tool, full payloads)"
OUT="$(BLACKBOX_API_KEY="$WRITE_KEY" BLACKBOX_ENDPOINT="$API" \
  uv run --project packages/sdk-python python examples/multi-agent/agent.py --skew-ms 1500 --fail-tool --full-payloads | tail -1)"
RUN_ID="$(echo "$OUT" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["ok"] and d["flushed"], d; print(d["run_id"])')"
echo "run $RUN_ID"

step "wait for the summary, then assert the stored run"
for _ in $(seq 1 60); do
  state="$(curl -sS "$API/v1/runs/$RUN_ID" -H "Authorization: Bearer $READ_KEY" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("summary_state"))')"
  [ "$state" = current ] && break; sleep 0.5
done
[ "$state" = current ] || fail "worker did not derive the run (state=$state)"
sleep 1.5   # a late batch (the tester's flush) re-opens the summary: wait for it to settle, then read
RUN_JSON="$WORK/run.json"; EVENTS_JSON="$WORK/events.json"; SPANS_JSON="$WORK/spans.json"
curl -sS "$API/v1/runs/$RUN_ID" -H "Authorization: Bearer $READ_KEY" >"$RUN_JSON"
curl -sS "$API/v1/runs/$RUN_ID/events?limit=500" -H "Authorization: Bearer $READ_KEY" >"$EVENTS_JSON"
curl -sS "$API/v1/runs/$RUN_ID/spans?limit=500" -H "Authorization: Bearer $READ_KEY" >"$SPANS_JSON"
RUN_JSON="$RUN_JSON" EVENTS_JSON="$EVENTS_JSON" SPANS_JSON="$SPANS_JSON" python3 - <<'PY' || fail "server-side assertions"
import json, os
run = json.load(open(os.environ["RUN_JSON"]))
events = json.load(open(os.environ["EVENTS_JSON"]))["items"]
spans = json.load(open(os.environ["SPANS_JSON"]))["items"]
assert run["summary_state"] == "current" and run["summary_version"] == 3, run["summary_version"]
assert run["agent_id"] == "planner", run["agent_id"]                 # the root agent comes from run.started
assert run["ordering_mode"] == "time", run["ordering_mode"]          # the tester sends no sequence
assert run["status"] == "SUCCESS", run["status"]
agents = run["summary"]["agents"]
assert run["summary"]["agent_count"] == 5 and len(agents) == 5, agents
assert {a["agent_id"] for a in agents} == {"planner", "researcher", "coder", "tester", "reviewer"}
assert all(a["event_count"] > 0 for a in agents)
coder = next(e for e in events if e["event_type"] == "agent.started" and e["agent_id"] == "coder")
tester = next(e for e in events if e["event_type"] == "agent.started" and e["agent_id"] == "tester")
assert tester["parent_span_id"] == coder["span_id"], "the tester must nest under the coder"
assert tester["sequence"] is None and coder["sequence"] is not None
assert tester["occurred_at"] < coder["occurred_at"], "the skewed tester should precede its parent in time"
assert sum(e["event_type"] == "tool.call.failed" for e in events) == 1
by_id = {s["id"]: s for s in spans}
assert by_id[tester["span_id"]]["parent_span_id"] == coder["span_id"] and by_id[tester["span_id"]]["agent_id"] == "tester"
print("stored run ok:", len(events), "events,", len(spans), "spans, 5 agents, ordering_mode", run["ordering_mode"])
PY

step "Playwright: owner and viewer in a real browser (built web server on :3104)"
cd "$root/apps/web"
E2E_SESSION_TOKEN="$OWNER_SESSION" E2E_VIEWER_SESSION_TOKEN="$VIEWER_SESSION" E2E_WORKSPACE="$WS" \
  E2E_MULTIAGENT_API_URL="$API" E2E_MULTIAGENT_RUN_ID="$RUN_ID" pnpm exec playwright test e2e/multiagent.spec.ts
printf '\nMULTIAGENT-E2E PASSED\n'
