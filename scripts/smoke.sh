#!/usr/bin/env bash
# End-to-end smoke test against the running container stack (`make up`):
#   provision a tenant -> ingest a run over HTTP (gzip) -> worker derives it -> read it back,
#   plus the negative cases (no key, wrong key, another workspace). Exits non-zero on any failure.
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
compose=(docker compose --profile app)
cli() { "${compose[@]}" exec -T api python -m abb_api.cli "$@"; }
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
suffix="$(date +%s)$RANDOM"
fail() { echo "SMOKE FAILED: $*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }
json() { python3 -c "import sys, json; d = json.load(sys.stdin); print($1)"; }   # $1: expression on d

step "provision two workspaces (keys are shown once and never echoed here)"
cli create-workspace --name "Smoke A" --slug "smoke-a-$suffix" 2>/dev/null
cli create-project --workspace "smoke-a-$suffix" --name Demo --slug demo 2>/dev/null
KEY="$(cli create-key --workspace "smoke-a-$suffix" --project demo --scopes events:write runs:read --name smoke 2>/dev/null)"
cli create-workspace --name "Smoke B" --slug "smoke-b-$suffix" 2>/dev/null
cli create-project --workspace "smoke-b-$suffix" --name Demo --slug demo 2>/dev/null
OTHER_KEY="$(cli create-key --workspace "smoke-b-$suffix" --project demo --scopes events:write runs:read 2>/dev/null)"
[[ "$KEY" == abb_live_* && "$OTHER_KEY" == abb_live_* ]] || fail "key creation"

step "build a 10-event coding-agent run (run.started ... run.completed) with the event schema package"
BATCH="$("${compose[@]}" exec -T api python - <<'PY'
import json
from datetime import datetime, timedelta, timezone
from abb_event_schema.ids import IdKind, new_id

run, trace, root = new_id(IdKind.RUN), new_id(IdKind.TRACE), new_id(IdKind.SPAN)
t0 = datetime.now(timezone.utc)
tool_a, tool_b = new_id(IdKind.SPAN), new_id(IdKind.SPAN)

def ev(n, kind, attrs=None, **kw):
    e = {"schema_version": "1.0", "event_id": new_id(IdKind.EVENT), "run_id": run, "trace_id": trace,
         "agent_id": "coding-agent", "event_type": kind, "sequence": n,
         "occurred_at": (t0 + timedelta(seconds=n)).isoformat().replace("+00:00", "Z"),
         "attributes": attrs or {}}
    e.update(kw)
    return e

events = [
    ev(1, "run.started", {"run.name": "Smoke: fix login bug"}),
    ev(2, "agent.started", span_id=root),
    ev(3, "file.read", {"file.path": "src/auth/login.ts"}),
    ev(4, "llm.request.completed", {"llm.provider": "demo", "llm.model": "model-x", "llm.input_tokens": 120,
       "llm.output_tokens": 30, "cost.estimated_usd": 0.004}, span_id=new_id(IdKind.SPAN), parent_span_id=root, status="success", duration_ms=900),
    ev(5, "tool.call.started", {"tool.name": "shell"}, span_id=tool_a, parent_span_id=root),
    ev(6, "tool.call.completed", {"tool.name": "shell"}, span_id=tool_a, parent_span_id=root, status="success", duration_ms=1000),
    ev(7, "file.modified", {"file.path": "src/auth/login.ts"}),
    ev(8, "retry.attempted", {"retry.attempt": 1}),
    ev(9, "agent.completed", span_id=root, status="success"),
    ev(10, "run.completed"),
]
print(json.dumps({"batch_id": "smoke-batch", "events": events}))
PY
)"
RUN_ID="$(echo "$BATCH" | json 'd["events"][0]["run_id"]')"

step "ingest over HTTP with gzip"
status="$(echo -n "$BATCH" | gzip -c | curl -sS -o "$WORK/ingest.json" -w '%{http_code}' \
  -X POST "$API_URL/v1/events/batch" -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -H 'Content-Encoding: gzip' --data-binary @-)"
[[ "$status" == 202 ]] || fail "ingest returned $status: $(cat "$WORK/ingest.json")"
[[ "$(json 'd["accepted"]' < "$WORK/ingest.json")" == 10 ]] || fail "expected 10 accepted: $(cat "$WORK/ingest.json")"
echo "202 accepted=10"
retry="$(echo -n "$BATCH" | curl -sS -X POST "$API_URL/v1/events/batch" -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' --data-binary @- | json '(d["accepted"], d["duplicates"])')"
[[ "$retry" == "(0, 10)" ]] || fail "retry must be all duplicates, got $retry"
echo "retry of the same batch: accepted=0 duplicates=10"

step "wait for the worker to derive the run"
state=""
for _ in $(seq 1 40); do
  state="$(curl -sS "$API_URL/v1/runs/$RUN_ID" -H "Authorization: Bearer $KEY" | json 'd["summary_state"]')"
  [[ "$state" == current ]] && break
  sleep 0.5
done
[[ "$state" == current ]] || fail "worker did not process the run within 20s (state=$state)"
curl -sS "$API_URL/v1/runs/$RUN_ID" -H "Authorization: Bearer $KEY" > "$WORK/run.json"
[[ "$(json 'd["status"]' < "$WORK/run.json")" == SUCCESS ]] || fail "run status: $(cat "$WORK/run.json")"
[[ "$(json 'd["summary"]["event_count"]' < "$WORK/run.json")" == 10 ]] || fail "event_count"
[[ "$(json 'd["summary"]["retry_count"]' < "$WORK/run.json")" == 1 ]] || fail "retry_count"
python3 - "$WORK/run.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); s = d["summary"]
print(f"run {d['id']}: {d['status']} \"{d['name']}\" in {d['duration_ms']:.0f} ms")
print(f"  events={s['event_count']} llm={s['llm_calls']} tools={s['tool_calls']} retries={s['retry_count']} "
      f"files={s['files_modified']} tokens={s['input_tokens']}/{s['output_tokens']} cost=${s['estimated_cost_usd']}")
PY

step "read back events (canonical order) and spans"
curl -sS "$API_URL/v1/runs/$RUN_ID/events?limit=5" -H "Authorization: Bearer $KEY" > "$WORK/events.json"
[[ "$(json '[e["sequence"] for e in d["items"]]' < "$WORK/events.json")" == "[1, 2, 3, 4, 5]" ]] || fail "event order"
cursor="$(json 'd["next_cursor"]' < "$WORK/events.json")"
page2="$(curl -sS "$API_URL/v1/runs/$RUN_ID/events?limit=5&cursor=$cursor" -H "Authorization: Bearer $KEY" | json '[e["sequence"] for e in d["items"]]')"
[[ "$page2" == "[6, 7, 8, 9, 10]" ]] || fail "page 2 order: $page2"
spans="$(curl -sS "$API_URL/v1/runs/$RUN_ID/spans" -H "Authorization: Bearer $KEY" | json 'len(d["items"])')"
[[ "$spans" == 3 ]] || fail "expected 3 spans, got $spans"
echo "events paged in order 1..10; $spans spans"

step "negative cases"
code() { curl -sS -o /dev/null -w '%{http_code}' "$@"; }
[[ "$(code "$API_URL/v1/runs")" == 401 ]] || fail "no key must be 401"
[[ "$(code "$API_URL/v1/runs" -H 'Authorization: Bearer abb_live_aaaaaaaaaaaa.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA')" == 401 ]] || fail "wrong key must be 401"
[[ "$(code "$API_URL/v1/runs/$RUN_ID" -H "Authorization: Bearer $OTHER_KEY")" == 404 ]] || fail "other workspace must get 404"
[[ "$(code -X POST "$API_URL/v1/events/batch" -H 'Content-Type: application/json' -d '{"events":[{}]}')" == 401 ]] || fail "ingest without key must be 401"
echo "no key -> 401, wrong key -> 401, other workspace -> 404"

echo
echo "SMOKE PASSED"
