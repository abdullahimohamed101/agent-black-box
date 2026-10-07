#!/usr/bin/env bash
# SDK end to end against the running container stack (`make up`): provision a key, run the example
# script with the real SDK, read the run back through the API and check order, status and counts.
set -euo pipefail
API_URL="${API_URL:-http://localhost:8000}"
compose=(docker compose --profile app)
cli() { "${compose[@]}" exec -T api python -m abb_api.cli "$@"; }
suffix="$(date +%s)$RANDOM"
fail() { echo "SDK E2E FAILED: $*" >&2; exit 1; }

cli create-workspace --name "SDK $suffix" --slug "sdk-$suffix" >/dev/null 2>&1
cli create-project --workspace "sdk-$suffix" --name Demo --slug demo >/dev/null 2>&1
KEY="$(cli create-key --workspace "sdk-$suffix" --project demo --scopes events:write runs:read --name sdk-e2e 2>/dev/null)"
[[ "$KEY" == abb_live_* ]] || fail "key creation"

out="$(cd "$(dirname "$0")/../packages/sdk-python" && BLACKBOX_API_KEY="$KEY" BLACKBOX_ENDPOINT="$API_URL" \
  uv run python ../../examples/python/trace_an_agent.py)"
RUN_ID="${out##run }"
echo "example finished: $RUN_ID"

for _ in $(seq 1 40); do   # the worker derives the summary asynchronously (~1 s debounce)
  state="$(curl -fsS -H "Authorization: Bearer $KEY" "$API_URL/v1/runs/$RUN_ID" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'], d['summary_state'], *[d['summary'].get(k) for k in ('event_count','llm_calls','tool_calls','input_tokens')])")" || state=""
  [[ "$state" == "SUCCESS current 6 1 1 1200" ]] && break
  sleep 0.5
done
[[ "$state" == "SUCCESS current 6 1 1 1200" ]] || fail "unexpected run state: '$state'"
types="$(curl -fsS -H "Authorization: Bearer $KEY" "$API_URL/v1/runs/$RUN_ID/events" | python3 -c "import sys,json; print(','.join(e['event_type'] for e in json.load(sys.stdin)['items']))")"
expected="run.started,tool.call.started,tool.call.completed,llm.request.started,llm.request.completed,run.completed"
[[ "$types" == "$expected" ]] || fail "event order: $types"
spans="$(curl -fsS -H "Authorization: Bearer $KEY" "$API_URL/v1/runs/$RUN_ID/spans" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['items']))")"
[[ "$spans" == "2" ]] || fail "spans: $spans"
echo "SDK E2E PASSED"
